from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from arca.catalog import CatalogEntry, Resolution, record_zim_entry
from arca.db import open_library_rw, record_event, utc_now_iso
from arca.opds import ALLOWED_URL_SCHEMES, USER_AGENT

PART_SUFFIX = ".part"
SHA256_SUFFIX = ".sha256"
STREAM_CHUNK_BYTES = 256 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
REPLACE_ATTEMPTS = 3
REPLACE_RETRY_SECONDS = 0.2
PROGRESS_FLUSH_BYTES = 8 * 1024 * 1024
PROGRESS_FLUSH_SECONDS = 2.0
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_CONCURRENCY = 2
REQUEST_TIMEOUT_SECONDS = 30
CANCELLED_ERROR = "cancelled"
SHA256_LENGTH = 64
SHA256_ALPHABET = "0123456789abcdef"
HASH_MISMATCH_ERROR = "hash mismatch"

ACTIVE_STATUSES = ("queued", "running", "paused", "verifying")
ZIM_SUBDIR = "zim"
BOOKS_SUBDIR = "books"
KIND_ZIM = "zim"
KIND_BOOK = "book"
AFTER_INDEX = "index"

ProgressCallback = Callable[["ProgressUpdate"], None]
SleepFunction = Callable[[float], None]
ReplaceHook = Callable[[Path], None]
DoneHook = Callable[["QueueResult"], None]


@dataclass(frozen=True)
class ProgressUpdate:
    download_id: int
    filename: str
    bytes_done: int
    size_bytes: int | None
    mirror_url: str


@dataclass(frozen=True)
class QueueResult:
    download_id: int
    kind: str
    ref: str
    filename: str
    status: str
    dest_path: str = ""
    after_done: str = ""


class _DownloadInterrupted(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def enqueue(
    connection: sqlite3.Connection,
    *,
    kind: str,
    ref: str,
    filename: str,
    dest_path: Path,
    size_bytes: int | None,
    sha256: str,
    mirrors: tuple[str, ...],
    after_done: str = "",
) -> int:
    existing = connection.execute(
        "SELECT id, status FROM downloads WHERE kind = ? AND ref = ? ORDER BY id DESC LIMIT 1",
        (kind, ref),
    ).fetchone()
    if existing is not None and existing["status"] in ACTIVE_STATUSES:
        return int(existing["id"])
    now = utc_now_iso()
    cursor = connection.execute(
        """
        INSERT INTO downloads (
            kind, ref, filename, dest_path, size_bytes, sha256, mirrors, status,
            after_done, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (
            kind,
            ref,
            filename,
            str(dest_path),
            size_bytes,
            sha256,
            json.dumps(list(mirrors)),
            after_done,
            now,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid or 0)


def queue_zim_download(
    connection: sqlite3.Connection,
    root: Path,
    entry: CatalogEntry,
    resolution: Resolution,
) -> int:
    if not entry.filename or entry.filename != Path(entry.filename).name:
        raise ValueError(f"unsafe filename: {entry.filename}")
    record_zim_entry(connection, entry)
    download_id = enqueue(
        connection,
        kind=KIND_ZIM,
        ref=entry.uuid,
        filename=entry.filename,
        dest_path=root / ZIM_SUBDIR / entry.filename,
        size_bytes=resolution.size_bytes or entry.size_bytes,
        sha256=resolution.sha256,
        mirrors=resolution.mirrors,
    )
    record_event(connection, "info", "fetch", f"queued download: {entry.filename}")
    return download_id


def queue_book_download(
    connection: sqlite3.Connection,
    root: Path,
    *,
    ref: str,
    filename: str,
    resolution: Resolution,
    after_done: str = "",
) -> int:
    if not filename or filename != Path(filename).name:
        raise ValueError(f"unsafe filename: {filename}")
    download_id = enqueue(
        connection,
        kind=KIND_BOOK,
        ref=ref,
        filename=filename,
        dest_path=root / BOOKS_SUBDIR / filename,
        size_bytes=resolution.size_bytes,
        sha256=resolution.sha256,
        mirrors=resolution.mirrors,
        after_done=after_done,
    )
    record_event(connection, "info", "fetch", f"queued download: {filename}")
    return download_id


def queue_manual_download(
    connection: sqlite3.Connection,
    root: Path,
    *,
    url: str,
    filename: str,
    item_kind: str,
    sha256: str,
    after_done: str = "",
) -> int:
    resolution = Resolution(size_bytes=None, sha256=sha256, mirrors=(url,), hash_missing=not sha256)
    if item_kind != KIND_ZIM:
        return queue_book_download(
            connection,
            root,
            ref=f"manual:{url}",
            filename=filename,
            resolution=resolution,
            after_done=after_done,
        )
    if not filename or filename != Path(filename).name:
        raise ValueError(f"unsafe filename: {filename}")
    download_id = enqueue(
        connection,
        kind=KIND_ZIM,
        ref=f"manual:{url}",
        filename=filename,
        dest_path=root / ZIM_SUBDIR / filename,
        size_bytes=None,
        sha256=sha256,
        mirrors=resolution.mirrors,
    )
    record_event(connection, "info", "fetch", f"queued download: {filename}")
    return download_id


def list_downloads(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(connection.execute("SELECT * FROM downloads ORDER BY id DESC").fetchall())


def get_download(connection: sqlite3.Connection, download_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM downloads WHERE id = ?", (download_id,)
    ).fetchone()
    return row


def request_pause(connection: sqlite3.Connection, download_id: int) -> bool:
    return _transition(connection, download_id, ("queued", "running"), "paused")


def request_resume(connection: sqlite3.Connection, download_id: int) -> bool:
    return _transition(connection, download_id, ("paused",), "queued")


def request_retry(connection: sqlite3.Connection, download_id: int) -> bool:
    changed = connection.execute(
        """
        UPDATE downloads SET status = 'queued', attempts = 0, last_error = NULL, updated_at = ?
        WHERE id = ? AND status = 'failed'
        """,
        (utc_now_iso(), download_id),
    ).rowcount
    connection.commit()
    return changed > 0


def request_delete(connection: sqlite3.Connection, download_id: int) -> bool:
    row = get_download(connection, download_id)
    if row is None:
        return False
    part_path = Path(str(row["dest_path"]) + PART_SUFFIX)
    connection.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
    connection.commit()
    with contextlib.suppress(OSError):
        part_path.unlink(missing_ok=True)
    record_event(connection, "info", "fetch", f"download removed: {row['filename']}")
    return True


def request_cancel(connection: sqlite3.Connection, download_id: int) -> bool:
    changed = connection.execute(
        """
        UPDATE downloads SET status = 'failed', last_error = ?, updated_at = ?
        WHERE id = ? AND status IN ('queued', 'running', 'paused')
        """,
        (CANCELLED_ERROR, utc_now_iso(), download_id),
    ).rowcount
    connection.commit()
    return changed > 0


def requeue_stale(connection: sqlite3.Connection) -> int:
    changed = connection.execute(
        """
        UPDATE downloads SET status = 'queued', updated_at = ?
        WHERE status IN ('running', 'verifying')
        """,
        (utc_now_iso(),),
    ).rowcount
    connection.commit()
    return changed


def claim_next(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        """
        UPDATE downloads SET status = 'running', started_at = COALESCE(started_at, ?),
            updated_at = ?
        WHERE id = (SELECT id FROM downloads WHERE status = 'queued' ORDER BY id LIMIT 1)
            AND status = 'queued'
        RETURNING id
        """,
        (utc_now_iso(), utc_now_iso()),
    ).fetchone()
    connection.commit()
    if row is None:
        return None
    return int(row["id"])


def run_download(
    connection: sqlite3.Connection,
    download_id: int,
    *,
    progress: ProgressCallback | None = None,
    sleep: SleepFunction = time.sleep,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    before_replace: ReplaceHook | None = None,
) -> str:
    while True:
        row = get_download(connection, download_id)
        if row is None:
            return "failed"
        if row["status"] != "running":
            return str(row["status"])
        mirrors = _mirrors(row)
        if not mirrors:
            return _fail(connection, download_id, "no mirrors available")
        part_path = Path(row["dest_path"] + PART_SUFFIX)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_url = mirrors[int(row["mirror_index"]) % len(mirrors)]
        try:
            _stream(connection, row, part_path, mirror_url, progress)
            return _verify_and_finish(connection, download_id, part_path, before_replace)
        except _DownloadInterrupted as interrupted:
            return interrupted.status
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            outcome = _register_failure(connection, download_id, str(error), len(mirrors))
            if outcome is not None:
                return outcome
            attempts = int((get_download(connection, download_id) or row)["attempts"])
            if max_attempts > 0 and attempts >= max_attempts:
                return _fail(connection, download_id, str(error))
            sleep(min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_CAP_SECONDS))


def run_queue(
    db_path: Path,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress: ProgressCallback | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: SleepFunction = time.sleep,
    before_replace: ReplaceHook | None = None,
    on_done: DoneHook | None = None,
) -> list[QueueResult]:
    results: list[QueueResult] = []
    results_lock = threading.Lock()
    bootstrap = open_library_rw(db_path)
    try:
        requeue_stale(bootstrap)
    finally:
        bootstrap.close()

    def worker() -> None:
        connection = open_library_rw(db_path)
        try:
            while True:
                download_id = claim_next(connection)
                if download_id is None:
                    return
                status = run_download(
                    connection,
                    download_id,
                    progress=progress,
                    sleep=sleep,
                    max_attempts=max_attempts,
                    before_replace=before_replace,
                )
                result = _finalize(connection, download_id, status)
                if result is not None:
                    with results_lock:
                        results.append(result)
                    _run_hook(connection, on_done, result)
        finally:
            connection.close()

    workers = [threading.Thread(target=worker) for _ in range(max(1, concurrency))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    return results


class QueueWorker:
    def __init__(
        self,
        db_path: Path,
        *,
        concurrency: int,
        max_attempts: int,
        before_replace: ReplaceHook | None = None,
        on_done: DoneHook | None = None,
    ) -> None:
        self._db_path = db_path
        self._concurrency = concurrency
        self._max_attempts = max_attempts
        self._before_replace = before_replace
        self._on_done = on_done
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def kick(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._drain, daemon=True)
            self._thread.start()

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _drain(self) -> None:
        run_queue(
            self._db_path,
            concurrency=self._concurrency,
            max_attempts=self._max_attempts,
            before_replace=self._before_replace,
            on_done=self._on_done,
        )


def is_sha256(value: str) -> bool:
    return len(value) == SHA256_LENGTH and all(character in SHA256_ALPHABET for character in value)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_sidecar(final_path: Path, digest: str) -> None:
    sidecar = final_path.with_name(final_path.name + SHA256_SUFFIX)
    sidecar.write_text(f"{digest}  {final_path.name}\n", "utf-8")


def read_sha256_sidecar(final_path: Path) -> str:
    sidecar = final_path.with_name(final_path.name + SHA256_SUFFIX)
    try:
        content = sidecar.read_text("utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    return content.split()[0]


@dataclass(frozen=True)
class VerifyOutcome:
    uuid: str
    filename: str
    result: str


def verify_library(connection: sqlite3.Connection, root: Path) -> list[VerifyOutcome]:
    outcomes = []
    rows = connection.execute(
        "SELECT uuid, filename, sha256, status FROM zim_files ORDER BY filename"
    ).fetchall()
    for row in rows:
        outcome = _verify_zim(connection, root, row)
        outcomes.append(outcome)
    return outcomes


def _verify_zim(connection: sqlite3.Connection, root: Path, row: sqlite3.Row) -> VerifyOutcome:
    uuid = str(row["uuid"])
    filename = str(row["filename"])
    path = root / ZIM_SUBDIR / filename
    if not path.is_file():
        _set_zim_status(connection, uuid, "missing")
        return VerifyOutcome(uuid, filename, "missing")
    expected = read_sha256_sidecar(path) or str(row["sha256"] or "")
    if not expected:
        _set_zim_status(connection, uuid, "present")
        return VerifyOutcome(uuid, filename, "no_hash")
    if file_sha256(path) == expected:
        _set_zim_status(connection, uuid, "present")
        return VerifyOutcome(uuid, filename, "ok")
    _set_zim_status(connection, uuid, "corrupt")
    record_event(connection, "error", "verify", f"corrupt zim detected: {filename}")
    return VerifyOutcome(uuid, filename, "corrupt")


def _set_zim_status(connection: sqlite3.Connection, uuid: str, status: str) -> None:
    connection.execute("UPDATE zim_files SET status = ? WHERE uuid = ?", (status, uuid))
    connection.commit()


def _stream(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    part_path: Path,
    mirror_url: str,
    progress: ProgressCallback | None,
) -> None:
    download_id = int(row["id"])
    filename = str(row["filename"])
    size_bytes = row["size_bytes"]
    if urllib.parse.urlsplit(mirror_url).scheme not in ALLOWED_URL_SCHEMES:
        raise OSError(f"unsupported mirror scheme: {mirror_url}")
    resume_from = part_path.stat().st_size if part_path.exists() else 0
    if size_bytes is not None and resume_from >= int(size_bytes) > 0:
        return
    headers = {"User-Agent": USER_AGENT}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(mirror_url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        if error.code == 416:
            return
        raise
    with response:
        if resume_from > 0 and response.status == 206:
            mode = "ab"
            bytes_done = resume_from
        else:
            mode = "wb"
            bytes_done = 0
        declared = str(response.headers.get("Content-Length", ""))
        expected_stream = int(declared) if declared.isdigit() else None
        streamed = 0
        with part_path.open(mode) as handle:
            unflushed = 0
            last_flush = time.monotonic()
            while chunk := response.read(STREAM_CHUNK_BYTES):
                handle.write(chunk)
                bytes_done += len(chunk)
                streamed += len(chunk)
                unflushed += len(chunk)
                now = time.monotonic()
                if unflushed >= PROGRESS_FLUSH_BYTES or now - last_flush >= PROGRESS_FLUSH_SECONDS:
                    unflushed = 0
                    last_flush = now
                    _flush_progress(
                        connection,
                        download_id,
                        filename,
                        bytes_done,
                        size_bytes,
                        mirror_url,
                        progress,
                    )
    _flush_progress(connection, download_id, filename, bytes_done, size_bytes, mirror_url, progress)
    if expected_stream is not None and streamed < expected_stream:
        raise OSError(f"connection closed after {streamed} of {expected_stream} bytes")


def _flush_progress(
    connection: sqlite3.Connection,
    download_id: int,
    filename: str,
    bytes_done: int,
    size_bytes: int | None,
    mirror_url: str,
    progress: ProgressCallback | None,
) -> None:
    connection.execute(
        "UPDATE downloads SET bytes_done = ?, updated_at = ? WHERE id = ? AND status = 'running'",
        (bytes_done, utc_now_iso(), download_id),
    )
    connection.commit()
    row = get_download(connection, download_id)
    current_status = str(row["status"]) if row is not None else "failed"
    if progress is not None:
        progress(ProgressUpdate(download_id, filename, bytes_done, size_bytes, mirror_url))
    if current_status != "running":
        raise _DownloadInterrupted(current_status)


def _replace_when_free(
    part_path: Path, final_path: Path, before_replace: ReplaceHook | None
) -> None:
    if before_replace is not None:
        before_replace(final_path)
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(part_path, final_path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_SECONDS)


def _verify_and_finish(
    connection: sqlite3.Connection,
    download_id: int,
    part_path: Path,
    before_replace: ReplaceHook | None = None,
) -> str:
    row = get_download(connection, download_id)
    if row is None:
        return "failed"
    connection.execute(
        "UPDATE downloads SET status = 'verifying', updated_at = ? WHERE id = ?",
        (utc_now_iso(), download_id),
    )
    connection.commit()
    if not part_path.exists():
        return _fail(connection, download_id, "downloaded file missing before verification")
    digest = file_sha256(part_path)
    expected = str(row["sha256"] or "")
    if expected and digest != expected:
        record_event(
            connection,
            "error",
            "fetch",
            f"hash mismatch for {row['filename']}",
            f"expected {expected} got {digest}",
        )
        return _fail(connection, download_id, HASH_MISMATCH_ERROR)
    final_path = Path(row["dest_path"])
    _replace_when_free(part_path, final_path, before_replace)
    write_sha256_sidecar(final_path, digest)
    connection.execute(
        """
        UPDATE downloads SET status = 'done', sha256 = ?, bytes_done = ?, size_bytes = ?,
            updated_at = ? WHERE id = ?
        """,
        (digest, final_path.stat().st_size, final_path.stat().st_size, utc_now_iso(), download_id),
    )
    connection.commit()
    if not expected:
        record_event(
            connection,
            "warning",
            "fetch",
            f"no upstream hash for {row['filename']}",
            f"stored computed sha256 {digest} for future local verification",
        )
    return "done"


def _register_failure(
    connection: sqlite3.Connection, download_id: int, error: str, mirror_count: int
) -> str | None:
    row = get_download(connection, download_id)
    if row is None:
        return "failed"
    if row["status"] != "running":
        return str(row["status"])
    connection.execute(
        """
        UPDATE downloads SET attempts = attempts + 1, mirror_index = (mirror_index + 1) % ?,
            last_error = ?, updated_at = ? WHERE id = ?
        """,
        (max(1, mirror_count), error, utc_now_iso(), download_id),
    )
    connection.commit()
    return None


def _fail(connection: sqlite3.Connection, download_id: int, error: str) -> str:
    connection.execute(
        "UPDATE downloads SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
        (error, utc_now_iso(), download_id),
    )
    connection.commit()
    return "failed"


def _run_hook(
    connection: sqlite3.Connection, on_done: DoneHook | None, result: QueueResult
) -> None:
    if on_done is None:
        return
    try:
        on_done(result)
    except Exception as error:
        record_event(
            connection,
            "error",
            "fetch",
            f"post-download step failed: {result.filename}",
            f"{type(error).__name__}: {error}",
        )


def _finalize(connection: sqlite3.Connection, download_id: int, status: str) -> QueueResult | None:
    row = get_download(connection, download_id)
    if row is None:
        return None
    result = QueueResult(
        download_id=download_id,
        kind=str(row["kind"]),
        ref=str(row["ref"]),
        filename=str(row["filename"]),
        status=status,
        dest_path=str(row["dest_path"]),
        after_done=str(row["after_done"] or ""),
    )
    if result.kind == KIND_ZIM and status == "done":
        connection.execute(
            """
            UPDATE zim_files SET status = 'present', sha256 = ?, size_bytes = ?
            WHERE uuid = ?
            """,
            (row["sha256"], row["size_bytes"], result.ref),
        )
        connection.commit()
        record_event(connection, "info", "fetch", f"download complete: {result.filename}")
    elif status == "done":
        record_event(connection, "info", "fetch", f"download complete: {result.filename}")
    elif status == "failed":
        record_event(
            connection,
            "error",
            "fetch",
            f"download failed: {result.filename}",
            str(row["last_error"] or ""),
        )
    return result


def _mirrors(row: sqlite3.Row) -> list[str]:
    try:
        parsed: object = json.loads(str(row["mirrors"] or "[]"))
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item]


def _transition(
    connection: sqlite3.Connection,
    download_id: int,
    allowed_from: tuple[str, ...],
    target: str,
) -> bool:
    placeholders = ", ".join("?" for _ in allowed_from)
    changed = connection.execute(
        f"UPDATE downloads SET status = ?, updated_at = ? WHERE id = ? "
        f"AND status IN ({placeholders})",
        (target, utc_now_iso(), download_id, *allowed_from),
    ).rowcount
    connection.commit()
    return changed > 0
