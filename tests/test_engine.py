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
    STATE_UNSUPPORTED,
    STATE_WAITING_MODEL,
    Asset,
    EngineBringup,
    EngineError,
    EngineUnavailableError,
    backend_of,
    candidates,
    chat_endpoint,
    companion_of,
    default_profile,
    extract_archive,
    is_server_package,
    list_releases,
    locate_binary,
    matches_platform,
    pick_release,
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
    embedding_command,
    mark_last_good,
    read_model,
    save_profile,
)
from arca.vendor import default_lock_path, load_lock
from tests.support import (
    ReleaseServer,
    engine_package,
    engine_tarball,
    free_port,
    runtime_package,
    stub_command,
    write_gguf,
)
from tests.test_api import http_get, http_request

RELEASE_ASSETS = (
    "cudart-llama-bin-win-cuda-12.4-x64.zip",
    "cudart-llama-bin-win-cuda-13.3-x64.zip",
    "llama-b9999-bin-android-arm64.tar.gz",
    "llama-b9999-bin-macos-arm64.tar.gz",
    "llama-b9999-bin-ubuntu-s390x.tar.gz",
    "llama-b9999-bin-ubuntu-vulkan-x64.tar.gz",
    "llama-b9999-bin-ubuntu-x64.tar.gz",
    "llama-b9999-bin-win-cpu-arm64.zip",
    "llama-b9999-bin-win-cpu-x64.zip",
    "llama-b9999-bin-win-cuda-12.4-x64.zip",
    "llama-b9999-bin-win-openvino-2026.3.1-x64.zip",
    "llama-b9999-bin-win-vulkan-x64.zip",
    "llama-b9999-ui.tar.gz",
    "llama-b9999-xcframework.zip",
)
WINDOWS_ORDER_WITH_NVIDIA = [
    "llama-b9999-bin-win-cuda-12.4-x64.zip",
    "llama-b9999-bin-win-vulkan-x64.zip",
    "llama-b9999-bin-win-cpu-x64.zip",
]
WINDOWS_ORDER_WITHOUT_NVIDIA = [
    "llama-b9999-bin-win-vulkan-x64.zip",
    "llama-b9999-bin-win-cuda-12.4-x64.zip",
    "llama-b9999-bin-win-cpu-x64.zip",
]


@pytest.fixture
def releases() -> Iterator[ReleaseServer]:
    server = ReleaseServer()
    for name in RELEASE_ASSETS:
        if name.startswith("cudart"):
            server.add(name, runtime_package())
        elif name.endswith(".zip"):
            server.add(name, engine_package())
        else:
            server.add(name, engine_tarball())
    server.start()
    yield server
    server.stop()


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("arca.engine.platform.system", lambda: "Windows")
    monkeypatch.setattr("arca.engine.platform.machine", lambda: "AMD64")


def _assets(*names: str) -> tuple[Asset, ...]:
    return tuple(
        Asset(name=name, url=f"http://x/{name}", size_bytes=10, sha256="") for name in names
    )


def test_a_release_without_a_package_for_this_platform_is_skipped(
    releases: ReleaseServer,
) -> None:
    found = list_releases(releases.url, releases.repo)
    assert [item.tag for item in found] == [releases.decoy_tag, releases.tag]
    chosen = pick_release(found, "windows", "amd64")
    assert chosen is not None
    assert chosen.tag == releases.tag


def test_the_release_is_resolved_at_the_moment_with_its_assets(releases: ReleaseServer) -> None:
    release = resolve_release(releases.url, releases.repo, "windows", "amd64")
    assert release is not None
    assert release.tag == "b9999"
    assert {asset.name for asset in release.assets} == set(RELEASE_ASSETS)
    for asset in release.assets:
        assert asset.url.startswith(releases.url)
        assert len(asset.sha256) == 64


def test_the_commit_of_the_release_is_pinned_from_the_feed(releases: ReleaseServer) -> None:
    release = resolve_release(releases.url, releases.repo, "windows", "amd64")
    assert release is not None
    assert release.commit == releases.commit


def test_a_branch_name_is_never_taken_for_a_commit(releases: ReleaseServer) -> None:
    releases.commitish = "master"
    release = resolve_release(releases.url, releases.repo, "windows", "amd64")
    assert release is not None
    assert release.commit == ""
    assert resolve_commit(release.tag, releases.url, releases.repo) == releases.commit


def test_an_unresolvable_tag_degrades_to_no_commit(releases: ReleaseServer) -> None:
    releases.tag_reachable = False
    assert resolve_commit("b9999", releases.url, releases.repo) == ""


def test_an_unreachable_release_feed_is_reported_not_raised_raw() -> None:
    with pytest.raises(EngineUnavailableError):
        list_releases("http://127.0.0.1:1", "ggml-org/llama.cpp")


@pytest.mark.parametrize(
    ("name", "backend", "gpu"),
    [
        ("llama-b1-bin-win-cuda-12.4-x64.zip", "cuda", True),
        ("llama-b1-bin-win-vulkan-x64.zip", "vulkan", True),
        ("llama-b1-bin-win-rocm-7.14-x64.zip", "rocm", True),
        ("llama-b1-bin-win-sycl-x64.zip", "sycl", True),
        ("llama-b1-bin-win-cpu-x64.zip", "cpu", False),
        ("llama-b1-bin-ubuntu-x64.tar.gz", "cpu", False),
    ],
)
def test_the_backend_is_read_from_the_asset_name(name: str, backend: str, gpu: bool) -> None:
    assert backend_of(name) == backend
    assert uses_gpu(name) is gpu


@pytest.mark.parametrize(
    "name",
    [
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "llama-b9999-bin-win-openvino-2026.3.1-x64.zip",
        "llama-b9999-bin-win-opencl-adreno-arm64.zip",
        "llama-b9999-xcframework.zip",
        "llama-b9999-bin-ubuntu-s390x.tar.gz",
    ],
)
def test_packages_that_carry_no_server_are_never_candidates(name: str) -> None:
    assert not is_server_package(name)


def test_candidates_put_the_gpu_first_and_the_cpu_last() -> None:
    picks = candidates(_assets(*RELEASE_ASSETS), "windows", "amd64", nvidia=True)
    assert [asset.name for asset in picks] == WINDOWS_ORDER_WITH_NVIDIA


def test_without_an_nvidia_driver_the_self_contained_gpu_package_goes_first() -> None:
    picks = candidates(_assets(*RELEASE_ASSETS), "windows", "amd64", nvidia=False)
    assert [asset.name for asset in picks] == WINDOWS_ORDER_WITHOUT_NVIDIA


def test_candidates_never_cross_platforms_or_architectures() -> None:
    assets = _assets(*RELEASE_ASSETS)
    linux = candidates(assets, "linux", "x86_64", nvidia=False)
    mac = candidates(assets, "darwin", "arm64", nvidia=False)
    windows_arm = candidates(assets, "windows", "arm64", nvidia=False)
    assert [asset.name for asset in linux] == [
        "llama-b9999-bin-ubuntu-vulkan-x64.tar.gz",
        "llama-b9999-bin-ubuntu-x64.tar.gz",
    ]
    assert [asset.name for asset in mac] == ["llama-b9999-bin-macos-arm64.tar.gz"]
    assert [asset.name for asset in windows_arm] == ["llama-b9999-bin-win-cpu-arm64.zip"]


def test_an_unknown_platform_selects_nothing() -> None:
    assert candidates(_assets(*RELEASE_ASSETS), "haiku", "amd64") == ()
    assert not matches_platform("llama-b1-bin-win-cpu-x64.zip", "linux", "x86_64")


def test_the_cuda_runtime_travels_with_its_own_cuda_package() -> None:
    assets = _assets(*RELEASE_ASSETS)
    cuda = next(asset for asset in assets if asset.name == "llama-b9999-bin-win-cuda-12.4-x64.zip")
    vulkan = next(asset for asset in assets if "vulkan" in asset.name)
    companion = companion_of(cuda, assets)
    assert companion is not None
    assert companion.name == "cudart-llama-bin-win-cuda-12.4-x64.zip"
    assert companion_of(vulkan, assets) is None


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


def test_a_zip_package_is_unpacked_and_its_binary_located(tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(engine_package())
    target = tmp_path / "dest"
    extract_archive(archive, target)
    binary = locate_binary(target)
    assert binary is not None
    assert binary.name == "llama-server.exe"
    assert (target / "build" / "bin" / "ggml.dll").is_file()


def test_a_tarball_package_is_unpacked_the_same_way(tmp_path: Path) -> None:
    archive = tmp_path / "package.tar.gz"
    archive.write_bytes(engine_tarball())
    target = tmp_path / "dest"
    extract_archive(archive, target)
    binary = locate_binary(target)
    assert binary is not None
    assert binary.name == "llama-server"
    assert (target / "build" / "bin" / "libggml.so").is_file()


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


def _bringup(
    root: Path,
    config: ArcaConfig,
    api_url: str = "http://127.0.0.1:1",
    nvidia: bool = False,
) -> EngineBringup:
    worker = QueueWorker(library_db_path(root), concurrency=1, max_attempts=1)
    return EngineBringup(
        root,
        config,
        LlamaServer(default_url=config.chat_url),
        worker,
        api_url=api_url,
        download_timeout=30.0,
        nvidia=nvidia,
    )


def _accept(marker: str = "") -> object:
    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        ok = marker in str(binary)
        trial = TrialResult(ok, "ok" if ok else "start", (str(binary),), error="" if ok else "no")
        return ApplyResult(ok, trial, profile.name if ok else "", "", ok)

    return fake_apply


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


def test_a_release_with_nothing_for_this_platform_is_declared_unsupported(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    monkeypatch.setattr("arca.engine.platform.system", lambda: "Haiku")
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_UNSUPPORTED


def test_the_bringup_tries_the_gpu_package_first_and_falls_back_to_the_cpu_one(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)
    attempts: list[str] = []

    def fake_apply(connection, server, binary, profile, **kwargs):  # type: ignore[no-untyped-def]
        attempts.append(Path(str(binary)).parent.parent.parent.name)
        ok = "cpu" in str(binary)
        trial = TrialResult(ok, "ok" if ok else "start", (str(binary),), error="" if ok else "no")
        return ApplyResult(ok, trial, profile.name if ok else "", "", ok)

    monkeypatch.setattr("arca.engine.apply_profile", fake_apply)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    status = _bringup(root, ArcaConfig(), api_url=releases.url, nvidia=True).run()
    assert status.state == STATE_READY
    assert attempts == ["b9999-cuda", "b9999-vulkan", "b9999-cpu"]
    assert not (root / "vendor" / "llama.cpp" / "b9999-cuda").exists()
    assert not (root / "vendor" / "llama.cpp" / "b9999-vulkan").exists()
    assert (root / "vendor" / "llama.cpp" / "b9999-cpu").is_dir()
    assert status.profile == "auto"


def test_the_cuda_runtime_is_deployed_next_to_the_cuda_engine(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.apply_profile", _accept("cuda"))
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    status = _bringup(root, ArcaConfig(), api_url=releases.url, nvidia=True).run()
    assert status.state == STATE_READY
    deployed = root / "vendor" / "llama.cpp" / "b9999-cuda"
    assert (deployed / "build" / "bin" / "llama-server.exe").is_file()
    assert (deployed / "cudart64_12.dll").is_file()


def test_every_downloaded_package_is_verified_and_kept_for_offline_reuse(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.apply_profile", _accept())
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_READY
    archive = root / "vendor" / "llama.cpp" / "llama-b9999-bin-win-vulkan-x64.zip"
    assert archive.is_file()
    assert archive.with_suffix(archive.suffix + ".sha256").is_file()
    connection = open_library_ro(library_db_path(root))
    try:
        rows = connection.execute("SELECT status FROM downloads WHERE kind = 'engine'").fetchall()
    finally:
        connection.close()
    assert [str(row["status"]) for row in rows] == ["done"]


def test_a_successful_bringup_pins_the_commit_in_the_lock(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    lock = _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    monkeypatch.setattr("arca.engine.apply_profile", _accept())
    _bringup(root, ArcaConfig(), api_url=releases.url).run()
    entries = {entry.key: entry for entry in load_lock(lock)}
    assert entries["llama.cpp"].commit == releases.commit
    assert entries["llama.cpp"].license == "MIT"


def test_when_no_package_works_the_bringup_says_it_exhausted_them(
    tmp_path: Path, releases: ReleaseServer, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "model.gguf")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.apply_profile", _accept("never"))
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
    monkeypatch.setattr("arca.engine.apply_profile", _accept())
    status = _bringup(root, ArcaConfig(), api_url=releases.url).run()
    assert status.state == STATE_READY
    assert releases.requests == []


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


def _embed_bringup(
    root: Path,
    config: ArcaConfig,
    embed_server: LlamaServer,
    api_url: str = "http://127.0.0.1:1",
) -> EngineBringup:
    worker = QueueWorker(library_db_path(root), concurrency=1, max_attempts=1)
    return EngineBringup(
        root,
        config,
        LlamaServer(default_url=config.chat_url),
        worker,
        api_url=api_url,
        download_timeout=30.0,
        nvidia=False,
        embed_server=embed_server,
        health_timeout=20.0,
    )


def test_the_embedding_command_asks_for_embeddings_and_nothing_else(tmp_path: Path) -> None:
    model = tmp_path / "e5.gguf"
    write_gguf(model)
    command = embedding_command(Path("llama-server"), read_model(model, "embed"), "http://h:9999")
    assert "--embeddings" in command
    assert command[command.index("--pooling") + 1] == "mean"
    assert command[command.index("--model") + 1] == str(model)
    assert command[command.index("--host") + 1] == "h"
    assert command[command.index("--port") + 1] == "9999"


def _with_stub_embeddings(monkeypatch: pytest.MonkeyPatch, port: int, *extra: str) -> None:
    monkeypatch.setattr(
        "arca.engine.embedding_command",
        lambda binary, model, base_url: stub_command(port, *extra),
    )


def test_the_embedding_server_is_started_and_proved_by_its_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "embed" / "e5.gguf")
    binary = root / "vendor" / "llama.cpp" / "llama-server.exe"
    binary.write_text("binary", "utf-8")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    port = free_port()
    _with_stub_embeddings(monkeypatch, port, "--stub-dims", "8")
    server = LlamaServer(default_url=f"http://127.0.0.1:{port}")
    config = ArcaConfig(embed_url=f"http://127.0.0.1:{port}", embed_dims=8)
    try:
        status = _embed_bringup(root, config, server).run()
    finally:
        server.stop()
    assert status.embed_state == STATE_READY
    assert status.embed_model == "e5.gguf"


def test_an_embedding_server_with_other_dimensions_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "embed" / "e5.gguf")
    binary = root / "vendor" / "llama.cpp" / "llama-server.exe"
    binary.write_text("binary", "utf-8")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    port = free_port()
    _with_stub_embeddings(monkeypatch, port, "--stub-dims", "8")
    server = LlamaServer(default_url=f"http://127.0.0.1:{port}")
    config = ArcaConfig(embed_url=f"http://127.0.0.1:{port}", embed_dims=768)
    try:
        status = _embed_bringup(root, config, server).run()
    finally:
        server.stop()
    assert status.embed_state == "failed"
    assert "768" in status.embed_detail


def test_with_no_model_at_all_the_bringup_waits_for_either(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    server = LlamaServer(default_url="http://127.0.0.1:1")
    status = _embed_bringup(root, ArcaConfig(), server).run()
    assert status.state == STATE_WAITING_MODEL


def test_a_chat_model_alone_leaves_the_embedding_side_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "chat.gguf")
    binary = root / "vendor" / "llama.cpp" / "llama-server.exe"
    binary.write_text("binary", "utf-8")
    _isolate_lock(root, monkeypatch)
    monkeypatch.setattr("arca.engine.capture_llama_help", lambda root, binary: None)
    monkeypatch.setattr("arca.engine.apply_profile", _accept())
    server = LlamaServer(default_url="http://127.0.0.1:1")
    status = _embed_bringup(root, ArcaConfig(), server).run()
    assert status.state == STATE_READY
    assert status.embed_state == STATE_WAITING_MODEL
