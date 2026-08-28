from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import urllib.parse
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from arca.db import utc_now_iso
from arca.fetch import (
    ProgressCallback,
    QueueResult,
    enqueue,
    file_sha256,
    run_queue,
    write_sha256_sidecar,
)
from arca.llama import find_binary

LOCK_FILENAME = "sources.lock"
DIRECT_DEPENDENCIES: tuple[str, ...] = ("fastapi", "uvicorn", "libzim", "pymupdf", "numpy")
LLAMA_BINARY = "llama-server"
LLAMA_HELP_FILENAME = "llama-server-help.txt"
LLAMA_VERSION_FILENAME = "llama-server-version.txt"
LLAMA_KEY = "llama.cpp"
LLAMA_TARBALL = "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/"
LLAMA_LICENSE = "MIT"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
HELP_TIMEOUT_SECONDS = 30


class VendorError(Exception):
    pass


@dataclass(frozen=True)
class LockEntry:
    key: str
    version: str
    kind: str
    url: str
    commit: str
    sha256: str
    license: str
    captured_at: str
    notes: str


@dataclass(frozen=True)
class VendorStatus:
    key: str
    filename: str
    result: str


@dataclass(frozen=True)
class DriftStatus:
    package: str
    installed: str
    documented: str
    result: str


def default_lock_path() -> Path:
    return Path(__file__).resolve().parents[1] / LOCK_FILENAME


def load_lock(path: Path) -> list[LockEntry]:
    try:
        parsed: object = json.loads(path.read_text("utf-8"))
    except OSError as error:
        raise VendorError(f"cannot read {path}: {error}") from error
    except ValueError as error:
        raise VendorError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(parsed, list):
        raise VendorError(f"{path} must contain a JSON array of entries")
    entries = []
    for item in parsed:
        if not isinstance(item, dict):
            raise VendorError(f"{path} contains a non-object entry")
        entries.append(
            LockEntry(
                key=_field(item, "key"),
                version=_field(item, "version"),
                kind=_field(item, "kind"),
                url=_field(item, "url"),
                commit=_field(item, "commit"),
                sha256=_field(item, "sha256"),
                license=_field(item, "license"),
                captured_at=_field(item, "captured_at"),
                notes=_field(item, "notes"),
            )
        )
    return entries


def entry_filename(entry: LockEntry) -> str:
    if not entry.url:
        return ""
    path = urllib.parse.urlsplit(entry.url).path
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


def entry_dest(root: Path, entry: LockEntry) -> Path | None:
    filename = entry_filename(entry)
    if not filename:
        return None
    if entry.kind == "pypi":
        return root / "vendor" / "wheelhouse" / filename
    if entry.kind == "git":
        return root / "vendor" / entry.key / filename
    return root / "vendor" / "docs" / entry.key / filename


def enqueue_vendor_downloads(
    connection: sqlite3.Connection, root: Path, entries: list[LockEntry]
) -> int:
    active_rows = connection.execute(
        "SELECT ref FROM downloads WHERE kind = 'vendor' AND status IN "
        "('queued', 'running', 'paused', 'verifying')"
    ).fetchall()
    active = {str(row["ref"]) for row in active_rows}
    queued = 0
    for entry in entries:
        if entry.key in active:
            continue
        dest = entry_dest(root, entry)
        if dest is None:
            continue
        if dest.is_file() and (not entry.sha256 or file_sha256(dest) == entry.sha256):
            continue
        enqueue(
            connection,
            kind="vendor",
            ref=entry.key,
            filename=dest.name,
            dest_path=dest,
            size_bytes=None,
            sha256=entry.sha256,
            mirrors=(entry.url,),
        )
        queued += 1
    return queued


def vendor_download(
    db_path: Path,
    connection: sqlite3.Connection,
    root: Path,
    entries: list[LockEntry],
    *,
    concurrency: int,
    max_attempts: int,
    progress: ProgressCallback | None = None,
) -> list[QueueResult]:
    if enqueue_vendor_downloads(connection, root, entries) == 0:
        return []
    return run_queue(db_path, concurrency=concurrency, max_attempts=max_attempts, progress=progress)


def vendor_sources(root: Path, entries: list[LockEntry]) -> list[VendorSourceRow]:
    found: list[VendorSourceRow] = []
    for entry in entries:
        destination = entry_dest(root, entry)
        if destination is None or not destination.is_file():
            continue
        found.append(
            VendorSourceRow(
                doc_id=entry.sha256 or file_sha256(destination),
                name=destination.name,
                title=f"{entry.key} {entry.version}",
                version=entry.version,
                license=entry.license,
                path=destination,
                relative_path=str(destination.relative_to(root)).replace("\\", "/"),
            )
        )
    return found


@dataclass(frozen=True)
class VendorSourceRow:
    doc_id: str
    name: str
    title: str
    version: str
    license: str
    path: Path
    relative_path: str


def verify_offline(root: Path, entries: list[LockEntry]) -> list[VendorStatus]:
    statuses = []
    for entry in entries:
        dest = entry_dest(root, entry)
        if dest is None:
            statuses.append(VendorStatus(entry.key, "", "no_url"))
            continue
        if not dest.is_file():
            statuses.append(VendorStatus(entry.key, dest.name, "missing"))
            continue
        if not entry.sha256:
            statuses.append(VendorStatus(entry.key, dest.name, "no_hash"))
            continue
        result = "ok" if file_sha256(dest) == entry.sha256 else "mismatch"
        statuses.append(VendorStatus(entry.key, dest.name, result))
    return statuses


def check_drift(entries: list[LockEntry]) -> list[DriftStatus]:
    documented = {entry.key.lower(): entry for entry in entries if entry.kind == "pypi"}
    statuses = []
    for package in DIRECT_DEPENDENCIES:
        installed = _installed_version(package)
        entry = documented.get(package)
        if entry is None:
            statuses.append(DriftStatus(package, installed, "", "undocumented"))
        elif not installed:
            statuses.append(DriftStatus(package, "", entry.version, "not_installed"))
        elif installed == entry.version:
            statuses.append(DriftStatus(package, installed, entry.version, "ok"))
        else:
            statuses.append(DriftStatus(package, installed, entry.version, "drift"))
    return statuses


def capture_llama_help(root: Path, binary: str = "") -> Path | None:
    resolved = _llama_binary(root, binary)
    if resolved is None:
        return None
    help_path = _capture(root, resolved, ["--help"], LLAMA_HELP_FILENAME)
    _capture(root, resolved, ["--version"], LLAMA_VERSION_FILENAME)
    return help_path


def _llama_binary(root: Path, binary: str) -> str | None:
    if binary:
        found = shutil.which(binary)
        return found if found else (binary if Path(binary).is_file() else None)
    path, _ = find_binary(root)
    return str(path) if path is not None else None


def _capture(root: Path, binary: str, flags: list[str], filename: str) -> Path | None:
    try:
        completed = subprocess.run(
            [binary, *flags],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=HELP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    body = completed.stdout + completed.stderr
    if not body.strip():
        return None
    target = root / "vendor" / "llama.cpp" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, "utf-8")
    write_sha256_sidecar(target, file_sha256(target))
    return target


def _installed_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return ""


def _field(item: dict[str, object], key: str) -> str:
    value = item.get(key, "")
    if isinstance(value, str):
        return value
    return str(value)


def is_commit(value: str) -> bool:
    return bool(COMMIT_PATTERN.match(value.strip().lower()))


def llama_lock_entry(commit: str, now: str = "") -> LockEntry:
    reference = commit.strip().lower()
    return LockEntry(
        key=LLAMA_KEY,
        version=reference[:7],
        kind="git",
        url=f"{LLAMA_TARBALL}{reference}",
        commit=reference,
        sha256="",
        license=LLAMA_LICENSE,
        captured_at=now or utc_now_iso(),
        notes="documentation and sources of the compiled llama.cpp commit",
    )


def write_lock_entry(path: Path, entry: LockEntry) -> list[LockEntry]:
    entries = [item for item in load_lock(path) if item.key != entry.key]
    entries.append(entry)
    entries.sort(key=lambda item: item.key)
    payload = [
        {
            "key": item.key,
            "version": item.version,
            "kind": item.kind,
            "url": item.url,
            "commit": item.commit,
            "sha256": item.sha256,
            "license": item.license,
            "captured_at": item.captured_at,
            "notes": item.notes,
        }
        for item in entries
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return entries
