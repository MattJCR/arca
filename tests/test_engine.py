from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_ro, open_library_rw
from arca.engine import (
    STATE_EXHAUSTED,
    STATE_IDLE,
    STATE_NO_NETWORK,
    STATE_READY,
    STATE_WAITING_MODEL,
    Asset,
    EngineBringup,
    EngineError,
    EngineUnavailableError,
    backend_of,
    candidates,
    chat_endpoint,
    default_profile,
    extract_archive,
    locate_binary,
    matches_platform,
    resolve_commit,
    resolve_release,
    uses_gpu,
)
from arca.fetch import QueueWorker
from arca.llama import (
    ApplyResult,
    BinaryInfo,
    LlamaServer,
    Profile,
    TrialResult,
    mark_last_good,
    read_model,
    save_profile,
)
from arca.vendor import default_lock_path, load_lock
from tests.support import ReleaseServer, engine_package, write_gguf
from tests.test_api import http_get, http_request

WINDOWS_ASSETS = (
    "llama-b9999-bin-win-cpu-x64.zip",
    "llama-b9999-bin-win-cuda-12.4-x64.zip",
    "llama-b9999-bin-win-vulkan-x64.zip",
    "llama-b9999-bin-win-arm64.zip",
    "llama-b9999-bin-ubuntu-x64.zip",
    "llama-b9999-bin-macos-arm64.zip",
    "llama-b9999-src.tar.gz",
)


@pytest.fixture
def releases() -> Iterator[ReleaseServer]:
    server = ReleaseServer()
    for name in WINDOWS_ASSETS:
        server.add(name, engine_package())
    server.start()
    yield server
    server.stop()


def _assets(*names: str) -> tuple[Asset, ...]:
    return tuple(
        Asset(name=name, url=f"http://x/{name}", size_bytes=10, sha256="") for name in names
    )


def test_the_release_is_resolved_at_the_moment_with_its_assets(releases: ReleaseServer) -> None:
    release = resolve_release(releases.url, releases.repo)
    assert release.tag == "b9999"
    assert {asset.name for asset in release.assets} == set(WINDOWS_ASSETS)
    for asset in release.assets:
        assert asset.url.startswith(releases.url)
        assert len(asset.sha256) == 64


def test_a_branch_name_is_never_taken_for_a_commit(releases: ReleaseServer) -> None:
    release = resolve_release(releases.url, releases.repo)
    assert release.commit == ""
    assert resolve_commit(release.tag, releases.url, releases.repo) == releases.commit


def test_an_unresolvable_tag_degrades_to_no_commit(releases: ReleaseServer) -> None:
    releases.tag_reachable = False
    assert resolve_commit("b9999", releases.url, releases.repo) == ""


def test_an_unreachable_release_feed_is_reported_not_raised_raw() -> None:
    with pytest.raises(EngineUnavailableError):
        resolve_release("http://127.0.0.1:1", "ggml-org/llama.cpp")


@pytest.mark.parametrize(
    ("name", "backend", "gpu"),
    [
        ("llama-b1-bin-win-cuda-12.4-x64.zip", "cuda", True),
        ("llama-b1-bin-win-vulkan-x64.zip", "vulkan", True),
        ("llama-b1-bin-win-hip-x64.zip", "hip", True),
        ("llama-b1-bin-win-cpu-x64.zip", "cpu", False),
        ("llama-b1-bin-ubuntu-x64.zip", "cpu", False),
    ],
)
def test_the_backend_is_read_from_the_asset_name(name: str, backend: str, gpu: bool) -> None:
    assert backend_of(name) == backend
    assert uses_gpu(name) is gpu


def test_candidates_put_the_gpu_first_and_the_cpu_last() -> None:
    picks = candidates(_assets(*WINDOWS_ASSETS), "windows", "amd64")
    assert [asset.name for asset in picks] == [
        "llama-b9999-bin-win-cuda-12.4-x64.zip",
        "llama-b9999-bin-win-vulkan-x64.zip",
        "llama-b9999-bin-win-cpu-x64.zip",
    ]


def test_candidates_never_cross_platforms_or_architectures() -> None:
    linux = candidates(_assets(*WINDOWS_ASSETS), "linux", "x86_64")
    mac = candidates(_assets(*WINDOWS_ASSETS), "darwin", "arm64")
    windows_arm = candidates(_assets(*WINDOWS_ASSETS), "windows", "arm64")
    assert [asset.name for asset in linux] == ["llama-b9999-bin-ubuntu-x64.zip"]
    assert [asset.name for asset in mac] == ["llama-b9999-bin-macos-arm64.zip"]
    assert [asset.name for asset in windows_arm] == ["llama-b9999-bin-win-arm64.zip"]


def test_an_unknown_platform_selects_nothing() -> None:
    assert candidates(_assets(*WINDOWS_ASSETS), "haiku", "amd64") == ()
    assert not matches_platform("llama-b1-bin-win-cpu-x64.zip", "linux", "x86_64")


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "/absolute.txt", "C:/drive.txt", "build/../../escape.txt"],
)
def test_an_archive_that_escapes_its_destination_is_refused(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "evil.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(member, "payload")
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(EngineError):
        extract_archive(archive, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_a_package_is_unpacked_and_its_binary_located(tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(engine_package())
    target = tmp_path / "dest"
    extract_archive(archive, target)
    binary = locate_binary(target)
    assert binary is not None
    assert binary.name == "llama-server.exe"
    assert (target / "build" / "bin" / "ggml.dll").is_file()


def test_the_default_profile_carries_the_decisions_of_section_two(tmp_path: Path) -> None:
    model = tmp_path / "chat.gguf"
    write_gguf(model)
    profile = default_profile(read_model(model, "chat"), "http://127.0.0.1:8099", gpu=True)
    assert profile.args["--ctx-size"] == "8192"
    assert profile.args["--cache-type-k"] == "q8_0"
    assert profile.args["--cache-type-v"] == "q8_0"
    assert profile.args["--model"] == str(model)
    assert profile.args["--host"] == "127.0.0.1"
    assert profile.args["--port"] == "8099"
    assert profile.args["--n-gpu-layers"] == "999"


def test_a_cpu_profile_asks_for_no_gpu_layers(tmp_path: Path) -> None:
    model = tmp_path / "chat.gguf"
    write_gguf(model)
    profile = default_profile(read_model(model, "chat"), "http://127.0.0.1:8081", gpu=False)
    assert "--n-gpu-layers" not in profile.args


def test_the_chat_endpoint_falls_back_when_the_url_says_nothing() -> None:
    assert chat_endpoint("http://127.0.0.1:8081") == ("127.0.0.1", "8081")
    assert chat_endpoint("") == ("127.0.0.1", "8081")


def _isolate_lock(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lock = root.parent / "sources.lock"
    lock.write_text(default_lock_path().read_text("utf-8"), "utf-8")
    monkeypatch.setattr("arca.engine.default_lock_path", lambda: lock)
    return lock


def _bringup(root: Path, config: ArcaConfig, api_url: str = "http://127.0.0.1:1") -> EngineBringup:
    worker = QueueWorker(library_db_path(root), concurrency=1, max_attempts=1)
    return EngineBringup(
        root,
        config,
        LlamaServer(default_url=config.chat_url),
        worker,
        api_url=api_url,
        download_timeout=30.0,
    )


def test_without_a_chat_model_the_bringup_waits_and_touches_no_network(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    status = _bringup(root, ArcaConfig()).run()
    assert status.state == STATE_WAITING_MODEL


def test_without_a_network_the_bringup_says_so_instead_of_failing(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    status = _bringup(root, ArcaConfig()).run()
    assert status.state == STATE_NO_NETWORK
    assert status.detail


def test_the_bringup_tries_the_gpu_package_first_and_falls_back_to_the_cpu_one(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)
    attempts: list[str] = []

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        attempts.append(str(binary))
        ok = "cpu" in str(binary)
        trial = TrialResult(ok, "ok" if ok else "start", (str(binary),), error="" if ok else "boom")
        return ApplyResult(ok, trial, profile.name if ok else "", "", ok)

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    bringup = _bringup(root, ArcaConfig(), api_url=releases.url)
    status = bringup.run()
    assert status.state == STATE_READY
    assert [Path(item).parent.parent.parent.name for item in attempts] == [
        "b9999-cuda",
        "b9999-vulkan",
        "b9999-cpu",
    ]
    assert not (root / "vendor" / "llama.cpp" / "b9999-cuda").exists()
    assert not (root / "vendor" / "llama.cpp" / "b9999-vulkan").exists()
    assert (root / "vendor" / "llama.cpp" / "b9999-cpu").is_dir()
    assert status.profile == "auto"


def test_every_downloaded_package_is_verified_and_kept_for_offline_reuse(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        return ApplyResult(True, TrialResult(True, "ok", (str(binary),)), profile.name, "", True)

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_READY
    archive = root / "vendor" / "llama.cpp" / "llama-b9999-bin-win-cuda-12.4-x64.zip"
    assert archive.is_file()
    assert archive.with_suffix(archive.suffix + ".sha256").is_file()
    connection = open_library_ro(library_db_path(root))
    try:
        rows = connection.execute(
            "SELECT kind, status FROM downloads WHERE kind = 'engine'"
        ).fetchall()
    finally:
        connection.close()
    assert [str(row["status"]) for row in rows] == ["done"]


def test_a_successful_bringup_pins_the_commit_in_the_lock(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    lock = _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        return ApplyResult(True, TrialResult(True, "ok", (str(binary),)), profile.name, "", True)

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    _bringup(root, ArcaConfig(), api_url=releases.url).run()
    entries = {entry.key: entry for entry in load_lock(lock)}
    assert entries["llama.cpp"].commit == releases.commit
    assert entries["llama.cpp"].license == "MIT"


def test_when_no_package_works_the_bringup_says_it_exhausted_them(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        return ApplyResult(
            False, TrialResult(False, "start", (str(binary),), error="no"), "", "", False
        )

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_EXHAUSTED
    assert len(status.tried) == 3
    assert not list((root / "vendor" / "llama.cpp").glob("b9999-*"))


def test_an_existing_binary_is_used_without_asking_the_network(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    existing = root / "vendor" / "llama.cpp" / "llama-server.exe"
    existing.write_text("binary", "utf-8")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    monkeypatch.setattr("arca.engine.inspect_binary", lambda root, configured: BinaryInfo(existing))

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        return ApplyResult(True, TrialResult(True, "ok", (str(binary),)), profile.name, "", True)

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_READY
    assert releases.requests == []


@pytest.fixture
def library(tmp_path: Path) -> Iterator[tuple[Path, FastAPI]]:
    root = tmp_path / "library"
    init_library(root)
    yield root, create_app(root, ArcaConfig())


def test_creating_the_application_starts_no_bringup(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, body = http_get(app, "/api/admin/engine")
    assert status == 200
    payload = json.loads(body)
    assert payload["state"] == STATE_IDLE
    assert payload["running"] is False


def test_the_panel_can_ask_for_the_bringup_and_it_never_blocks(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    status, body = http_request(app, "POST", "/api/admin/engine/start")
    assert status == 200
    assert json.loads(body)["state"] in (STATE_IDLE, STATE_WAITING_MODEL)


def test_a_settled_chat_is_left_exactly_as_the_operator_left_it(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    existing = root / "vendor" / "llama.cpp" / "llama-server.exe"
    existing.write_text("binary", "utf-8")
    _isolate_lock(root, monkeypatch)
    connection = open_library_rw(library_db_path(root))
    try:
        save_profile(connection, Profile(name="mine", mode="raw", raw_cmd="--model mine.gguf"))
        mark_last_good(connection, "mine")
    finally:
        connection.close()
    calls: list[str] = []
    monkeypatch.setattr(
        "arca.engine.apply_profile",
        lambda *args, **kwargs: calls.append("applied"),
    )
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_READY
    assert status.profile == "mine"
    assert calls == []
    assert releases.requests == []
    connection = open_library_ro(library_db_path(root))
    try:
        names = [str(row["name"]) for row in connection.execute("SELECT name FROM llama_profiles")]
    finally:
        connection.close()
    assert names == ["mine"]
