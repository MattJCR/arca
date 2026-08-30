from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.cli import main
from arca.config import ArcaConfig, config_path, init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EMBED_MODEL, EmbeddingClient
from arca.extract import OcrRunner, extract_document
from arca.fetch import file_sha256
from arca.formats import (
    CBZ,
    CODE_NO_OCR_BINARY,
    EPUB,
    FAMILY_IMAGE,
    FB2,
    FIXED_LAYOUT,
    FORMATS,
    MOBI,
    OCR_BINARY,
    OXPS,
    PDF,
    TXT,
    XPS,
    Format,
    UnsupportedFormatError,
    detect_format,
)
from arca.i18n import load_catalog
from arca.ingest import (
    anchor,
    list_originals,
    original_path,
    read_sidecar,
    receive_document,
)
from arca.pipeline import IngestReport, ingest_documents
from arca.shards import KIND_DOCS, Shard, inspect_shard, read_manifest, shard_path, write_manifest
from tests.support import (
    EmbeddingServer,
    build_cbz,
    build_epub,
    build_fb2,
    build_image,
    build_mobi,
    build_pdf,
    build_txt,
    build_xps,
    snapshot_tree,
    tree_difference,
)
from tests.test_api import http_get, http_headers

DIMS = 8
TURBINE = "The turbine bearing must be greased before the rotor is allowed to spin. "
BLADE = "A cracked blade is replaced, never welded, because the weld fails under load. "
FILLER = "words that the layout engine has to spread across the pages of the book "
BOOK_TITLE = "Field handbook"
REFLOWABLE_SUFFIXES = (EPUB.suffix, MOBI.suffix, FB2.suffix, TXT.suffix)


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture
def library(
    tmp_path: Path, server: EmbeddingServer
) -> Iterator[tuple[Path, FastAPI, str, sqlite3.Connection]]:
    root = tmp_path / "library"
    init_library(root)
    source = build_xps(
        tmp_path / "inbox" / "turbine.xps",
        [["Turbine chapter. " + TURBINE * 3], ["Blade chapter. " + BLADE * 3]],
    )
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    report = ingest_documents(root, connection, [source], client)
    assert report.sealed is True
    doc_id = report.results[0].doc_id
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    yield root, app, doc_id, connection
    connection.close()


def _search(app: FastAPI, query: str) -> dict[str, Any]:
    _, body = http_get(app, "/api/search", query=urlencode({"q": query}))
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def test_an_xps_keeps_its_own_extension_and_mime(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    root, _, doc_id, _ = library
    stored = original_path(root, doc_id)
    assert stored.suffix == XPS.suffix
    assert stored.is_file()
    record = read_sidecar(root, doc_id)
    assert record is not None
    assert record.mime == XPS.mime
    assert record.orig_name == "turbine.xps"


def test_an_xps_answers_the_search_with_its_own_paragraph(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = library
    payload = _search(app, "turbine")
    documents = [item for item in payload["keyword"] if item["source_kind"] == "doc"]
    assert documents
    first = documents[0]
    assert first["doc_id"] == doc_id
    assert first["anchor"].startswith("p:")
    assert "turbine" in first["snippet"].lower()


def test_the_readable_version_of_an_xps_carries_its_anchors(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = library
    status, raw = http_get(app, f"/api/doc/{doc_id}")
    body = raw.decode("utf-8")
    assert status == 200
    assert 'id="p:0001:01"' in body
    assert 'id="p:0002:01"' in body
    boundary = body.index('id="p:0002:01"')
    assert body.index("Turbine chapter") < boundary < body.index("Blade chapter")


def _pages(tag: str, count: int = 14) -> list[str]:
    return [f"Paragraph {index} of the {tag}. " + FILLER * 8 for index in range(1, count + 1)]


def _build_book(directory: Path, suffix: str) -> Path:
    path = directory / f"handbook{suffix}"
    paragraphs = _pages(suffix.lstrip("."))
    if suffix == EPUB.suffix:
        return build_epub(path, paragraphs, title=BOOK_TITLE)
    if suffix == MOBI.suffix:
        return build_mobi(path, paragraphs, title=BOOK_TITLE)
    if suffix == FB2.suffix:
        return build_fb2(path, paragraphs, title=BOOK_TITLE)
    return build_txt(path, paragraphs)


def _ingest(root: Path, source: Path, server: EmbeddingServer) -> tuple[IngestReport, Path]:
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    try:
        report = ingest_documents(root, connection, [source], client)
    finally:
        connection.close()
    return report, shard_path(root, report.shard_id, KIND_DOCS)


def _blocks_of(directory: Path, doc_id: str) -> list[tuple[str, str]]:
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    shard = Shard(state)
    try:
        return [(anchor, text) for _page, _ord, anchor, text in shard.blocks(doc_id)]
    finally:
        shard.close()


@pytest.mark.parametrize("suffix", REFLOWABLE_SUFFIXES)
def test_a_reflowable_book_is_indexed_with_anchors_and_found_by_search(
    tmp_path: Path, server: EmbeddingServer, suffix: str
) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _build_book(tmp_path / "inbox", suffix)
    report, directory = _ingest(root, source, server)
    assert report.sealed is True
    assert report.done == 1
    doc_id = report.results[0].doc_id
    blocks = _blocks_of(directory, doc_id)
    assert blocks
    assert all(anchor.startswith("p:") for anchor, _text in blocks)
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    payload = _search(app, "paragraph")
    documents = [item for item in payload["keyword"] if item["doc_id"] == doc_id]
    assert documents
    assert documents[0]["anchor"].startswith("p:")


@pytest.mark.parametrize("suffix", REFLOWABLE_SUFFIXES)
def test_reimporting_the_same_book_gives_the_same_anchors(
    tmp_path: Path, server: EmbeddingServer, suffix: str
) -> None:
    source = _build_book(tmp_path / "inbox", suffix)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    init_library(first_root)
    init_library(second_root)
    first_report, first_dir = _ingest(first_root, source, server)
    second_report, second_dir = _ingest(second_root, source, server)
    doc_id = first_report.results[0].doc_id
    assert second_report.results[0].doc_id == doc_id
    assert _blocks_of(first_dir, doc_id) == _blocks_of(second_dir, doc_id)


def test_the_batch_records_the_layout_it_was_sealed_with(
    tmp_path: Path, server: EmbeddingServer
) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = _build_book(tmp_path / "inbox", EPUB.suffix)
    report, directory = _ingest(root, source, server)
    assert report.manifest is not None
    assert report.manifest.layout == FIXED_LAYOUT.signature
    assert read_manifest(directory).layout == FIXED_LAYOUT.signature


def test_a_batch_sealed_with_another_layout_is_reported_by_verify(
    tmp_path: Path, server: EmbeddingServer, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    init_library(root)
    config_path(root).write_text(json.dumps({"embed_dims": DIMS}), "utf-8")
    source = _build_book(tmp_path / "inbox", EPUB.suffix)
    _report, directory = _ingest(root, source, server)
    assert main(["--root", str(root), "verify"]) == 0
    assert FIXED_LAYOUT.signature in capsys.readouterr().out
    manifest = read_manifest(directory)
    write_manifest(directory, replace(manifest, layout="400x600@9"))
    assert main(["--root", str(root), "verify"]) == 1
    assert "400x600@9" in capsys.readouterr().out


def test_the_original_of_an_xps_is_served_with_its_own_type(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = library
    headers = http_headers(app, f"/api/doc/{doc_id}/original")
    assert headers["content-type"] == XPS.mime
    assert headers["content-disposition"].startswith("attachment;")
    assert headers["content-disposition"].endswith('.xps"')


def test_the_original_of_an_xps_is_byte_for_byte_what_was_ingested(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    root, app, doc_id, _ = library
    status, body = http_get(app, f"/api/doc/{doc_id}/original")
    assert status == 200
    assert body == original_path(root, doc_id).read_bytes()
    assert file_sha256(original_path(root, doc_id)) == doc_id


def test_a_search_result_for_a_downloadable_original_says_so(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = library
    payload = _search(app, "turbine")
    documents = [item for item in payload["keyword"] if item["doc_id"] == doc_id]
    assert documents
    first = documents[0]
    assert first["original_download"] is True
    assert first["original_url"] == f"/api/doc/{doc_id}/original"


def test_a_pdf_keeps_its_viewer_and_its_page_anchor(
    tmp_path: Path, server: EmbeddingServer
) -> None:
    root = tmp_path / "library"
    init_library(root)
    source = build_pdf(tmp_path / "inbox" / "pump.pdf", [["The pump must be primed. " * 8]])
    report, _directory = _ingest(root, source, server)
    doc_id = report.results[0].doc_id
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    payload = _search(app, "pump")
    first = next(item for item in payload["keyword"] if item["doc_id"] == doc_id)
    assert first["original_download"] is False
    assert first["original_url"].endswith("#page=1")
    headers = http_headers(app, f"/api/doc/{doc_id}/original")
    assert headers["content-type"] == PDF.mime
    assert headers["content-disposition"].startswith("inline;")


def _sample_for(entry: Format, directory: Path) -> Path:
    path = directory / f"sample{entry.suffix}"
    paragraphs = _pages(entry.name)
    if entry is PDF:
        return build_pdf(path, [[paragraph] for paragraph in paragraphs[:3]])
    if entry in (XPS, OXPS):
        return build_xps(path, [[paragraph] for paragraph in paragraphs[:3]])
    if entry is EPUB:
        return build_epub(path, paragraphs, title=BOOK_TITLE)
    if entry is MOBI:
        return build_mobi(path, paragraphs, title=BOOK_TITLE)
    if entry is FB2:
        return build_fb2(path, paragraphs, title=BOOK_TITLE)
    if entry is TXT:
        return build_txt(path, paragraphs)
    if entry is CBZ:
        return build_cbz(path, [f"Scanned page {number} of the cbz." for number in (1, 2)])
    if entry.family == FAMILY_IMAGE:
        return build_image(path, f"Scanned sheet of the {entry.name}.")
    raise AssertionError(f"the whitelist declares {entry.suffix} with no sample to ingest")


def _fake_ocr(source: Path, page: int, workdir: Path) -> str:
    return f"Recovered by OCR from {file_sha256(source)[:12]} page {page}"


def _ocr_for(entry: Format) -> OcrRunner | None:
    return _fake_ocr if entry.needs_ocr else None


@pytest.mark.parametrize("entry", FORMATS, ids=lambda entry: entry.suffix)
def test_every_whitelisted_extension_ingests_a_real_file_with_text(
    tmp_path: Path, entry: Format
) -> None:
    path = _sample_for(entry, tmp_path)
    assert detect_format(path) is entry
    extraction = extract_document(path, _ocr_for(entry), tmp_path / "work")
    assert extraction.blocks, entry.suffix
    assert not extraction.unindexable_pages, entry.suffix


@pytest.mark.parametrize("entry", FORMATS, ids=lambda entry: entry.suffix)
def test_every_whitelisted_extension_is_received_into_the_library(
    tmp_path: Path, entry: Format, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.formats.ocr_available", lambda: True)
    root = tmp_path / "library"
    init_library(root)
    source = _sample_for(entry, tmp_path / "inbox")
    outcome = receive_document(root, source)
    assert outcome.created is True
    assert outcome.record.mime == entry.mime
    assert outcome.record.pages >= 1


def test_importing_a_folder_picks_up_every_whitelisted_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.formats.ocr_available", lambda: True)
    root = tmp_path / "library"
    init_library(root)
    inbox = tmp_path / "inbox"
    for entry in FORMATS:
        _sample_for(entry, inbox)
    assert main(["--root", str(root), "import", str(inbox)]) == 0
    printed = capsys.readouterr().out
    for entry in FORMATS:
        assert f"sample{entry.suffix}" in printed
    assert len(list_originals(root)) == len(FORMATS)


def test_importing_a_spreadsheet_is_refused_with_an_actionable_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    init_library(root)
    sheet = tmp_path / "inbox" / "budget.xlsx"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"not a document")
    assert main(["--root", str(root), "import", str(sheet)]) == 1
    reported = capsys.readouterr().err
    assert "budget.xlsx" in reported
    assert load_catalog("es").text("documents.refusal.unsupported_format") in reported


def test_a_scanned_comic_becomes_blocks_with_anchors_through_ocr(tmp_path: Path) -> None:
    comic = build_cbz(tmp_path / "story.cbz", ["First scanned page.", "Second scanned page."])
    extraction = extract_document(comic, _fake_ocr, tmp_path / "work")
    assert extraction.ocr_pages == (1, 2)
    assert [anchor(block.page, block.ord) for block in extraction.blocks] == [
        "p:0001:01",
        "p:0002:01",
    ]


def test_each_comic_page_is_rendered_apart_before_it_reaches_the_ocr(tmp_path: Path) -> None:
    comic = build_cbz(tmp_path / "story.cbz", ["First scanned page.", "Second scanned page."])
    extraction = extract_document(comic, _fake_ocr, tmp_path / "work")
    texts = [block.text for block in extraction.blocks]
    assert len(set(texts)) == len(texts)


def test_the_render_step_leaves_no_file_behind(tmp_path: Path) -> None:
    comic = build_cbz(tmp_path / "story.cbz", ["Only one page."])
    workdir = tmp_path / "work"
    extract_document(comic, _fake_ocr, workdir)
    assert list(workdir.iterdir()) == []


def test_a_comic_is_refused_while_the_ocr_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.formats.ocr_available", lambda: False)
    root = tmp_path / "library"
    init_library(root)
    comic = build_cbz(tmp_path / "inbox" / "story.cbz", ["A page of the comic."])
    with pytest.raises(UnsupportedFormatError) as raised:
        receive_document(root, comic)
    assert raised.value.code == CODE_NO_OCR_BINARY
    assert OCR_BINARY in load_catalog("en").text(f"documents.refusal.{raised.value.code}")


def test_a_missing_ocr_binary_stops_no_other_ingest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.formats.ocr_available", lambda: False)
    root = tmp_path / "library"
    init_library(root)
    inbox = tmp_path / "inbox"
    build_cbz(inbox / "story.cbz", ["A page of the comic."])
    for entry in (PDF, EPUB, TXT):
        _sample_for(entry, inbox)
    assert main(["--root", str(root), "import", str(inbox)]) == 1
    assert {record.orig_name for record in list_originals(root)} == {
        "sample.pdf",
        "sample.epub",
        "sample.txt",
    }
    reported = capsys.readouterr()
    assert "story.cbz" in reported.err
    assert load_catalog("es").text("documents.refusal.no_ocr_binary") in reported.err


def test_a_folder_scan_never_drops_a_comic_in_silence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.formats.ocr_available", lambda: False)
    root = tmp_path / "library"
    init_library(root)
    inbox = tmp_path / "inbox"
    build_cbz(inbox / "story.cbz", ["A page of the comic."])
    assert main(["--root", str(root), "import", str(inbox)]) == 1
    printed = capsys.readouterr()
    assert "story.cbz" in printed.err
    assert "1 rechazados" in printed.out


def test_serving_a_book_and_its_original_writes_nothing_under_the_root(
    library: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    root, app, doc_id, _ = library
    before = snapshot_tree(root)
    assert http_get(app, f"/api/doc/{doc_id}")[0] == 200
    assert http_get(app, f"/api/doc/{doc_id}/original")[0] == 200
    assert _search(app, "turbine")["keyword"]
    created, removed, changed = tree_difference(before, snapshot_tree(root))
    assert created == []
    assert removed == []
    assert [name for name in changed if not name.startswith("library.db-")] == []


def test_the_same_bytes_under_two_extensions_stay_one_document(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    inbox = tmp_path / "inbox"
    first = build_txt(inbox / "notes.txt", _pages("txt"))
    second = inbox / "notes.fb2"
    second.write_bytes(first.read_bytes())
    original = receive_document(root, first)
    repeat = receive_document(root, second)
    assert original.created is True
    assert repeat.created is False
    assert repeat.record.doc_id == original.record.doc_id
    assert repeat.record.path == original.record.path
    assert len(list_originals(root)) == 1
