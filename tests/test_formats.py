from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from arca.config import init_library
from arca.formats import (
    CODE_UNSUPPORTED_FORMAT,
    FIXED_LAYOUT,
    PDF,
    XPS,
    Layout,
    UnsupportedFormatError,
    apply_layout,
    detect_format,
    is_reflowable,
    open_document,
)
from arca.ingest import original_path, receive_document
from tests.support import build_epub, build_fb2, build_mobi, build_pdf, build_txt, build_xps

FILLER = "filler words that the layout engine has to spread across pages "
SECTIONS = 12


def _paragraphs(tag: str) -> list[str]:
    return [f"Paragraph {index} of the {tag}. {FILLER * 6}" for index in range(1, SECTIONS + 1)]


def _samples(directory: Path) -> list[Path]:
    return [
        build_epub(directory / "sample.epub", _paragraphs("epub")),
        build_fb2(directory / "sample.fb2", _paragraphs("fb2")),
        build_txt(directory / "sample.txt", _paragraphs("txt")),
        build_mobi(directory / "sample.mobi", _paragraphs("mobi")),
    ]


def _anchors(path: Path, layout: Layout = FIXED_LAYOUT) -> list[tuple[str, str]]:
    document = open_document(path, layout)
    found: list[tuple[str, str]] = []
    try:
        for index in range(document.page_count):
            position = 0
            for block in document[index].get_text("blocks", sort=True):
                if len(block) > 6 and int(block[6]) != 0:
                    continue
                text = " ".join(line.strip() for line in str(block[4]).splitlines() if line.strip())
                if not text:
                    continue
                position += 1
                found.append((f"p:{index + 1:04d}:{position:02d}", text))
    finally:
        document.close()
    return found


def _digest(entries: Sequence[tuple[str, str]]) -> str:
    payload = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_the_fixed_layout_has_a_readable_signature() -> None:
    assert FIXED_LAYOUT.signature == "595x842@11"


def test_a_pdf_is_not_reflowable_and_keeps_its_own_pages(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "manual.pdf", [["One block."], ["Another block."]])
    document = open_document(path)
    try:
        assert not is_reflowable(document)
        assert document.page_count == 2
    finally:
        document.close()


@pytest.mark.parametrize("index", range(4))
def test_reflowable_samples_are_reflowable_before_the_layout(tmp_path: Path, index: int) -> None:
    import pymupdf

    path = _samples(tmp_path)[index]
    document = pymupdf.open(path)
    try:
        assert is_reflowable(document)
    finally:
        document.close()


@pytest.mark.parametrize("index", range(4))
def test_two_passes_over_the_same_file_give_the_same_anchors(tmp_path: Path, index: int) -> None:
    path = _samples(tmp_path)[index]
    first = _anchors(path)
    assert first
    assert _digest(first) == _digest(_anchors(path))


@pytest.mark.parametrize("index", range(4))
def test_the_file_name_does_not_move_a_single_anchor(tmp_path: Path, index: int) -> None:
    path = _samples(tmp_path)[index]
    copy = tmp_path / f"renamed-{path.name}"
    copy.write_bytes(path.read_bytes())
    assert _digest(_anchors(path)) == _digest(_anchors(copy))


def test_a_different_layout_moves_the_anchors(tmp_path: Path) -> None:
    path = _samples(tmp_path)[0]
    narrow = Layout(FIXED_LAYOUT.width, FIXED_LAYOUT.height / 2, FIXED_LAYOUT.fontsize)
    assert _digest(_anchors(path)) != _digest(_anchors(path, narrow))


def test_applying_the_layout_to_a_pdf_changes_nothing(tmp_path: Path) -> None:
    import pymupdf

    path = build_pdf(tmp_path / "manual.pdf", [["One block."], ["Another block."]])
    document = pymupdf.open(path)
    try:
        before = document.page_count
        apply_layout(document)
        assert document.page_count == before
    finally:
        document.close()


def test_the_format_table_recognises_a_pdf_by_suffix_and_magic(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "manual.pdf", [["One block."]])
    assert detect_format(path) is PDF


def test_the_format_table_recognises_an_xps_package(tmp_path: Path) -> None:
    path = build_xps(tmp_path / "manual.xps", [["One block."]])
    assert detect_format(path) is XPS


def test_a_file_that_only_borrows_the_pdf_suffix_is_not_a_format(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"not a pdf at all")
    assert detect_format(path) is None


def test_an_unknown_suffix_is_not_a_format(tmp_path: Path) -> None:
    path = tmp_path / "budget.xlsx"
    path.write_bytes(b"not a document")
    assert detect_format(path) is None


def test_receiving_rejects_a_file_whose_format_is_not_known(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = tmp_path / "inbox" / "budget.xlsx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not a document")
    with pytest.raises(UnsupportedFormatError) as raised:
        receive_document(root, source)
    assert raised.value.code == CODE_UNSUPPORTED_FORMAT


def test_receiving_an_xps_stores_it_under_its_own_suffix(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = build_xps(tmp_path / "inbox" / "manual.xps", [["One block of text."]])
    outcome = receive_document(root, source)
    assert outcome.created is True
    assert outcome.record.mime == XPS.mime
    assert outcome.record.path.suffix == ".xps"
    assert original_path(root, outcome.record.doc_id) == outcome.record.path
