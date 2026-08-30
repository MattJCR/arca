from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from arca.formats import (
    FIXED_LAYOUT,
    OCR_BINARY,
    OCR_RENDER,
    PDF,
    Block,
    Format,
    IngestError,
    Layout,
    detect_format,
    open_document,
)

TEXT_BLOCK = 0
SOURCE_TEXT = "text"
SOURCE_OCR = "ocr"
SOURCE_NONE = "none"
OCR_TIMEOUT_SECONDS = 300
OCR_RENDER_DPI = 300
RENDER_FAILURES = (IngestError, RuntimeError, ValueError, IndexError, OSError)
RENDER_PAGE = 1
OcrRunner = Callable[[Path, int, Path], str]


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
    layout: Layout | None = None

    @property
    def blocks(self) -> list[Block]:
        return [block for page in self.pages for block in page.blocks]

    @property
    def unindexable_pages(self) -> tuple[int, ...]:
        return tuple(page.number for page in self.pages if not page.indexable)

    @property
    def ocr_pages(self) -> tuple[int, ...]:
        return tuple(page.number for page in self.pages if page.source == SOURCE_OCR)


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


def render_page_to_pdf(source: Path, page: int, destination: Path) -> None:
    document = open_document(source)
    try:
        pixmap = document[page - 1].get_pixmap(dpi=OCR_RENDER_DPI)
    finally:
        document.close()
    image = pymupdf.open("png", pixmap.tobytes("png"))
    try:
        destination.write_bytes(image.convert_to_pdf())
    finally:
        image.close()


def rendered_ocr(runner: OcrRunner, source: Path, page: int, workdir: Path) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    rendered = workdir / f"render-{page:04d}.pdf"
    try:
        render_page_to_pdf(source, page, rendered)
    except RENDER_FAILURES:
        return ""
    try:
        return runner(rendered, RENDER_PAGE, workdir)
    finally:
        rendered.unlink(missing_ok=True)


def _runner_for(entry: Format, ocr: OcrRunner | None) -> OcrRunner | None:
    if ocr is None or not entry.ocr:
        return None
    if entry.ocr != OCR_RENDER:
        return ocr

    def render_first(source: Path, page: int, workdir: Path) -> str:
        return rendered_ocr(ocr, source, page, workdir)

    return render_first


def extract_document(
    path: Path,
    ocr: OcrRunner | None = None,
    workdir: Path | None = None,
    layout: Layout = FIXED_LAYOUT,
) -> Extraction:
    entry = detect_format(path) or PDF
    document = open_document(path, layout)
    pages: list[PageExtraction] = []
    try:
        runner = _runner_for(entry, ocr)
        for index in range(document.page_count):
            pages.append(_extract_page(document, path, index, runner, workdir))
    finally:
        document.close()
    return Extraction(
        doc_id=path.stem,
        pages=tuple(pages),
        layout=layout if entry.reflowable else None,
    )


def _extract_page(
    document: Any, path: Path, index: int, ocr: OcrRunner | None, workdir: Path | None
) -> PageExtraction:
    number = index + 1
    page = document[index]
    texts = _page_texts(page)
    if texts:
        return PageExtraction(number, _blocks(number, texts), SOURCE_TEXT)
    if ocr is not None and workdir is not None:
        recovered = _paragraphs(ocr(path, number, workdir))
        if recovered:
            return PageExtraction(number, _blocks(number, recovered), SOURCE_OCR)
    return PageExtraction(number, (), SOURCE_NONE)


def _page_texts(page: Any) -> list[str]:
    raw = page.get_text("blocks", sort=True)
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
