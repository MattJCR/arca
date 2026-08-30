from __future__ import annotations

import json
import secrets
import sqlite3
import stat
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from arca.embed import EMBED_DIMS, EMBED_MODEL, PASSAGE_PREFIX, QUERY_PREFIX
from arca.fetch import file_sha256
from arca.formats import FIXED_LAYOUT, Block
from arca.ingest import Chunk, OriginalRecord
from arca.vectors import (
    IDS_FILENAME,
    QUERY_SCALE,
    VECTOR_SCALE,
    VECTORS_FILENAME,
    VectorError,
    VectorHit,
    VectorIndex,
    VectorSource,
    write_index,
)

SHARDS_SUBDIR = ("docs", "shards")
VENDOR_SUBDIR = ("docs", "vendor")
TEXT_FILENAME = "text.sqlite"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = 1
KIND_DOCS = "docs"
KIND_VENDOR = "vendor"
STATE_SEALED = "sealed"
STATE_PROVISIONAL = "provisional"
STATE_CORRUPT = "corrupt"
STATE_INCOMPATIBLE = "incompatible"
SHARD_SUFFIX_BYTES = 3
INDEXED_FILES = (TEXT_FILENAME, VECTORS_FILENAME, IDS_FILENAME)

TEXT_SCHEMA = """
CREATE TABLE documents (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT,
    orig_name   TEXT,
    orig_path   TEXT,
    mime        TEXT,
    pages       INTEGER,
    imported_at TEXT,
    meta        TEXT
);

CREATE TABLE blocks (
    doc_id      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    ord         INTEGER NOT NULL,
    anchor      TEXT NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (doc_id, page, ord)
);

CREATE TABLE chunks (
    chunk_id    INTEGER PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    anchor      TEXT NOT NULL,
    page        INTEGER,
    ord         INTEGER,
    text        TEXT NOT NULL,
    n_tokens    INTEGER
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id, ord);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='chunk_id', tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE unindexable_pages (
    doc_id TEXT NOT NULL,
    page   INTEGER NOT NULL,
    reason TEXT,
    PRIMARY KEY (doc_id, page)
);
"""


class ShardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Manifest:
    shard_id: str
    schema_version: int
    kind: str
    sealed_at: str
    embed_model: str
    embed_dims: int
    embed_prefix_query: str
    embed_prefix_passage: str
    vector_scale: float
    query_scale: float
    chunk_count: int
    doc_count: int
    layout: str = ""
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ShardHit:
    chunk_id: int
    doc_id: str
    anchor: str
    page: int
    text: str
    rank: int


@dataclass(frozen=True)
class DocumentRow:
    doc_id: str
    title: str
    orig_name: str
    orig_path: str
    mime: str
    pages: int
    imported_at: str


def shards_dir(root: Path, kind: str = KIND_DOCS) -> Path:
    parts = VENDOR_SUBDIR if kind == KIND_VENDOR else SHARDS_SUBDIR
    return root.joinpath(*parts)


def new_shard_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H%M")
    return f"{stamp}-{secrets.token_hex(SHARD_SUFFIX_BYTES)}"


def shard_path(root: Path, shard_id: str, kind: str = KIND_DOCS) -> Path:
    return shards_dir(root, kind) / shard_id


def list_shard_paths(root: Path) -> list[Path]:
    found: list[Path] = []
    for kind in (KIND_DOCS, KIND_VENDOR):
        directory = shards_dir(root, kind)
        if directory.is_dir():
            found.extend(sorted(path for path in directory.iterdir() if path.is_dir()))
    return found


class ShardWriter:
    def __init__(self, directory: Path, kind: str = KIND_DOCS) -> None:
        self.directory = directory
        self.kind = kind
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / TEXT_FILENAME
        fresh = not self._path.exists()
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = DELETE")
        if fresh:
            self._connection.executescript(TEXT_SCHEMA)
            self._connection.commit()

    def add_document(self, record: OriginalRecord, orig_path: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO documents "
            "(doc_id, title, orig_name, orig_path, mime, pages, imported_at, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.doc_id,
                record.title,
                record.orig_name,
                orig_path,
                record.mime,
                record.pages,
                record.imported_at,
                json.dumps(record.meta, ensure_ascii=False),
            ),
        )
        self._connection.commit()

    def add_blocks(self, doc_id: str, blocks: Sequence[Block], anchors: Sequence[str]) -> None:
        self._connection.executemany(
            "INSERT OR REPLACE INTO blocks (doc_id, page, ord, anchor, text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (doc_id, block.page, block.ord, anchor, block.text)
                for block, anchor in zip(blocks, anchors, strict=True)
            ],
        )
        self._connection.commit()

    def reset_document(self, doc_id: str) -> None:
        for table in ("chunks", "blocks", "unindexable_pages"):
            self._connection.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
        self._connection.commit()

    def add_chunks(self, doc_id: str, chunks: Sequence[Chunk]) -> list[int]:
        ids: list[int] = []
        for chunk in chunks:
            cursor = self._connection.execute(
                "INSERT INTO chunks (doc_id, anchor, page, ord, text, n_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, chunk.anchor, chunk.page, chunk.ord, chunk.text, chunk.n_tokens),
            )
            ids.append(int(cursor.lastrowid or 0))
        self._connection.commit()
        return ids

    def mark_unindexable(self, doc_id: str, pages: Sequence[int], reason: str) -> None:
        self._connection.executemany(
            "INSERT OR REPLACE INTO unindexable_pages (doc_id, page, reason) VALUES (?, ?, ?)",
            [(doc_id, page, reason) for page in pages],
        )
        self._connection.commit()

    def documents(self) -> list[str]:
        rows = self._connection.execute("SELECT doc_id FROM documents ORDER BY doc_id").fetchall()
        return [str(row["doc_id"]) for row in rows]

    def counts(self) -> tuple[int, int]:
        documents = int(self._connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        chunks = int(self._connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return documents, chunks

    def close(self) -> None:
        self._connection.close()

    def seal(
        self,
        vectors: VectorSource,
        chunk_ids: Sequence[int],
        embed_model: str = EMBED_MODEL,
        embed_dims: int = EMBED_DIMS,
        layout: str = "",
        now: datetime | None = None,
    ) -> Manifest:
        self._connection.execute(
            "INSERT INTO chunks_fts(rowid, text) SELECT chunk_id, text FROM chunks"
        )
        self._connection.commit()
        documents, chunks = self.counts()
        self._connection.execute("VACUUM")
        self._connection.close()
        write_index(self.directory, vectors, chunk_ids, embed_dims)
        manifest = Manifest(
            shard_id=self.directory.name,
            schema_version=SCHEMA_VERSION,
            kind=self.kind,
            sealed_at=(now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            embed_model=embed_model,
            embed_dims=embed_dims,
            embed_prefix_query=QUERY_PREFIX,
            embed_prefix_passage=PASSAGE_PREFIX,
            vector_scale=VECTOR_SCALE,
            query_scale=QUERY_SCALE,
            chunk_count=chunks,
            doc_count=documents,
            layout=layout,
            files=_hash_files(self.directory),
        )
        write_manifest(self.directory, manifest)
        _make_read_only(self.directory / TEXT_FILENAME)
        return manifest


def write_manifest(directory: Path, manifest: Manifest) -> Path:
    path = directory / MANIFEST_FILENAME
    payload = {
        "shard_id": manifest.shard_id,
        "schema_version": manifest.schema_version,
        "kind": manifest.kind,
        "sealed_at": manifest.sealed_at,
        "embed_model": manifest.embed_model,
        "embed_dims": manifest.embed_dims,
        "embed_prefix_query": manifest.embed_prefix_query,
        "embed_prefix_passage": manifest.embed_prefix_passage,
        "vector_scale": manifest.vector_scale,
        "query_scale": manifest.query_scale,
        "chunk_count": manifest.chunk_count,
        "doc_count": manifest.doc_count,
        "layout": manifest.layout,
        "files": manifest.files,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return path


def read_manifest(directory: Path) -> Manifest:
    path = directory / MANIFEST_FILENAME
    try:
        parsed: object = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ShardError(f"unreadable manifest in {directory.name}: {error}") from error
    if not isinstance(parsed, dict):
        raise ShardError(f"malformed manifest in {directory.name}")
    files = parsed.get("files")
    return Manifest(
        shard_id=str(parsed.get("shard_id", directory.name)),
        schema_version=_as_int(parsed.get("schema_version")),
        kind=str(parsed.get("kind", KIND_DOCS)),
        sealed_at=str(parsed.get("sealed_at", "")),
        embed_model=str(parsed.get("embed_model", "")),
        embed_dims=_as_int(parsed.get("embed_dims")),
        embed_prefix_query=str(parsed.get("embed_prefix_query", "")),
        embed_prefix_passage=str(parsed.get("embed_prefix_passage", "")),
        vector_scale=_as_float(parsed.get("vector_scale"), VECTOR_SCALE),
        query_scale=_as_float(parsed.get("query_scale"), QUERY_SCALE),
        chunk_count=_as_int(parsed.get("chunk_count")),
        doc_count=_as_int(parsed.get("doc_count")),
        layout=str(parsed.get("layout", "")),
        files={str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else {},
    )


@dataclass(frozen=True)
class ShardState:
    shard_id: str
    path: Path
    state: str
    manifest: Manifest | None
    reason: str

    @property
    def searchable_by_text(self) -> bool:
        return self.state in (STATE_SEALED, STATE_INCOMPATIBLE)

    @property
    def searchable_by_vector(self) -> bool:
        return self.state == STATE_SEALED


def layout_drift(manifest: Manifest, layout: str = FIXED_LAYOUT.signature) -> str:
    if not manifest.layout or manifest.layout == layout:
        return ""
    return (
        f"sealed with the reflowable layout {manifest.layout}, the active layout is {layout}; "
        "re-importing a reflowable document into a new batch will not reproduce these anchors"
    )


def inspect_shard(
    directory: Path, embed_model: str = EMBED_MODEL, embed_dims: int = EMBED_DIMS
) -> ShardState:
    shard_id = directory.name
    if not (directory / MANIFEST_FILENAME).is_file():
        return ShardState(shard_id, directory, STATE_PROVISIONAL, None, "not sealed yet")
    try:
        manifest = read_manifest(directory)
    except ShardError as error:
        return ShardState(shard_id, directory, STATE_CORRUPT, None, str(error))
    mismatch = _hash_mismatch(directory, manifest)
    if mismatch:
        return ShardState(shard_id, directory, STATE_CORRUPT, manifest, mismatch)
    if manifest.embed_model != embed_model or manifest.embed_dims != embed_dims:
        reason = (
            f"sealed with {manifest.embed_model} at {manifest.embed_dims} dimensions, "
            f"active model is {embed_model} at {embed_dims}"
        )
        return ShardState(shard_id, directory, STATE_INCOMPATIBLE, manifest, reason)
    return ShardState(shard_id, directory, STATE_SEALED, manifest, "")


class Shard:
    def __init__(self, state: ShardState) -> None:
        if state.manifest is None:
            raise ShardError(f"shard {state.shard_id} has no manifest")
        self.state = state
        self.manifest = state.manifest
        uri = f"{(state.path / TEXT_FILENAME).resolve().as_uri()}?mode=ro"
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only = ON")
        self._vectors: VectorIndex | None = None
        if state.searchable_by_vector:
            try:
                self._vectors = VectorIndex(state.path, self.manifest.embed_dims)
            except VectorError:
                self._vectors = None

    def _query(self, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, params).fetchall())

    def search_text(self, query: str, limit: int = 10) -> list[ShardHit]:
        text = query.strip()
        if not text or limit <= 0:
            return []
        try:
            rows = self._query(
                "SELECT c.chunk_id, c.doc_id, c.anchor, c.page, c.text "
                "FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY f.rank LIMIT ?",
                (_fts_query(text), limit),
            )
        except sqlite3.Error:
            return []
        return [
            ShardHit(
                chunk_id=int(row["chunk_id"]),
                doc_id=str(row["doc_id"]),
                anchor=str(row["anchor"]),
                page=int(row["page"] or 0),
                text=str(row["text"]),
                rank=position,
            )
            for position, row in enumerate(rows)
        ]

    def search_vectors(self, query: Sequence[float], limit: int = 10) -> list[VectorHit]:
        if self._vectors is None:
            return []
        return self._vectors.search(query, limit)

    def chunk(self, chunk_id: int) -> ShardHit | None:
        rows = self._query(
            "SELECT chunk_id, doc_id, anchor, page, text FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return ShardHit(
            chunk_id=int(row["chunk_id"]),
            doc_id=str(row["doc_id"]),
            anchor=str(row["anchor"]),
            page=int(row["page"] or 0),
            text=str(row["text"]),
            rank=0,
        )

    def document(self, doc_id: str) -> DocumentRow | None:
        rows = self._query(
            "SELECT doc_id, title, orig_name, orig_path, mime, pages, imported_at "
            "FROM documents WHERE doc_id = ?",
            (doc_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return DocumentRow(
            doc_id=str(row["doc_id"]),
            title=str(row["title"] or ""),
            orig_name=str(row["orig_name"] or ""),
            orig_path=str(row["orig_path"] or ""),
            mime=str(row["mime"] or ""),
            pages=int(row["pages"] or 0),
            imported_at=str(row["imported_at"] or ""),
        )

    def documents(self) -> list[DocumentRow]:
        rows = self._query("SELECT doc_id FROM documents ORDER BY title", ())
        found = [self.document(str(row["doc_id"])) for row in rows]
        return [document for document in found if document is not None]

    def blocks(self, doc_id: str) -> list[tuple[int, int, str, str]]:
        rows = self._query(
            "SELECT page, ord, anchor, text FROM blocks WHERE doc_id = ? ORDER BY page, ord",
            (doc_id,),
        )
        return [
            (int(row["page"]), int(row["ord"]), str(row["anchor"]), str(row["text"]))
            for row in rows
        ]

    def unindexable_pages(self, doc_id: str) -> list[int]:
        rows = self._query(
            "SELECT page FROM unindexable_pages WHERE doc_id = ? ORDER BY page", (doc_id,)
        )
        return [int(row["page"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
        if self._vectors is not None:
            self._vectors.close()
            self._vectors = None


def _fts_query(text: str) -> str:
    words = [word for word in "".join(_safe(ch) for ch in text).split() if word]
    if not words:
        return '""'
    return " ".join(f'"{word}"' for word in words)


def _safe(character: str) -> str:
    return character if character.isalnum() or character.isspace() else " "


def _hash_files(directory: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in INDEXED_FILES:
        path = directory / name
        if path.is_file():
            hashes[name] = f"sha256:{file_sha256(path)}"
    return hashes


def _hash_mismatch(directory: Path, manifest: Manifest) -> str:
    for name, expected in manifest.files.items():
        path = directory / name
        if not path.is_file():
            return f"{name} is missing"
        if f"sha256:{file_sha256(path)}" != expected:
            return f"{name} does not match the hash recorded in the manifest"
    return ""


def _make_read_only(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        return


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _as_float(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


class ShardLibrary:
    def __init__(
        self, root: Path, embed_model: str = EMBED_MODEL, embed_dims: int = EMBED_DIMS
    ) -> None:
        self._root = root
        self._embed_model = embed_model
        self._embed_dims = embed_dims
        self._lock = threading.Lock()
        self._states: dict[str, ShardState] = {}
        self._open: dict[str, Shard] = {}
        self._signatures: dict[str, tuple[int, int]] = {}

    def refresh(self) -> list[ShardState]:
        with self._lock:
            self._scan()
            return self._sorted()

    def states(self) -> list[ShardState]:
        with self._lock:
            return self._sorted()

    def shard(self, shard_id: str) -> Shard | None:
        with self._lock:
            if not self._states:
                self._scan()
            state = self._states.get(shard_id)
            if state is None or not state.searchable_by_text:
                return None
            opened = self._open.get(shard_id)
            if opened is None:
                try:
                    opened = Shard(state)
                except (ShardError, sqlite3.Error):
                    return None
                self._open[shard_id] = opened
            return opened

    def searchable(self) -> list[Shard]:
        found: list[Shard] = []
        for state in self.refresh():
            if not state.searchable_by_text:
                continue
            opened = self.shard(state.shard_id)
            if opened is not None:
                found.append(opened)
        return found

    def document_location(self, doc_id: str) -> tuple[Shard, DocumentRow] | None:
        for shard in self.searchable():
            document = shard.document(doc_id)
            if document is not None:
                return shard, document
        return None

    def close(self) -> None:
        with self._lock:
            for shard in self._open.values():
                shard.close()
            self._open.clear()
            self._states.clear()
            self._signatures.clear()

    def _scan(self) -> None:
        seen: dict[str, tuple[int, int]] = {}
        states: dict[str, ShardState] = {}
        for path in list_shard_paths(self._root):
            signature = _manifest_signature(path)
            seen[path.name] = signature
            if self._signatures.get(path.name) == signature and path.name in self._states:
                states[path.name] = self._states[path.name]
                continue
            self._release(path.name)
            states[path.name] = inspect_shard(path, self._embed_model, self._embed_dims)
        for shard_id in list(self._open):
            if shard_id not in states:
                self._release(shard_id)
        self._states = states
        self._signatures = seen

    def _release(self, shard_id: str) -> None:
        opened = self._open.pop(shard_id, None)
        if opened is not None:
            opened.close()

    def _sorted(self) -> list[ShardState]:
        return sorted(self._states.values(), key=lambda state: state.shard_id)


def _manifest_signature(directory: Path) -> tuple[int, int]:
    manifest = directory / MANIFEST_FILENAME
    try:
        info = manifest.stat()
    except OSError:
        return (0, 0)
    return (info.st_mtime_ns, info.st_size)
