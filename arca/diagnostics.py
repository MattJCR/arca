from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Sequence
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
from arca.embed import EmbeddingClient, EmbeddingError
from arca.fetch import PART_SUFFIX, SHA256_SUFFIX, verify_library
from arca.i18n import Catalog
from arca.ingest import PDF_MIME, original_path
from arca.llama import (
    HEALTH_OK,
    LlamaServer,
    Profile,
    chat_model_file,
    default_profile,
    estimate_memory,
    find_binary,
    health_of,
    last_good_profile,
    referenced_model,
    system_memory_bytes,
)
from arca.shards import (
    STATE_CORRUPT,
    STATE_INCOMPATIBLE,
    STATE_PROVISIONAL,
    STATE_SEALED,
    ShardLibrary,
    ShardState,
)
from arca.vendor import VendorError, check_drift, default_lock_path, load_lock
from arca.zim import ZimInfo, ZimLibrary, index_mode

KIND_ZIM = "zim"
KIND_SHARD = "shard"
KIND_DOCUMENT = "document"
KIND_DATABASE = "database"
BUILD_EPOCH = datetime(2026, 8, 1, tzinfo=UTC)
FUTURE_TOLERANCE_SECONDS = 48 * 3600
GIB = 1024**3
INDEX_FULLTEXT = "fulltext"
REFERENCE_CONTEXT = 4096
EMBEDDING_PROBE_SECONDS = 5.0
EMBEDDING_PROBE_TEXT = "arca"


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
    zim: ZimLibrary | None = None,
    shards: ShardLibrary | None = None,
) -> list[CheckResult]:
    moment = now if now is not None else datetime.now(UTC)
    path_check = _check_library_path(root)
    if path_check.state is CheckState.BROKEN:
        return [path_check, _check_clock(None, moment)]
    states = _shard_states(root, config, shards)
    return [
        path_check,
        _check_free_space(root, config),
        _check_write_access(root),
        _check_clock(root, moment),
        _check_zim_integrity(root),
        _check_fulltext_index(root, zim),
        _check_shard_manifests(states),
        _check_embedding_coherence(states),
        _check_provisional_shards(states),
        _check_embedding_server(config),
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
    return [binary_result, server_result, *_check_chat_model(root, config)]


def _check_chat_model(root: Path, config: ArcaConfig) -> list[CheckResult]:
    wanted = referenced_model(config.chat_model, _profile(root))
    model = chat_model_file(root, wanted)
    if model is None or model.error:
        state = CheckState.BROKEN if wanted else CheckState.DEGRADED
        return [CheckResult("chat_model", state, {"name": wanted})]
    needed = estimate_memory(model, REFERENCE_CONTEXT, "f16", "f16")
    available = system_memory_bytes()
    params = {
        "name": model.name,
        "needed_gib": f"{needed / GIB:.1f}",
        "available_gib": f"{available / GIB:.1f}",
        "context": str(REFERENCE_CONTEXT),
    }
    memory = CheckState.DEGRADED if available and needed > available else CheckState.OK
    return [
        CheckResult("chat_model", CheckState.OK, {"name": model.name}),
        CheckResult("chat_model_memory", memory, params),
    ]


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


def _shard_states(root: Path, config: ArcaConfig, library: ShardLibrary | None) -> list[ShardState]:
    if library is not None:
        return library.refresh()
    own = ShardLibrary(root, config.embed_model, config.embed_dims)
    try:
        return own.refresh()
    finally:
        own.close()


def _zim_infos(root: Path, library: ZimLibrary | None) -> list[ZimInfo]:
    if library is not None:
        return library.refresh()
    own = ZimLibrary(root)
    try:
        return own.refresh()
    finally:
        own.close()


def _check_fulltext_index(root: Path, library: ZimLibrary | None) -> CheckResult:
    infos = _zim_infos(root, library)
    without = [info for info in infos if index_mode(info) != INDEX_FULLTEXT]
    params = {
        "count": str(len(infos)),
        "without": str(len(without)),
        "first": without[0].title if without else "",
    }
    if without:
        return CheckResult("fulltext_index", CheckState.DEGRADED, params)
    return CheckResult("fulltext_index", CheckState.OK, params)


def _check_shard_manifests(states: Sequence[ShardState]) -> CheckResult:
    sealed = [state for state in states if state.state == STATE_SEALED]
    corrupt = [state for state in states if state.state == STATE_CORRUPT]
    params = {
        "count": str(len(sealed)),
        "corrupt": str(len(corrupt)),
        "detail": ", ".join(state.shard_id for state in corrupt),
    }
    if corrupt:
        return CheckResult("shard_manifests", CheckState.DEGRADED, params)
    return CheckResult("shard_manifests", CheckState.OK, params)


def _check_embedding_coherence(states: Sequence[ShardState]) -> CheckResult:
    mismatched = [state for state in states if state.state == STATE_INCOMPATIBLE]
    params = {
        "count": str(len(states)),
        "mismatched": str(len(mismatched)),
        "detail": ", ".join(state.shard_id for state in mismatched),
    }
    if mismatched:
        return CheckResult("embedding_coherence", CheckState.DEGRADED, params)
    return CheckResult("embedding_coherence", CheckState.OK, params)


def _check_provisional_shards(states: Sequence[ShardState]) -> CheckResult:
    pending = [state for state in states if state.state == STATE_PROVISIONAL]
    params = {"count": str(len(pending)), "detail": ", ".join(s.shard_id for s in pending)}
    if pending:
        return CheckResult("provisional_shards", CheckState.DEGRADED, params)
    return CheckResult("provisional_shards", CheckState.OK, params)


def _check_embedding_server(config: ArcaConfig) -> CheckResult:
    client = EmbeddingClient(
        base_url=config.embed_url,
        model=config.embed_model,
        dims=config.embed_dims,
        timeout=EMBEDDING_PROBE_SECONDS,
    )
    params = {"url": config.embed_url, "dims": str(config.embed_dims), "found": ""}
    if not client.available():
        return CheckResult("embedding_server", CheckState.DEGRADED, params)
    try:
        vector = client.embed_query(EMBEDDING_PROBE_TEXT)
    except EmbeddingError as error:
        params["found"] = str(error)
        return CheckResult("embedding_server", CheckState.BROKEN, params)
    params["found"] = str(len(vector))
    if len(vector) != config.embed_dims:
        return CheckResult("embedding_server", CheckState.BROKEN, params)
    return CheckResult("embedding_server", CheckState.OK, params)
