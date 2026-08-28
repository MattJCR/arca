from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from html import escape
from math import ceil
from pathlib import Path

import pymupdf

from arca.db import record_event, utc_now_iso
from arca.fetch import file_sha256

ORIGINALS_SUBDIR = ("docs", "originals")
PDF_MIME = "application/pdf"
PDF_MAGIC = b"%PDF-"
PART_SUFFIX = ".part"
COPY_CHUNK_BYTES = 1024 * 1024
METADATA_FIELDS = ("author", "subject", "keywords", "creator", "producer", "creationDate")
OCR_BINARY = "ocrmypdf"
OCR_TIMEOUT_SECONDS = 300
TEXT_BLOCK = 0
SOURCE_TEXT = "text"
SOURCE_OCR = "ocr"
SOURCE_NONE = "none"
OcrRunner = Callable[[Path, int, Path], str]
ANCHOR_PAGE_DIGITS = 4
ANCHOR_BLOCK_DIGITS = 2
TOKENS_PER_WORD = 1.4
MIN_CHUNK_TOKENS = 300
MAX_CHUNK_TOKENS = 500
OVERLAP_BLOCKS = 1
DOC_ID_LENGTH = 64
DOC_ID_ALPHABET = "0123456789abcdef"
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


class IngestError(RuntimeError):
    pass


class UnreadableDocumentError(IngestError):
    pass


class ProtectedDocumentError(IngestError):
    pass


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


@dataclass(frozen=True)
class Block:
    page: int
    ord: int
    text: str


@dataclass(frozen=True)
class PageExtraction:
    number: int
    blocks: tuple[Block, ...]
    source: str

    @property
    def indexable(self) -> bool:
        return bool(self.blocks)


@dataclass(frozen=True)
class Extraction:
    doc_id: str
    pages: tuple[PageExtraction, ...]

    @property
    def blocks(self) -> list[Block]:
        return [block for page in self.pages for block in page.blocks]

    @property
    def unindexable_pages(self) -> tuple[int, ...]:
        return tuple(page.number for page in self.pages if not page.indexable)

    @property
    def ocr_pages(self) -> tuple[int, ...]:
        return tuple(page.number for page in self.pages if page.source == SOURCE_OCR)


def ocr_available() -> bool:
    return shutil.which(OCR_BINARY) is not None


def run_ocrmypdf(source: Path, page: int, workdir: Path) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    sidecar = workdir / f"page-{page:04d}.txt"
    rendered = workdir / f"page-{page:04d}.pdf"
    command = [
        OCR_BINARY,
        "--force-ocr",
        "--pages",
        str(page),
        "--sidecar",
        str(sidecar),
        str(source),
        str(rendered),
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=OCR_TIMEOUT_SECONDS, check=True)
        return sidecar.read_text("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        sidecar.unlink(missing_ok=True)
        rendered.unlink(missing_ok=True)


def extract_document(
    path: Path, ocr: OcrRunner | None = None, workdir: Path | None = None
) -> Extraction:
    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise UnreadableDocumentError(f"cannot open pdf: {path.name}: {error}") from error
    pages: list[PageExtraction] = []
    try:
        if document.needs_pass:
            raise ProtectedDocumentError(f"pdf is password protected: {path.name}")
        for index in range(document.page_count):
            pages.append(_extract_page(document, path, index, ocr, workdir))
    finally:
        document.close()
    return Extraction(doc_id=path.stem, pages=tuple(pages))


def _extract_page(
    document: object, path: Path, index: int, ocr: OcrRunner | None, workdir: Path | None
) -> PageExtraction:
    number = index + 1
    page = document[index]  # type: ignore[index]
    texts = _page_texts(page)
    if texts:
        return PageExtraction(number, _blocks(number, texts), SOURCE_TEXT)
    if ocr is not None and workdir is not None:
        recovered = _paragraphs(ocr(path, number, workdir))
        if recovered:
            return PageExtraction(number, _blocks(number, recovered), SOURCE_OCR)
    return PageExtraction(number, (), SOURCE_NONE)


def _page_texts(page: object) -> list[str]:
    raw = page.get_text("blocks", sort=True)  # type: ignore[attr-defined]
    texts = []
    for block in raw:
        if len(block) > 6 and int(block[6]) != TEXT_BLOCK:
            continue
        paragraph = _paragraph(str(block[4]))
        if paragraph:
            texts.append(paragraph)
    return texts


def _blocks(page: int, texts: list[str]) -> tuple[Block, ...]:
    return tuple(Block(page, position + 1, text) for position, text in enumerate(texts))


def _paragraph(raw: str) -> str:
    lines = (line.strip() for line in raw.splitlines())
    return " ".join(line for line in lines if line).strip()


def _paragraphs(raw: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in raw.splitlines():
        if line.strip():
            current.append(line)
            continue
        paragraphs.append(_paragraph(" ".join(current)))
        current = []
    paragraphs.append(_paragraph(" ".join(current)))
    return [paragraph for paragraph in paragraphs if paragraph]


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


def readable_html(record: OriginalRecord, extraction: Extraction) -> str:
    blocks = [
        (block.page, block.ord, anchor(block.page, block.ord), block.text)
        for block in extraction.blocks
    ]
    return render_readable(
        record.title, record.orig_name, record.doc_id, blocks, extraction.unindexable_pages
    )


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


def original_path(root: Path, doc_id: str) -> Path:
    return originals_dir(root) / f"{doc_id}.pdf"


def sidecar_path(root: Path, doc_id: str) -> Path:
    return originals_dir(root) / f"{doc_id}.json"


def probe_document(path: Path) -> DocumentProbe:
    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise UnreadableDocumentError(f"cannot open pdf: {path.name}: {error}") from error
    try:
        if document.needs_pass:
            raise ProtectedDocumentError(f"pdf is password protected: {path.name}")
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
    if not _looks_like_pdf(source):
        raise UnreadableDocumentError(f"not a pdf: {source.name}")
    probe = probe_document(source)
    doc_id = file_sha256(source)
    destination = original_path(root, doc_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing(root, doc_id, destination, source)
    if existing is not None:
        return ReceiveOutcome(existing, False)
    _copy_verified(source, destination, doc_id)
    record = OriginalRecord(
        doc_id=doc_id,
        path=destination,
        orig_name=source.name,
        mime=PDF_MIME,
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
        mime=str(parsed.get("mime", PDF_MIME)),
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
    for path in sorted(directory.glob("*.pdf")):
        record = read_sidecar(root, path.stem)
        if record is not None:
            records.append(record)
    return records


def _existing(root: Path, doc_id: str, destination: Path, source: Path) -> OriginalRecord | None:
    if not destination.is_file():
        return None
    if destination.stat().st_size != source.stat().st_size:
        return None
    record = read_sidecar(root, doc_id)
    if record is not None:
        return record
    probe = probe_document(destination)
    rebuilt = OriginalRecord(
        doc_id=doc_id,
        path=destination,
        orig_name=source.name,
        mime=PDF_MIME,
        size_bytes=destination.stat().st_size,
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


def _looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


def _read_int(parsed: dict[str, object], key: str) -> int:
    value = parsed.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
