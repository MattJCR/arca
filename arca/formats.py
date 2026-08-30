from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

LAYOUT_WIDTH = 595.0
LAYOUT_HEIGHT = 842.0
LAYOUT_FONTSIZE = 11.0

FAMILY_PAGED = "paged"
FAMILY_REFLOWABLE = "reflowable"
FAMILY_IMAGE = "image"

PDF_MIME = "application/pdf"
ZIP_MAGIC = b"PK\x03\x04"
MOBI_MAGIC = b"BOOKMOBI"
MOBI_MAGIC_OFFSET = 60
MAGIC_READ_BYTES = 8
PNG_MAGIC = bytes([0x89]) + b"PNG"
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
PPM_MAGIC = (b"P6", b"P5", b"P3")

OCR_NONE = ""
OCR_DIRECT = "direct"
OCR_RENDER = "render"
OCR_BINARY = "ocrmypdf"


def ocr_available() -> bool:
    return shutil.which(OCR_BINARY) is not None


class IngestError(RuntimeError):
    pass


class UnreadableDocumentError(IngestError):
    pass


class ProtectedDocumentError(IngestError):
    pass


class UnsupportedFormatError(IngestError):
    def __init__(self, message: str, code: str, suffix: str) -> None:
        super().__init__(message)
        self.code = code
        self.suffix = suffix


@dataclass(frozen=True)
class Block:
    page: int
    ord: int
    text: str


@dataclass(frozen=True)
class Layout:
    width: float
    height: float
    fontsize: float

    @property
    def signature(self) -> str:
        return f"{self.width:g}x{self.height:g}@{self.fontsize:g}"


FIXED_LAYOUT = Layout(LAYOUT_WIDTH, LAYOUT_HEIGHT, LAYOUT_FONTSIZE)


@dataclass(frozen=True)
class Format:
    suffix: str
    mime: str
    family: str
    magic: tuple[bytes, ...] = ()
    magic_offset: int = 0
    ocr: str = OCR_NONE

    @property
    def name(self) -> str:
        return self.suffix.lstrip(".")

    @property
    def reflowable(self) -> bool:
        return self.family == FAMILY_REFLOWABLE

    @property
    def needs_ocr(self) -> bool:
        return self.ocr == OCR_RENDER


PDF = Format(".pdf", PDF_MIME, FAMILY_PAGED, (b"%PDF-",), ocr=OCR_DIRECT)
XPS = Format(".xps", "application/vnd.ms-xpsdocument", FAMILY_PAGED, (ZIP_MAGIC,))
OXPS = Format(".oxps", "application/oxps", FAMILY_PAGED, (ZIP_MAGIC,))
EPUB = Format(".epub", "application/epub+zip", FAMILY_REFLOWABLE, (ZIP_MAGIC,))
MOBI = Format(
    ".mobi",
    "application/x-mobipocket-ebook",
    FAMILY_REFLOWABLE,
    (MOBI_MAGIC,),
    magic_offset=MOBI_MAGIC_OFFSET,
)
FB2 = Format(".fb2", "application/x-fictionbook+xml", FAMILY_REFLOWABLE)
TXT = Format(".txt", "text/plain", FAMILY_REFLOWABLE)

CBZ = Format(".cbz", "application/vnd.comicbook+zip", FAMILY_IMAGE, (ZIP_MAGIC,), ocr=OCR_RENDER)
PNG = Format(".png", "image/png", FAMILY_IMAGE, (PNG_MAGIC,), ocr=OCR_RENDER)
JPG = Format(".jpg", "image/jpeg", FAMILY_IMAGE, (JPEG_MAGIC,), ocr=OCR_RENDER)
JPEG = Format(".jpeg", "image/jpeg", FAMILY_IMAGE, (JPEG_MAGIC,), ocr=OCR_RENDER)
PPM = Format(".ppm", "image/x-portable-pixmap", FAMILY_IMAGE, PPM_MAGIC, ocr=OCR_RENDER)

FORMATS: tuple[Format, ...] = (PDF, XPS, OXPS, EPUB, MOBI, FB2, TXT, CBZ, PNG, JPG, JPEG, PPM)

CODE_BAD_FILE = "bad_file"
CODE_NO_OCR_BINARY = "no_ocr_binary"
CODE_UNVERIFIED_FORMAT = "unverified_format"
CODE_UNSUPPORTED_FORMAT = "unsupported_format"

UNVERIFIED_SUFFIXES = (".azw3", ".tif", ".tiff", ".gif", ".bmp", ".jp2", ".psd")
REFUSAL_CODES = (
    CODE_BAD_FILE,
    CODE_NO_OCR_BINARY,
    CODE_UNVERIFIED_FORMAT,
    CODE_UNSUPPORTED_FORMAT,
)


def known_suffixes() -> tuple[str, ...]:
    return tuple(entry.suffix for entry in FORMATS)


def accepted_suffixes() -> tuple[str, ...]:
    ocr = ocr_available()
    return tuple(entry.suffix for entry in FORMATS if ocr or not entry.needs_ocr)


def refusal_by_name(name: str) -> str:
    suffix = Path(name).suffix.lower()
    entry = format_for_suffix(suffix)
    if entry is None:
        return CODE_UNVERIFIED_FORMAT if suffix in UNVERIFIED_SUFFIXES else CODE_UNSUPPORTED_FORMAT
    return CODE_NO_OCR_BINARY if entry.needs_ocr and not ocr_available() else ""


def refusal(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    code = refusal_by_name(path.name)
    if code:
        return code, suffix
    entry = format_for_suffix(suffix)
    if entry is not None and not matches_magic(path, entry):
        return CODE_BAD_FILE, suffix
    return "", ""


def format_for_suffix(suffix: str) -> Format | None:
    lowered = suffix.lower()
    return next((entry for entry in FORMATS if entry.suffix == lowered), None)


def format_for_mime(mime: str) -> Format | None:
    return next((entry for entry in FORMATS if entry.mime == mime), None)


def matches_magic(path: Path, entry: Format) -> bool:
    if not entry.magic:
        return True
    width = entry.magic_offset + max(MAGIC_READ_BYTES, max(len(prefix) for prefix in entry.magic))
    try:
        with path.open("rb") as handle:
            head = handle.read(width)[entry.magic_offset :]
    except OSError:
        return False
    return any(head.startswith(prefix) for prefix in entry.magic)


def detect_format(path: Path) -> Format | None:
    entry = format_for_suffix(path.suffix)
    if entry is None or not matches_magic(path, entry):
        return None
    return entry


def is_reflowable(document: Any) -> bool:
    return bool(document.is_reflowable)


def apply_layout(document: Any, layout: Layout = FIXED_LAYOUT) -> Any:
    if is_reflowable(document):
        document.layout(
            rect=pymupdf.Rect(0.0, 0.0, layout.width, layout.height), fontsize=layout.fontsize
        )
    return document


def open_document(path: Path, layout: Layout = FIXED_LAYOUT) -> Any:
    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise UnreadableDocumentError(f"cannot open document: {path.name}: {error}") from error
    try:
        apply_layout(document, layout)
    except Exception as error:
        document.close()
        raise UnreadableDocumentError(f"cannot lay out document: {path.name}: {error}") from error
    if document.needs_pass:
        document.close()
        raise ProtectedDocumentError(f"document is password protected: {path.name}")
    return document
