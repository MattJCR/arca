from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from arca.config import init_library, library_db_path
from arca.db import open_library_rw, recent_events
from arca.extract import extract_document
from arca.fetch import file_sha256
from arca.formats import (
    Block,
    ProtectedDocumentError,
    UnreadableDocumentError,
    UnsupportedFormatError,
)
from arca.ingest import (
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    anchor,
    chunk_blocks,
    estimate_tokens,
    list_originals,
    original_path,
    page_anchor,
    probe_document,
    read_sidecar,
    receive_document,
    sidecar_path,
    split_sentences,
)
from tests.support import blank_pdf, build_pdf, readable_html

PAGES = [
    ["First paragraph of the manual.", "A clearly different second block."],
    ["Page two, first block."],
]
ENCRYPT_AES_256 = 5


def _source(tmp_path: Path, name: str = "manual.pdf", title: str = "", author: str = "") -> Path:
    return build_pdf(tmp_path / "inbox" / name, PAGES, title=title, author=author)


def _originals(root: Path) -> list[Path]:
    return sorted((root / "docs" / "originals").glob("*.pdf"))


def test_probe_reads_pages_and_metadata(tmp_path: Path) -> None:
    probe = probe_document(_source(tmp_path, title="Pump manual", author="Ana"))
    assert probe.pages == 2
    assert probe.title == "Pump manual"
    assert probe.meta["author"] == "Ana"


def test_probe_falls_back_to_the_file_name_without_a_title(tmp_path: Path) -> None:
    assert probe_document(_source(tmp_path, "sin-titulo.pdf")).title == "sin-titulo"


def test_receiving_stores_the_original_under_its_own_hash(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path, title="Manual")
    digest = file_sha256(source)
    outcome = receive_document(root, source)
    assert outcome.created is True
    record = outcome.record
    assert record.doc_id == digest
    assert record.path == original_path(root, digest)
    assert record.path.is_file()
    assert record.orig_name == "manual.pdf"
    assert record.title == "Manual"
    assert record.pages == 2
    assert record.mime == "application/pdf"
    assert record.shard_id == ""


def test_the_stored_original_is_byte_identical_to_the_source(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path)
    before = source.read_bytes()
    record = receive_document(root, source).record
    assert record.path.read_bytes() == before
    assert file_sha256(record.path) == record.doc_id
    assert source.read_bytes() == before
    assert file_sha256(source) == record.doc_id


def test_reimporting_the_same_document_does_not_duplicate_it(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path)
    first = receive_document(root, source)
    second = receive_document(root, source)
    assert first.created is True
    assert second.created is False
    assert second.record.doc_id == first.record.doc_id
    assert len(_originals(root)) == 1


def test_the_same_content_under_another_name_is_still_one_document(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    first = _source(tmp_path, "manual.pdf")
    second = tmp_path / "inbox" / "copia.pdf"
    second.write_bytes(first.read_bytes())
    assert receive_document(root, first).created is True
    assert receive_document(root, second).created is False
    assert len(_originals(root)) == 1


def test_the_sidecar_records_what_the_pdf_does_not(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    record = receive_document(root, _source(tmp_path, title="Manual", author="Ana")).record
    payload = json.loads(sidecar_path(root, record.doc_id).read_text("utf-8"))
    assert payload["orig_name"] == "manual.pdf"
    assert payload["doc_id"] == record.doc_id
    assert payload["pages"] == 2
    assert payload["shard_id"] == ""
    assert payload["meta"]["author"] == "Ana"
    assert payload["imported_at"].endswith("Z")


def test_a_lost_sidecar_is_rebuilt_on_the_next_import(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path)
    record = receive_document(root, source).record
    sidecar_path(root, record.doc_id).unlink()
    outcome = receive_document(root, source)
    assert outcome.created is False
    assert sidecar_path(root, record.doc_id).is_file()
    assert read_sidecar(root, record.doc_id) is not None


def test_a_truncated_stored_original_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path)
    record = receive_document(root, source).record
    record.path.write_bytes(b"%PDF-1.7 truncated")
    outcome = receive_document(root, source)
    assert outcome.created is True
    assert file_sha256(record.path) == record.doc_id


def test_receiving_rejects_a_format_that_is_not_on_the_list(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    intruder = tmp_path / "inbox" / "notes.rtf"
    intruder.parent.mkdir(parents=True, exist_ok=True)
    intruder.write_text("plain text", "utf-8")
    with pytest.raises(UnsupportedFormatError):
        receive_document(root, intruder)
    with pytest.raises(UnreadableDocumentError):
        receive_document(root, tmp_path / "inbox" / "missing.pdf")
    assert _originals(root) == []


def test_receiving_rejects_a_pdf_that_cannot_be_opened(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    broken = tmp_path / "inbox" / "broken.pdf"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"%PDF-1.7\nthis is not a real pdf body")
    with pytest.raises(UnreadableDocumentError):
        receive_document(root, broken)
    assert _originals(root) == []


def test_receiving_rejects_a_password_protected_pdf(tmp_path: Path) -> None:
    import pymupdf

    root = tmp_path / "library"
    init_library(root)
    source = _source(tmp_path)
    protected = tmp_path / "inbox" / "locked.pdf"
    document = pymupdf.open(source)
    document.save(
        protected,
        encryption=ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    document.close()
    with pytest.raises(ProtectedDocumentError):
        receive_document(root, protected)
    assert _originals(root) == []


def test_an_empty_pdf_without_text_is_still_accepted(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    outcome = receive_document(root, blank_pdf(tmp_path / "inbox" / "scan.pdf", pages=3))
    assert outcome.created is True
    assert outcome.record.pages == 3


def test_receiving_records_an_event_when_given_a_connection(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    connection = open_library_rw(library_db_path(root))
    try:
        receive_document(root, _source(tmp_path), connection)
        messages = [str(row["message"]) for row in recent_events(connection)]
    finally:
        connection.close()
    assert any("document received" in message for message in messages)


def test_listing_originals_reads_them_back(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    first = receive_document(root, _source(tmp_path, "a.pdf", title="Alpha")).record
    second_source = build_pdf(tmp_path / "inbox" / "b.pdf", [["Another, different document."]])
    second = receive_document(root, second_source).record
    titles = {record.doc_id: record.title for record in list_originals(root)}
    assert titles == {first.doc_id: "Alpha", second.doc_id: "b"}


def test_listing_originals_is_empty_on_a_fresh_library(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    assert list_originals(root) == []


def test_extraction_numbers_pages_and_blocks_from_one(tmp_path: Path) -> None:
    extraction = extract_document(_source(tmp_path))
    assert [page.number for page in extraction.pages] == [1, 2]
    assert [(b.page, b.ord, b.text) for b in extraction.blocks] == [
        (1, 1, "First paragraph of the manual."),
        (1, 2, "A clearly different second block."),
        (2, 1, "Page two, first block."),
    ]
    assert extraction.unindexable_pages == ()
    assert extraction.ocr_pages == ()
    assert all(page.source == "text" for page in extraction.pages)


def test_a_page_without_a_text_layer_is_marked_not_indexable(tmp_path: Path) -> None:
    extraction = extract_document(blank_pdf(tmp_path / "scan.pdf", pages=2))
    assert extraction.unindexable_pages == (1, 2)
    assert extraction.blocks == []
    assert all(page.source == "none" for page in extraction.pages)


def test_ocr_recovers_a_page_when_a_runner_is_available(tmp_path: Path) -> None:
    def runner(path: Path, page: int, workdir: Path) -> str:
        return "Recovered line one" + chr(10) + "same paragraph" + chr(10) * 2 + "Second one."

    extraction = extract_document(
        blank_pdf(tmp_path / "scan.pdf", pages=1), ocr=runner, workdir=tmp_path / "work"
    )
    assert extraction.unindexable_pages == ()
    assert extraction.ocr_pages == (1,)
    assert [block.text for block in extraction.blocks] == [
        "Recovered line one same paragraph",
        "Second one.",
    ]


def test_a_silent_ocr_failure_leaves_the_page_marked_not_indexable(tmp_path: Path) -> None:
    def runner(path: Path, page: int, workdir: Path) -> str:
        return "   "

    extraction = extract_document(
        blank_pdf(tmp_path / "scan.pdf", pages=1), ocr=runner, workdir=tmp_path / "work"
    )
    assert extraction.unindexable_pages == (1,)
    assert extraction.pages[0].source == "none"


def test_extraction_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 nope")
    with pytest.raises(UnreadableDocumentError):
        extract_document(broken)


def test_anchor_format_is_zero_filled(tmp_path: Path) -> None:
    assert anchor(7, 3) == "p:0007:03"
    assert anchor(1, 1) == "p:0001:01"
    assert anchor(1234, 56) == "p:1234:56"
    assert page_anchor(7) == "page:0007"


def test_anchors_are_identical_across_reimports(tmp_path: Path) -> None:
    source = _source(tmp_path, title="Manual")
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    init_library(first_root)
    init_library(second_root)
    first = receive_document(first_root, source).record
    second = receive_document(second_root, source).record
    assert first.doc_id == second.doc_id
    left = extract_document(first.path)
    right = extract_document(second.path)
    assert [anchor(b.page, b.ord) for b in left.blocks] == [
        anchor(b.page, b.ord) for b in right.blocks
    ]
    assert readable_html(first, left) == readable_html(second, right)


def test_the_readable_version_anchors_every_block(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    record = receive_document(root, _source(tmp_path, title="Manual")).record
    extraction = extract_document(record.path)
    html = readable_html(record, extraction)
    assert html.startswith("<!doctype html>")
    assert "<title>Manual</title>" in html
    for block in extraction.blocks:
        assert f'<p id="{anchor(block.page, block.ord)}">{block.text}</p>' in html
    assert 'id="page:0001"' in html
    assert 'id="page:0002"' in html


def test_the_readable_version_escapes_the_document_text(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = build_pdf(
        tmp_path / "inbox" / "x.pdf",
        [["A block with <script>alert(1)</script> inside it."]],
        title="Tricky <b>title</b>",
    )
    record = receive_document(root, source).record
    html = readable_html(record, extract_document(record.path))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>title</b>" not in html


def test_chunks_stay_inside_the_token_range(tmp_path: Path) -> None:
    sentence = "The pump must be primed before the valve is opened and the gauge checked. "
    blocks = [Block(1 + i // 4, 1 + i % 4, f"Paragraph {i}. " + sentence * 6) for i in range(12)]
    chunks = chunk_blocks(blocks)
    assert len(chunks) > 1
    assert all(chunk.n_tokens <= MAX_CHUNK_TOKENS for chunk in chunks)
    assert all(chunk.n_tokens >= MIN_CHUNK_TOKENS for chunk in chunks[:-1])
    assert [chunk.ord for chunk in chunks] == list(range(len(chunks)))


def test_chunks_overlap_by_one_paragraph(tmp_path: Path) -> None:
    sentence = "The pump must be primed before the valve is opened and the gauge checked. "
    blocks = [Block(1, 1 + i, f"Paragraph {i}. " + sentence * 6) for i in range(6)]
    chunks = chunk_blocks(blocks)
    assert len(chunks) >= 2
    for previous, following in pairwise(chunks):
        tail = previous.text.rsplit("Paragraph ", 1)[-1][:12]
        assert tail in following.text


def test_an_oversized_paragraph_is_split_between_sentences(tmp_path: Path) -> None:
    sentence = "The pump must be primed before the valve is opened and the gauge checked. "
    chunks = chunk_blocks([Block(1, 1, sentence * 60)])
    assert len(chunks) > 1
    assert all(chunk.n_tokens <= MAX_CHUNK_TOKENS for chunk in chunks)
    assert all(chunk.text.rstrip().endswith(".") for chunk in chunks)
    assert all(chunk.anchor == "p:0001:01" for chunk in chunks)


def test_a_chunk_points_at_the_block_it_starts_from(tmp_path: Path) -> None:
    chunks = chunk_blocks([Block(7, 3, "A short paragraph."), Block(7, 4, "Another one.")])
    assert len(chunks) == 1
    assert chunks[0].anchor == "p:0007:03"
    assert chunks[0].page == 7
    assert chunks[0].text == "A short paragraph. Another one."


def test_chunking_nothing_produces_nothing() -> None:
    assert chunk_blocks([]) == []


def test_token_estimation_scales_with_words() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 5
    assert estimate_tokens("word " * 100) == 140


def test_sentences_split_on_terminators_only() -> None:
    assert split_sentences("One. Two! Three? Four") == ["One.", "Two!", "Three?", "Four"]
    assert split_sentences("No terminator here") == ["No terminator here"]
    assert split_sentences("   ") == []
