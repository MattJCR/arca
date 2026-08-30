from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape
from math import ceil
from pathlib import Path

from arca.db import record_event, utc_now_iso
from arca.fetch import file_sha256
from arca.formats import (
    CODE_UNSUPPORTED_FORMAT,
    FORMATS,
    PDF,
    Block,
    IngestError,
    UnreadableDocumentError,
    UnsupportedFormatError,
    detect_format,
    format_for_suffix,
    open_document,
    refusal,
)

ORIGINALS_SUBDIR = ("docs", "originals")
PART_SUFFIX = ".part"
COPY_CHUNK_BYTES = 1024 * 1024
METADATA_FIELDS = ("author", "subject", "keywords", "creator", "producer", "creationDate")
ANCHOR_PAGE_DIGITS = 4
ANCHOR_BLOCK_DIGITS = 2
TOKENS_PER_WORD = 1.4
MIN_CHUNK_TOKENS = 300
MAX_CHUNK_TOKENS = 500
OVERLAP_BLOCKS = 1
DOC_ID_LENGTH = 64
DOC_ID_ALPHABET = "0123456789abcdef"
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class DocumentProbe:
    pages: int
    title: str
    meta: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OriginalRecord:
    doc_id: str
    path: Path
    orig_name: str
    mime: str
    size_bytes: int
    pages: int
    title: str
    imported_at: str
    shard_id: str
    meta: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiveOutcome:
    record: OriginalRecord
    created: bool


@dataclass(frozen=True)
class Chunk:
    anchor: str
    page: int
    ord: int
    text: str
    n_tokens: int


def anchor(page: int, ord: int) -> str:
    return f"p:{page:0{ANCHOR_PAGE_DIGITS}d}:{ord:0{ANCHOR_BLOCK_DIGITS}d}"


def page_anchor(page: int) -> str:
    return f"page:{page:0{ANCHOR_PAGE_DIGITS}d}"


def is_doc_id(value: str) -> bool:
    return len(value) == DOC_ID_LENGTH and all(character in DOC_ID_ALPHABET for character in value)


def render_readable(
    title: str,
    orig_name: str,
    doc_id: str,
    blocks: Sequence[tuple[int, int, str, str]],
    unindexable: Sequence[int] = (),
) -> str:
    missing = set(unindexable)
    pages = sorted({block[0] for block in blocks} | missing)
    parts = [
        "<!doctype html>",
        '<html><head><meta charset="utf-8">',
        f"<title>{escape(title)}</title>",
        '<link rel="stylesheet" href="/static/fonts/fonts.css">',
        '<link rel="stylesheet" href="/static/style.css">',
        '<script src="/static/theme.js"></script>',
        "</head>",
        f'<body class="readable" data-doc="{escape(doc_id)}">',
        f"<h1>{escape(title)}</h1>",
        '<p class="seal"><span class="seal-kind">DOC</span>'
        f'<span class="seal-source">{escape(orig_name)}</span>'
        f'<span class="seal-path">{escape(doc_id[:12])}</span></p>',
    ]
    for page in pages:
        parts.append(f'<section class="page" id="{page_anchor(page)}" data-page="{page}">')
        if page in missing:
            parts.append(f'<p class="detail" data-unindexable="{page}"></p>')
        for block_page, _ord, block_anchor, text in blocks:
            if block_page == page:
                parts.append(f'<p id="{escape(block_anchor)}">{escape(text)}</p>')
        parts.append("</section>")
    parts.append('<script src="/static/selection.js"></script>')
    parts.append("</body></html>")
    return "\n".join(parts)


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return ceil(words * TOKENS_PER_WORD) if words else 0


def split_sentences(text: str) -> list[str]:
    pieces = SENTENCE_PATTERN.split(text)
    return [piece.strip() for piece in pieces if piece.strip()]


def chunk_blocks(
    blocks: Sequence[Block],
    min_tokens: int = MIN_CHUNK_TOKENS,
    max_tokens: int = MAX_CHUNK_TOKENS,
) -> list[Chunk]:
    units = _splittable_units(blocks, max_tokens)
    chunks: list[Chunk] = []
    current: list[Block] = []
    tokens = 0
    pending = False
    for unit in units:
        unit_tokens = estimate_tokens(unit.text)
        if current and tokens + unit_tokens > max_tokens:
            chunks.append(_chunk(len(chunks), current))
            current = _overlap(current, min_tokens)
            tokens = sum(estimate_tokens(block.text) for block in current)
            pending = False
        current.append(unit)
        tokens += unit_tokens
        pending = True
    if current and pending:
        chunks.append(_chunk(len(chunks), current))
    return chunks


def _overlap(current: list[Block], min_tokens: int) -> list[Block]:
    if not OVERLAP_BLOCKS:
        return []
    last = current[-1]
    return [last] if estimate_tokens(last.text) < min_tokens else []


def _splittable_units(blocks: Sequence[Block], max_tokens: int) -> list[Block]:
    units: list[Block] = []
    for block in blocks:
        if estimate_tokens(block.text) <= max_tokens:
            units.append(block)
            continue
        units.extend(_split_block(block, max_tokens))
    return units


def _split_block(block: Block, max_tokens: int) -> list[Block]:
    pieces: list[Block] = []
    current: list[str] = []
    tokens = 0
    for sentence in split_sentences(block.text) or [block.text]:
        sentence_tokens = estimate_tokens(sentence)
        if current and tokens + sentence_tokens > max_tokens:
            pieces.append(Block(block.page, block.ord, " ".join(current)))
            current = []
            tokens = 0
        current.append(sentence)
        tokens += sentence_tokens
    if current:
        pieces.append(Block(block.page, block.ord, " ".join(current)))
    return pieces


def _chunk(ordinal: int, blocks: Sequence[Block]) -> Chunk:
    first = blocks[0]
    text = " ".join(block.text for block in blocks)
    return Chunk(
        anchor=anchor(first.page, first.ord),
        page=first.page,
        ord=ordinal,
        text=text,
        n_tokens=estimate_tokens(text),
    )


def originals_dir(root: Path) -> Path:
    return root.joinpath(*ORIGINALS_SUBDIR)


def original_path(root: Path, doc_id: str, suffix: str = "") -> Path:
    directory = originals_dir(root)
    if suffix:
        return directory / f"{doc_id}{suffix}"
    stored = stored_original(root, doc_id)
    return stored if stored is not None else directory / f"{doc_id}{PDF.suffix}"


def stored_original(root: Path, doc_id: str) -> Path | None:
    directory = originals_dir(root)
    for entry in FORMATS:
        candidate = directory / f"{doc_id}{entry.suffix}"
        if candidate.is_file():
            return candidate
    return None


def sidecar_path(root: Path, doc_id: str) -> Path:
    return originals_dir(root) / f"{doc_id}.json"


def probe_document(path: Path) -> DocumentProbe:
    document = open_document(path)
    try:
        pages = int(document.page_count)
        raw = document.metadata or {}
        title = str(raw.get("title") or "").strip() or path.stem
        meta = {key: str(raw[key]).strip() for key in METADATA_FIELDS if raw.get(key)}
    finally:
        document.close()
    return DocumentProbe(pages=pages, title=title, meta=meta)


def receive_document(
    root: Path, source: Path, connection: sqlite3.Connection | None = None
) -> ReceiveOutcome:
    if not source.is_file():
        raise UnreadableDocumentError(f"not a file: {source}")
    code, suffix = refusal(source)
    if code:
        raise UnsupportedFormatError(f"{code}: {source.name}", code, suffix)
    entry = detect_format(source)
    if entry is None:
        raise UnsupportedFormatError(
            f"{CODE_UNSUPPORTED_FORMAT}: {source.name}", CODE_UNSUPPORTED_FORMAT, source.suffix
        )
    probe = probe_document(source)
    doc_id = file_sha256(source)
    destination = original_path(root, doc_id, entry.suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing(root, doc_id, source)
    if existing is not None:
        return ReceiveOutcome(existing, False)
    _copy_verified(source, destination, doc_id)
    record = OriginalRecord(
        doc_id=doc_id,
        path=destination,
        orig_name=source.name,
        mime=entry.mime,
        size_bytes=destination.stat().st_size,
        pages=probe.pages,
        title=probe.title,
        imported_at=utc_now_iso(),
        shard_id="",
        meta=probe.meta,
    )
    write_sidecar(root, record)
    if connection is not None:
        record_event(connection, "info", "ingest", f"document received: {record.orig_name}", doc_id)
    return ReceiveOutcome(record, True)


def write_sidecar(root: Path, record: OriginalRecord) -> Path:
    path = sidecar_path(root, record.doc_id)
    payload = {
        "doc_id": record.doc_id,
        "orig_name": record.orig_name,
        "mime": record.mime,
        "size_bytes": record.size_bytes,
        "pages": record.pages,
        "title": record.title,
        "imported_at": record.imported_at,
        "shard_id": record.shard_id,
        "meta": record.meta,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return path


def read_sidecar(root: Path, doc_id: str) -> OriginalRecord | None:
    path = sidecar_path(root, doc_id)
    try:
        parsed: object = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    meta = parsed.get("meta")
    return OriginalRecord(
        doc_id=str(parsed.get("doc_id", doc_id)),
        path=original_path(root, doc_id),
        orig_name=str(parsed.get("orig_name", "")),
        mime=str(parsed.get("mime", PDF.mime)),
        size_bytes=_read_int(parsed, "size_bytes"),
        pages=_read_int(parsed, "pages"),
        title=str(parsed.get("title", "")),
        imported_at=str(parsed.get("imported_at", "")),
        shard_id=str(parsed.get("shard_id", "")),
        meta={str(k): str(v) for k, v in meta.items()} if isinstance(meta, dict) else {},
    )


def list_originals(root: Path) -> list[OriginalRecord]:
    directory = originals_dir(root)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.iterdir()):
        if format_for_suffix(path.suffix) is None:
            continue
        record = read_sidecar(root, path.stem)
        if record is not None:
            records.append(record)
    return records


def _existing(root: Path, doc_id: str, source: Path) -> OriginalRecord | None:
    stored = stored_original(root, doc_id)
    if stored is None or stored.stat().st_size != source.stat().st_size:
        return None
    record = read_sidecar(root, doc_id)
    if record is not None:
        return record
    entry = format_for_suffix(stored.suffix) or PDF
    probe = probe_document(stored)
    rebuilt = OriginalRecord(
        doc_id=doc_id,
        path=stored,
        orig_name=source.name,
        mime=entry.mime,
        size_bytes=stored.stat().st_size,
        pages=probe.pages,
        title=probe.title,
        imported_at=utc_now_iso(),
        shard_id="",
        meta=probe.meta,
    )
    write_sidecar(root, rebuilt)
    return rebuilt


def _copy_verified(source: Path, destination: Path, doc_id: str) -> None:
    partial = destination.with_name(destination.name + PART_SUFFIX)
    with source.open("rb") as reader, partial.open("wb") as writer:
        while chunk := reader.read(COPY_CHUNK_BYTES):
            writer.write(chunk)
    if file_sha256(partial) != doc_id:
        partial.unlink(missing_ok=True)
        raise IngestError(f"copy of {source.name} does not match its hash")
    os.replace(partial, destination)


def _read_int(parsed: dict[str, object], key: str) -> int:
    value = parsed.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
