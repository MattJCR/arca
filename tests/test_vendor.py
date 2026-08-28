import hashlib
import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca import vendor
from arca.db import open_library_rw
from arca.vendor import (
    DIRECT_DEPENDENCIES,
    LockEntry,
    VendorError,
    capture_llama_help,
    check_drift,
    default_lock_path,
    entry_dest,
    load_lock,
    vendor_download,
    verify_offline,
)
from tests.support import Behavior, DownloadServer

PAYLOAD = b"vendored documentation payload"


@pytest.fixture
def server() -> Iterator[DownloadServer]:
    instance = DownloadServer()
    instance.start()
    yield instance
    instance.stop()


def _entry(**overrides: str) -> LockEntry:
    values = {
        "key": "example",
        "version": "1.0.0",
        "kind": "docs",
        "url": "https://example.org/docs/example-1.0.0.zip",
        "commit": "",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "license": "MIT",
        "captured_at": "2026-08-28",
        "notes": "",
    }
    values.update(overrides)
    return LockEntry(**values)


def test_load_lock_reads_the_repository_lock() -> None:
    entries = load_lock(default_lock_path())
    keys = {entry.key for entry in entries}
    assert set(DIRECT_DEPENDENCIES) <= keys
    assert all(entry.sha256 for entry in entries)
    assert all(entry.license for entry in entries)


def test_load_lock_rejects_invalid_content(tmp_path: Path) -> None:
    target = tmp_path / "sources.lock"
    with pytest.raises(VendorError):
        load_lock(target)
    target.write_text("{not json", "utf-8")
    with pytest.raises(VendorError):
        load_lock(target)
    target.write_text(json.dumps({"key": "not-a-list"}), "utf-8")
    with pytest.raises(VendorError):
        load_lock(target)


def test_direct_dependencies_match_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))
    declared = {
        dependency.split(">=")[0].split("==")[0].strip().lower()
        for dependency in pyproject["project"]["dependencies"]
    }
    assert declared == set(DIRECT_DEPENDENCIES)


def test_entry_dest_maps_kinds_to_vendor_subdirectories(tmp_path: Path) -> None:
    pypi = entry_dest(tmp_path, _entry(kind="pypi", url="https://x.org/pkg-1.0.tar.gz"))
    assert pypi == tmp_path / "vendor" / "wheelhouse" / "pkg-1.0.tar.gz"
    git = entry_dest(tmp_path, _entry(kind="git", key="llama.cpp", url="https://x.org/b1.tar.gz"))
    assert git == tmp_path / "vendor" / "llama.cpp" / "b1.tar.gz"
    docs = entry_dest(tmp_path, _entry(kind="docs", key="python-docs"))
    assert docs == tmp_path / "vendor" / "docs" / "python-docs" / "example-1.0.0.zip"
    assert entry_dest(tmp_path, _entry(url="")) is None


def test_verify_offline_reports_each_state(tmp_path: Path) -> None:
    present = _entry(key="present")
    dest = entry_dest(tmp_path, present)
    assert dest is not None
    dest.parent.mkdir(parents=True)
    dest.write_bytes(PAYLOAD)
    mismatched = _entry(key="mismatched", sha256="0" * 64)
    bad_dest = entry_dest(tmp_path, mismatched)
    assert bad_dest is not None
    bad_dest.parent.mkdir(parents=True)
    bad_dest.write_bytes(PAYLOAD)
    entries = [
        present,
        mismatched,
        _entry(key="absent"),
        _entry(key="unhashed", sha256=""),
        _entry(key="unlinked", url=""),
    ]
    for entry in entries[3:4]:
        unhashed_dest = entry_dest(tmp_path, entry)
        assert unhashed_dest is not None
        unhashed_dest.parent.mkdir(parents=True)
        unhashed_dest.write_bytes(PAYLOAD)
    results = {status.key: status.result for status in verify_offline(tmp_path, entries)}
    assert results == {
        "present": "ok",
        "mismatched": "mismatch",
        "absent": "missing",
        "unhashed": "no_hash",
        "unlinked": "no_url",
    }


def test_check_drift_against_the_real_lock_is_clean() -> None:
    results = {
        status.package: status.result for status in check_drift(load_lock(default_lock_path()))
    }
    assert results == dict.fromkeys(DIRECT_DEPENDENCIES, "ok")


def test_check_drift_detects_drift_and_missing_documentation() -> None:
    entries = [
        _entry(key="fastapi", kind="pypi", version="0.1.0"),
        _entry(key="uvicorn", kind="pypi", version="0.0.1"),
    ]
    results = {status.package: status.result for status in check_drift(entries)}
    assert results["fastapi"] == "drift"
    assert results["uvicorn"] == "drift"
    assert results["numpy"] == "undocumented"


def test_check_drift_reports_documented_but_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vendor, "_installed_version", lambda package: "")
    entries = [_entry(key="numpy", kind="pypi", version="2.5.2")]
    results = {status.package: status.result for status in check_drift(entries)}
    assert results["numpy"] == "not_installed"


def test_vendor_download_fetches_verifies_and_skips_existing(
    server: DownloadServer, tmp_path: Path
) -> None:
    behavior = server.add("/docs/example-1.0.0.zip", Behavior(payload=PAYLOAD))
    entry = _entry(url=server.url("/docs/example-1.0.0.zip"))
    db_path = tmp_path / "library.db"
    connection = open_library_rw(db_path)
    try:
        results = vendor_download(
            db_path, connection, tmp_path, [entry], concurrency=1, max_attempts=2
        )
        assert [result.status for result in results] == ["done"]
        dest = entry_dest(tmp_path, entry)
        assert dest is not None and dest.read_bytes() == PAYLOAD
        assert dest.with_name(dest.name + ".sha256").exists()
        again = vendor_download(
            db_path, connection, tmp_path, [entry], concurrency=1, max_attempts=2
        )
        assert again == []
        assert len(behavior.requests) == 1
    finally:
        connection.close()


def test_capture_llama_help_degrades_when_the_binary_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.vendor.shutil.which", lambda name: None)
    assert capture_llama_help(tmp_path) is None


def test_capture_llama_help_writes_the_help_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.vendor.shutil.which", lambda name: sys.executable)
    target = capture_llama_help(tmp_path)
    assert target is not None
    assert target.name == "llama-server-help.txt"
    assert target.read_text("utf-8")
    assert target.with_name(target.name + ".sha256").exists()


def test_capture_llama_help_also_records_the_compiled_version(tmp_path: Path) -> None:
    target = capture_llama_help(tmp_path, sys.executable)
    assert target is not None
    version = target.with_name("llama-server-version.txt")
    assert version.read_text("utf-8").strip()
    assert version.with_name(version.name + ".sha256").is_file()
