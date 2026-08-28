from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from arca.config import (
    FREE_SPACE_FLOOR_GIB,
    LIBRARY_DB_FILENAME,
    ArcaConfig,
    config_path,
    library_db_path,
)
from arca.db import open_library_ro
from arca.fetch import PART_SUFFIX, SHA256_SUFFIX, verify_library
from arca.i18n import Catalog
from arca.ingest import PDF_MIME, original_path
from arca.llama import (
    HEALTH_OK,
    LlamaServer,
    Profile,
    chat_model_file,
    default_profile,
    find_binary,
    health_of,
    last_good_profile,
    referenced_model,
)
from arca.shards import (
    STATE_INCOMPATIBLE,
    STATE_SEALED,
    ShardLibrary,
)
from arca.vendor import VendorError, check_drift, default_lock_path, load_lock

KIND_ZIM = "zim"
KIND_SHARD = "shard"
KIND_DOCUMENT = "document"
KIND_DATABASE = "database"
BUILD_EPOCH = datetime(2026, 8, 1, tzinfo=UTC)
FUTURE_TOLERANCE_SECONDS = 48 * 3600
GIB = 1024**3


class CheckState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    state: CheckState
    params: dict[str, str] = field(default_factory=dict)


def run_checks(
    root: Path,
    config: ArcaConfig,
    now: datetime | None = None,
    server: LlamaServer | None = None,
) -> list[CheckResult]:
    moment = now if now is not None else datetime.now(UTC)
    path_check = _check_library_path(root)
    if path_check.state is CheckState.BROKEN:
        return [path_check, _check_clock(None, moment)]
    return [
        path_check,
        _check_free_space(root, config),
        _check_write_access(root),
        _check_clock(root, moment),
        _check_zim_integrity(root),
        _check_incomplete_downloads(root),
        *_check_vendor_lock(),
        *_check_llama(root, config, server),
    ]


def worst_state(results: list[CheckResult]) -> CheckState:
    if any(result.state is CheckState.BROKEN for result in results):
        return CheckState.BROKEN
    if any(result.state is CheckState.DEGRADED for result in results):
        return CheckState.DEGRADED
    return CheckState.OK


def render(result: CheckResult, catalog: Catalog) -> dict[str, str]:
    base = f"diag.{result.check_id}.{result.state.value}"
    rendered = {
        "id": result.check_id,
        "state": result.state.value,
        "state_label": catalog.text(f"diag.state.{result.state.value}"),
        "summary": catalog.text(f"{base}.summary", **result.params),
    }
    for aspect in ("impact", "action"):
        key = f"{base}.{aspect}"
        if catalog.has(key):
            rendered[aspect] = catalog.text(key, **result.params)
    return rendered


def _check_library_path(root: Path) -> CheckResult:
    if root.is_dir() and os.access(root, os.R_OK):
        return CheckResult("library_path", CheckState.OK)
    return CheckResult("library_path", CheckState.BROKEN, {"root": str(root)})


def _check_free_space(root: Path, config: ArcaConfig) -> CheckResult:
    free_gib = shutil.disk_usage(root).free / GIB
    params = {"free_gib": f"{free_gib:.1f}", "warn_gib": f"{config.free_space_warn_gib:.1f}"}
    if free_gib < FREE_SPACE_FLOOR_GIB:
        return CheckResult("free_space", CheckState.BROKEN, params)
    if free_gib < config.free_space_warn_gib:
        return CheckResult("free_space", CheckState.DEGRADED, params)
    return CheckResult("free_space", CheckState.OK, params)


def _check_write_access(root: Path) -> CheckResult:
    if os.access(root, os.W_OK):
        return CheckResult("write_access", CheckState.OK)
    return CheckResult("write_access", CheckState.DEGRADED)


def _check_clock(root: Path | None, now: datetime) -> CheckResult:
    if now < BUILD_EPOCH:
        return CheckResult("clock", CheckState.BROKEN, {"now": now.date().isoformat()})
    newest = _newest_known_mtime(root)
    if newest is not None and (newest - now).total_seconds() > FUTURE_TOLERANCE_SECONDS:
        return CheckResult("clock", CheckState.DEGRADED, {"newest": newest.date().isoformat()})
    return CheckResult("clock", CheckState.OK)


def _check_zim_integrity(root: Path) -> CheckResult:
    rows = _zim_rows(root)
    if not rows:
        return CheckResult("zim_integrity", CheckState.OK, {"count": "0"})
    corrupt = sum(1 for row in rows if row["status"] == "corrupt")
    missing = 0
    unverifiable = 0
    for row in rows:
        path = root / "zim" / str(row["filename"])
        if not path.is_file():
            missing += 1
        elif not path.with_name(path.name + SHA256_SUFFIX).is_file():
            unverifiable += 1
    params = {
        "count": str(len(rows)),
        "corrupt": str(corrupt),
        "missing": str(missing),
        "unverifiable": str(unverifiable),
    }
    if corrupt or missing or unverifiable:
        return CheckResult("zim_integrity", CheckState.DEGRADED, params)
    return CheckResult("zim_integrity", CheckState.OK, params)


def _check_incomplete_downloads(root: Path) -> CheckResult:
    active = _active_download_parts(root)
    orphans = [
        part
        for part in root.rglob(f"*{PART_SUFFIX}")
        if part.is_file() and str(part.resolve()) not in active
    ]
    if orphans:
        return CheckResult(
            "incomplete_downloads",
            CheckState.DEGRADED,
            {"count": str(len(orphans)), "first": orphans[0].name},
        )
    return CheckResult("incomplete_downloads", CheckState.OK)


def _check_vendor_lock() -> list[CheckResult]:
    try:
        entries = load_lock(default_lock_path())
    except VendorError:
        params = {"detail": str(default_lock_path())}
        return [
            CheckResult("doc_drift", CheckState.DEGRADED, params),
            CheckResult("undocumented_dependency", CheckState.DEGRADED, params),
        ]
    statuses = check_drift(entries)
    drifted = [status.package for status in statuses if status.result == "drift"]
    undocumented = [status.package for status in statuses if status.result == "undocumented"]
    drift_result = (
        CheckResult("doc_drift", CheckState.DEGRADED, {"detail": ", ".join(drifted)})
        if drifted
        else CheckResult("doc_drift", CheckState.OK)
    )
    undocumented_result = (
        CheckResult(
            "undocumented_dependency",
            CheckState.DEGRADED,
            {"detail": ", ".join(undocumented)},
        )
        if undocumented
        else CheckResult("undocumented_dependency", CheckState.OK)
    )
    return [drift_result, undocumented_result]


def _zim_rows(root: Path) -> list[sqlite3.Row]:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return []
    try:
        connection = open_library_ro(db_path)
    except sqlite3.Error:
        return []
    try:
        return list(connection.execute("SELECT filename, status FROM zim_files").fetchall())
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _active_download_parts(root: Path) -> set[str]:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return set()
    try:
        connection = open_library_ro(db_path)
    except sqlite3.Error:
        return set()
    try:
        rows = connection.execute(
            "SELECT dest_path FROM downloads "
            "WHERE status IN ('queued', 'running', 'paused', 'verifying')"
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        connection.close()
    return {str(Path(str(row["dest_path"]) + PART_SUFFIX).resolve()) for row in rows}


def _newest_known_mtime(root: Path | None) -> datetime | None:
    if root is None or not root.is_dir():
        return None
    stamps = []
    for candidate in (config_path(root), library_db_path(root)):
        if candidate.is_file():
            stamps.append(datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC))
    if not stamps:
        return None
    return max(stamps)


def _check_llama(root: Path, config: ArcaConfig, server: LlamaServer | None) -> list[CheckResult]:
    binary, _ = find_binary(root, config.llama_bin)
    if binary is None:
        binary_result = CheckResult("llama_binary", CheckState.DEGRADED)
    else:
        binary_result = CheckResult("llama_binary", CheckState.OK, {"path": str(binary)})
    url = server.base_url if server is not None else config.chat_url
    health = server.health(timeout=1.0) if server is not None else _probe_health(url)
    if health == HEALTH_OK:
        server_result = CheckResult("llama_server", CheckState.OK, {"url": url})
    else:
        server_result = CheckResult("llama_server", CheckState.DEGRADED, {"url": url})
    return [binary_result, server_result, _check_chat_model(root, config)]


def _check_chat_model(root: Path, config: ArcaConfig) -> CheckResult:
    wanted = referenced_model(config.chat_model, _profile(root))
    model = chat_model_file(root, wanted)
    if model is not None and not model.error:
        return CheckResult("chat_model", CheckState.OK, {"name": model.name})
    if wanted:
        return CheckResult("chat_model", CheckState.BROKEN, {"name": wanted})
    return CheckResult("chat_model", CheckState.DEGRADED, {"name": ""})


def _profile(root: Path) -> Profile | None:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return None
    try:
        connection = open_library_ro(db_path)
    except sqlite3.Error:
        return None
    try:
        return default_profile(connection) or last_good_profile(connection)
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _probe_health(url: str) -> str:
    return health_of(url, timeout=1.0)


@dataclass(frozen=True)
class IntegrityItem:
    kind: str
    name: str
    result: str
    detail: str = ""


def verify_integrity(
    root: Path, config: ArcaConfig, connection: sqlite3.Connection
) -> list[IntegrityItem]:
    items = [
        IntegrityItem(KIND_ZIM, outcome.filename, outcome.result)
        for outcome in verify_library(connection, root)
    ]
    items.extend(_shard_items(root, config))
    items.extend(_database_items(root, connection))
    return items


def _shard_items(root: Path, config: ArcaConfig) -> list[IntegrityItem]:
    library = ShardLibrary(root, config.embed_model, config.embed_dims)
    items: list[IntegrityItem] = []
    try:
        for state in library.refresh():
            if state.state == STATE_SEALED:
                items.append(IntegrityItem(KIND_SHARD, state.shard_id, "ok"))
            elif state.state == STATE_INCOMPATIBLE:
                items.append(
                    IntegrityItem(KIND_SHARD, state.shard_id, "incompatible", state.reason)
                )
            else:
                items.append(IntegrityItem(KIND_SHARD, state.shard_id, state.state, state.reason))
            items.extend(_document_items(library, state.shard_id, root))
    finally:
        library.close()
    return items


def _document_items(library: ShardLibrary, shard_id: str, root: Path) -> list[IntegrityItem]:
    shard = library.shard(shard_id)
    if shard is None:
        return []
    missing = [
        row.doc_id
        for row in shard.documents()
        if row.mime == PDF_MIME and not original_path(root, row.doc_id).is_file()
    ]
    return [IntegrityItem(KIND_DOCUMENT, doc_id[:12], "missing") for doc_id in missing]


def _database_items(root: Path, connection: sqlite3.Connection) -> list[IntegrityItem]:
    items: list[IntegrityItem] = []
    try:
        verdict = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.Error as error:
        return [IntegrityItem(KIND_DATABASE, LIBRARY_DB_FILENAME, "corrupt", str(error))]
    items.append(
        IntegrityItem(
            KIND_DATABASE,
            LIBRARY_DB_FILENAME,
            "ok" if verdict == "ok" else "corrupt",
            "" if verdict == "ok" else verdict,
        )
    )
    try:
        orphans = connection.execute(
            "SELECT filename FROM zim_files WHERE status = 'missing' ORDER BY filename"
        ).fetchall()
        stale = connection.execute(
            "SELECT DISTINCT shard_id FROM ingest_jobs "
            "WHERE shard_id IS NOT NULL AND shard_id != '' ORDER BY shard_id"
        ).fetchall()
    except sqlite3.Error as error:
        items.append(IntegrityItem(KIND_DATABASE, "tables", "corrupt", str(error)))
        return items
    for row in orphans:
        items.append(IntegrityItem(KIND_DATABASE, str(row["filename"]), "orphan"))
    known = {path.name for path in (root / "docs" / "shards").glob("*") if path.is_dir()}
    for row in stale:
        shard_id = str(row["shard_id"])
        if shard_id not in known:
            items.append(IntegrityItem(KIND_DATABASE, shard_id, "orphan"))
    return items
