from __future__ import annotations

import sqlite3
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from arca.db import record_event, utc_now_iso
from arca.embed import EMBED_DIMS, EMBED_MODEL, EmbeddingClient, EmbeddingError
from arca.extract import OcrRunner, extract_document, run_ocrmypdf
from arca.formats import FIXED_LAYOUT, Block, IngestError, ocr_available
from arca.ingest import (
    MAX_CHUNK_TOKENS,
    OriginalRecord,
    anchor,
    chunk_blocks,
    original_path,
    read_sidecar,
    receive_document,
    write_sidecar,
)
from arca.search import SearchSettings, read_search_settings
from arca.shards import (
    KIND_DOCS,
    KIND_VENDOR,
    MANIFEST_FILENAME,
    Manifest,
    ShardWriter,
    new_shard_id,
    shard_path,
)
from arca.vendor import VendorSourceRow

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_SKIPPED = "skipped"
JOB_RETRY = "retry"
PENDING_SUBDIR = "pending"
VECTOR_SUFFIX = ".npy"
UNINDEXABLE_REASON = "no text layer and no ocr available"
ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class JobResult:
    doc_id: str
    orig_name: str
    status: str
    chunks: int
    unindexable_pages: int
    error: str


@dataclass(frozen=True)
class IngestReport:
    shard_id: str
    sealed: bool
    manifest: Manifest | None
    results: list[JobResult]

    @property
    def done(self) -> int:
        return sum(1 for result in self.results if result.status == JOB_DONE)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == JOB_FAILED)

    @property
    def retry(self) -> int:
        return sum(1 for result in self.results if result.status == JOB_RETRY)

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == JOB_SKIPPED)


def pending_dir(directory: Path) -> Path:
    return directory / PENDING_SUBDIR


def open_batch(connection: sqlite3.Connection, root: Path) -> str:
    for shard_id in _known_batches(connection):
        if _is_provisional(root, shard_id):
            return shard_id
    shard_id = new_shard_id()
    shard_path(root, shard_id, KIND_DOCS).mkdir(parents=True, exist_ok=True)
    return shard_id


def _known_batches(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT shard_id, MAX(id) AS newest FROM ingest_jobs "
        "WHERE shard_id IS NOT NULL AND shard_id != '' GROUP BY shard_id ORDER BY newest DESC"
    ).fetchall()
    return [str(row["shard_id"]) for row in rows]


def _is_provisional(root: Path, shard_id: str) -> bool:
    directory = shard_path(root, shard_id, KIND_DOCS)
    return directory.is_dir() and not (directory / MANIFEST_FILENAME).is_file()


def queue_document(
    connection: sqlite3.Connection, shard_id: str, source: Path, doc_id: str, pages: int
) -> int:
    row = connection.execute(
        "SELECT id, status FROM ingest_jobs WHERE shard_id = ? AND sha256 = ?",
        (shard_id, doc_id),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = connection.execute(
        "INSERT INTO ingest_jobs (shard_id, source_path, sha256, pages, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (shard_id, str(source), doc_id, pages, JOB_QUEUED, utc_now_iso()),
    )
    connection.commit()
    return int(cursor.lastrowid or 0)


def job_status(connection: sqlite3.Connection, job_id: int) -> str:
    row = connection.execute("SELECT status FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
    return str(row["status"]) if row is not None else ""


def set_job_status(
    connection: sqlite3.Connection, job_id: int, status: str, error: str = ""
) -> None:
    connection.execute(
        "UPDATE ingest_jobs SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (status, error, utc_now_iso(), job_id),
    )
    connection.commit()


def unfinished_jobs(connection: sqlite3.Connection, shard_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM ingest_jobs WHERE shard_id = ? AND status IN (?, ?)",
        (shard_id, JOB_QUEUED, JOB_RUNNING),
    ).fetchone()
    return int(row[0])


def provisional_shards(connection: sqlite3.Connection, root: Path) -> list[str]:
    return sorted(
        shard_id for shard_id in _known_batches(connection) if _is_provisional(root, shard_id)
    )


def ingest_documents(
    root: Path,
    connection: sqlite3.Connection,
    sources: Sequence[Path],
    client: EmbeddingClient,
    ocr: OcrRunner | None = None,
    progress: ProgressCallback | None = None,
    seal: bool = True,
) -> IngestReport:
    shard_id = open_batch(connection, root)
    directory = shard_path(root, shard_id, KIND_DOCS)
    writer = ShardWriter(directory, KIND_DOCS)
    results: list[JobResult] = []
    try:
        for source in sources:
            results.append(
                _process(root, connection, writer, shard_id, source, client, ocr, progress)
            )
    finally:
        writer.close()
    if not seal or unfinished_jobs(connection, shard_id) or not results:
        return IngestReport(shard_id, False, None, results)
    manifest = seal_batch(root, connection, shard_id, client.model, client.dims)
    return IngestReport(shard_id, manifest is not None, manifest, results)


def seal_batch(
    root: Path,
    connection: sqlite3.Connection,
    shard_id: str,
    embed_model: str = EMBED_MODEL,
    embed_dims: int = EMBED_DIMS,
) -> Manifest | None:
    directory = shard_path(root, shard_id, KIND_DOCS)
    writer = ShardWriter(directory, KIND_DOCS)
    documents = writer.documents()
    if not documents:
        writer.close()
        return None
    vectors: list[list[float]] = []
    chunk_ids: list[int] = []
    for doc_id in documents:
        stored = _load_vectors(directory, doc_id)
        if stored is None:
            writer.close()
            return None
        ids, matrix = stored
        chunk_ids.extend(ids)
        vectors.extend(matrix.tolist())
    manifest = writer.seal(vectors, chunk_ids, embed_model, embed_dims, FIXED_LAYOUT.signature)
    _clear_pending(directory)
    for doc_id in documents:
        record = read_sidecar(root, doc_id)
        if record is not None:
            write_sidecar(root, _with_shard(record, shard_id))
    record_event(
        connection,
        "info",
        "ingest",
        f"shard sealed: {shard_id}",
        f"{manifest.doc_count} documents, {manifest.chunk_count} chunks",
    )
    return manifest


def _process(
    root: Path,
    connection: sqlite3.Connection,
    writer: ShardWriter,
    shard_id: str,
    source: Path,
    client: EmbeddingClient,
    ocr: OcrRunner | None,
    progress: ProgressCallback | None,
) -> JobResult:
    try:
        outcome = receive_document(root, source, connection)
    except IngestError as error:
        return JobResult("", source.name, JOB_FAILED, 0, 0, str(error))
    record = outcome.record
    job_id = queue_document(connection, shard_id, source, record.doc_id, record.pages)
    if job_status(connection, job_id) == JOB_DONE:
        if progress is not None:
            progress(record.orig_name, JOB_SKIPPED)
        return JobResult(record.doc_id, record.orig_name, JOB_SKIPPED, 0, 0, "")
    set_job_status(connection, job_id, JOB_RUNNING)
    if progress is not None:
        progress(record.orig_name, JOB_RUNNING)
    try:
        chunks, gaps = _index_document(
            root, writer, record.doc_id, client, ocr, read_search_settings(connection)
        )
    except EmbeddingError as error:
        set_job_status(connection, job_id, JOB_QUEUED, str(error))
        return JobResult(record.doc_id, record.orig_name, JOB_RETRY, 0, 0, str(error))
    except IngestError as error:
        set_job_status(connection, job_id, JOB_FAILED, str(error))
        return JobResult(record.doc_id, record.orig_name, JOB_FAILED, 0, 0, str(error))
    writer.add_document(record, str(original_path(root, record.doc_id).relative_to(root)))
    set_job_status(connection, job_id, JOB_DONE)
    if progress is not None:
        progress(record.orig_name, JOB_DONE)
    return JobResult(record.doc_id, record.orig_name, JOB_DONE, chunks, gaps, "")


def _index_document(
    root: Path,
    writer: ShardWriter,
    doc_id: str,
    client: EmbeddingClient,
    ocr: OcrRunner | None,
    tuning: SearchSettings | None = None,
) -> tuple[int, int]:
    path = original_path(root, doc_id)
    extraction = extract_document(path, ocr, pending_dir(writer.directory))
    blocks = extraction.blocks
    writer.reset_document(doc_id)
    writer.add_blocks(doc_id, blocks, [anchor(block.page, block.ord) for block in blocks])
    if extraction.unindexable_pages:
        writer.mark_unindexable(doc_id, extraction.unindexable_pages, UNINDEXABLE_REASON)
    sizes = tuning if tuning is not None else SearchSettings()
    chunks = chunk_blocks(blocks, sizes.chunk_min_tokens, sizes.chunk_max_tokens)
    if not chunks:
        _store_vectors(writer.directory, doc_id, [], [])
        return 0, len(extraction.unindexable_pages)
    chunk_ids = writer.add_chunks(doc_id, chunks)
    vectors = client.embed_passages([chunk.text for chunk in chunks])
    _store_vectors(writer.directory, doc_id, chunk_ids, vectors)
    return len(chunks), len(extraction.unindexable_pages)


def _vector_file(directory: Path, doc_id: str) -> Path:
    return pending_dir(directory) / f"{doc_id}{VECTOR_SUFFIX}"


def _store_vectors(
    directory: Path, doc_id: str, chunk_ids: Sequence[int], vectors: Sequence[Sequence[float]]
) -> None:
    pending_dir(directory).mkdir(parents=True, exist_ok=True)
    ids = np.asarray(chunk_ids, dtype=np.int64)
    if len(chunk_ids) == 0:
        matrix = np.zeros((0, 0), dtype=np.float32)
    else:
        matrix = np.asarray(vectors, dtype=np.float32).reshape(len(chunk_ids), -1)
    with _vector_file(directory, doc_id).open("wb") as handle:
        np.savez(handle, ids=ids, vectors=matrix)


def _load_vectors(directory: Path, doc_id: str) -> tuple[list[int], np.ndarray] | None:
    path = _vector_file(directory, doc_id)
    if not path.is_file():
        return None
    with np.load(path) as stored:
        ids = [int(value) for value in stored["ids"]]
        vectors = np.asarray(stored["vectors"], dtype=np.float32)
    return ids, vectors


def _clear_pending(directory: Path) -> None:
    folder = pending_dir(directory)
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        path.unlink(missing_ok=True)
    folder.rmdir()


def _with_shard(record: OriginalRecord, shard_id: str) -> OriginalRecord:
    return replace(record, shard_id=shard_id)


def default_ocr() -> OcrRunner | None:
    return run_ocrmypdf if ocr_available() else None


VENDOR_TEXT_SUFFIXES = (".md", ".rst", ".txt")
VENDOR_MEMBER_MAX_BYTES = 2_000_000
VENDOR_MIME = "application/gzip"
VENDOR_DOC_LIMIT = 40


def vendor_texts(archive: Path) -> list[tuple[str, str]]:
    if not archive.is_file():
        return []
    found: list[tuple[str, str]] = []
    try:
        with tarfile.open(archive, "r:*") as bundle:
            members = sorted(
                (member for member in bundle.getmembers() if _is_documentation(member)),
                key=lambda member: member.name,
            )
            for member in members[:VENDOR_DOC_LIMIT]:
                handle = bundle.extractfile(member)
                if handle is None:
                    continue
                raw = handle.read(VENDOR_MEMBER_MAX_BYTES)
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    found.append((member.name, text))
    except (OSError, tarfile.TarError):
        return []
    return found


def _is_documentation(member: tarfile.TarInfo) -> bool:
    if not member.isfile() or member.size > VENDOR_MEMBER_MAX_BYTES:
        return False
    name = member.name.lower()
    tail = name.rsplit("/", 1)[-1]
    if tail.startswith("readme") or tail.startswith("changelog"):
        return True
    return name.endswith(VENDOR_TEXT_SUFFIXES) and "/docs/" in name


def ingest_vendor(
    root: Path,
    connection: sqlite3.Connection,
    entries: Sequence[VendorSourceRow],
    client: EmbeddingClient,
) -> IngestReport:
    shard_id = new_shard_id()
    directory = shard_path(root, shard_id, KIND_VENDOR)
    writer = ShardWriter(directory, KIND_VENDOR)
    results: list[JobResult] = []
    try:
        for entry in entries:
            results.append(_index_vendor(writer, entry, client))
    finally:
        writer.close()
    if not any(result.status == JOB_DONE for result in results):
        return IngestReport(shard_id, False, None, results)
    manifest = _seal_vendor(root, connection, directory, client)
    return IngestReport(shard_id, manifest is not None, manifest, results)


def _index_vendor(
    writer: ShardWriter, entry: VendorSourceRow, client: EmbeddingClient
) -> JobResult:
    texts = vendor_texts(entry.path)
    if not texts:
        return JobResult(entry.doc_id, entry.name, JOB_FAILED, 0, 0, "no documentation found")
    blocks: list[Block] = []
    for page, (_name, text) in enumerate(texts, start=1):
        for position, paragraph in enumerate(_split_paragraphs(text), start=1):
            blocks.append(Block(page, position, paragraph))
    if not blocks:
        return JobResult(entry.doc_id, entry.name, JOB_FAILED, 0, 0, "documentation is empty")
    record = OriginalRecord(
        doc_id=entry.doc_id,
        path=entry.path,
        orig_name=entry.name,
        mime=VENDOR_MIME,
        size_bytes=entry.path.stat().st_size,
        pages=len(texts),
        title=entry.title,
        imported_at=utc_now_iso(),
        shard_id=writer.directory.name,
        meta={"version": entry.version, "license": entry.license},
    )
    writer.reset_document(entry.doc_id)
    writer.add_document(record, entry.relative_path)
    writer.add_blocks(entry.doc_id, blocks, [anchor(b.page, b.ord) for b in blocks])
    chunks = chunk_blocks(blocks)
    chunk_ids = writer.add_chunks(entry.doc_id, chunks)
    try:
        vectors = client.embed_passages([chunk.text for chunk in chunks])
    except EmbeddingError as error:
        _store_vectors(writer.directory, entry.doc_id, [], [])
        return JobResult(entry.doc_id, entry.name, JOB_RETRY, len(chunks), 0, str(error))
    _store_vectors(writer.directory, entry.doc_id, chunk_ids, vectors)
    return JobResult(entry.doc_id, entry.name, JOB_DONE, len(chunks), 0, "")


def _seal_vendor(
    root: Path, connection: sqlite3.Connection, directory: Path, client: EmbeddingClient
) -> Manifest | None:
    writer = ShardWriter(directory, KIND_VENDOR)
    documents = writer.documents()
    vectors: list[list[float]] = []
    chunk_ids: list[int] = []
    for doc_id in documents:
        stored = _load_vectors(directory, doc_id)
        if stored is None:
            writer.close()
            return None
        ids, matrix = stored
        chunk_ids.extend(ids)
        if len(ids):
            vectors.extend(matrix.tolist())
    manifest = writer.seal(vectors, chunk_ids, client.model, client.dims)
    _clear_pending(directory)
    record_event(
        connection,
        "info",
        "vendor",
        f"vendor shard sealed: {directory.name}",
        f"{manifest.doc_count} packages, {manifest.chunk_count} chunks",
    )
    return manifest


def _split_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.strip())
            continue
        if current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return [paragraph for paragraph in paragraphs if len(paragraph.split()) >= 3]


SCOPE_OUT = "out"
SCOPE_FULL = "full"
SCOPE_CURATED = "curated"
SCOPES = (SCOPE_OUT, SCOPE_FULL, SCOPE_CURATED)
SCOPE_KIND_ZIM = "zim"
SCOPE_KIND_DOCS = "docs"
DEFAULT_DOC_SCOPE = SCOPE_FULL
DEFAULT_ZIM_SCOPE = SCOPE_OUT
TOKENS_PER_SECOND = 1200.0
BYTES_PER_VECTOR = 768


@dataclass(frozen=True)
class ScopeRow:
    source_kind: str
    source_ref: str
    scope: str
    article_count: int


@dataclass(frozen=True)
class ScopeEstimate:
    chunks: int
    seconds: float
    bytes: int


def normalize_scope(value: str | None) -> str:
    return value if value in SCOPES else SCOPE_OUT


def read_scope(
    connection: sqlite3.Connection, source_kind: str, source_ref: str, default: str
) -> ScopeRow:
    row = connection.execute(
        "SELECT scope, article_list FROM deep_index_scope WHERE source_kind = ? AND source_ref = ?",
        (source_kind, source_ref),
    ).fetchone()
    if row is None:
        return ScopeRow(source_kind, source_ref, default, 0)
    return ScopeRow(
        source_kind,
        source_ref,
        normalize_scope(str(row["scope"])),
        len(_titles(str(row["article_list"] or ""))),
    )


def write_scope(
    connection: sqlite3.Connection,
    source_kind: str,
    source_ref: str,
    scope: str,
    article_list: str = "",
) -> ScopeRow:
    titles = _titles(article_list)
    connection.execute(
        "INSERT INTO deep_index_scope (source_kind, source_ref, scope, article_list) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(source_kind, source_ref) DO UPDATE SET "
        "scope = excluded.scope, article_list = excluded.article_list",
        (source_kind, source_ref, normalize_scope(scope), "\n".join(titles)),
    )
    connection.commit()
    return ScopeRow(source_kind, source_ref, normalize_scope(scope), len(titles))


def scope_titles(connection: sqlite3.Connection, source_kind: str, source_ref: str) -> list[str]:
    row = connection.execute(
        "SELECT article_list FROM deep_index_scope WHERE source_kind = ? AND source_ref = ?",
        (source_kind, source_ref),
    ).fetchone()
    return _titles(str(row["article_list"] or "")) if row is not None else []


def estimate_scope(articles: int, tokens_per_article: int = 900) -> ScopeEstimate:
    chunks = max(0, articles) * max(1, tokens_per_article) // MAX_CHUNK_TOKENS
    tokens = chunks * MAX_CHUNK_TOKENS
    return ScopeEstimate(
        chunks=chunks,
        seconds=tokens / TOKENS_PER_SECOND,
        bytes=chunks * BYTES_PER_VECTOR,
    )


def _titles(raw: str) -> list[str]:
    seen: list[str] = []
    for line in raw.splitlines():
        title = line.strip()
        if title and title not in seen:
            seen.append(title)
    return seen
