from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library
from arca.ingest import list_originals, original_path
from tests.support import EmbeddingServer, build_cbz_bytes, build_pdf, write_gguf
from tests.test_api import http_get, http_request

DIMS = 8
TEXT = "The pump must be primed before the valve is opened and the gauge is checked. "


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture
def library(tmp_path: Path, server: EmbeddingServer) -> Iterator[tuple[Path, FastAPI]]:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(embed_url=server.url, embed_dims=DIMS)
    yield root, create_app(root, config)


def _pdf(tmp_path: Path, name: str = "manual.pdf") -> bytes:
    return build_pdf(
        tmp_path / "inbox" / name, [["Chapter. " + TEXT * 4]], title="Manual"
    ).read_bytes()


def _upload(app: FastAPI, filename: str, payload: bytes) -> tuple[int, bytes]:
    return http_request(
        app,
        "POST",
        "/api/admin/documents/upload",
        query=f"filename={filename}",
        raw_body=payload,
        content_type="application/pdf",
    )


def test_a_pdf_uploaded_from_the_panel_lands_in_the_library(
    tmp_path: Path, library: tuple[Path, FastAPI]
) -> None:
    root, app = library
    status, body = _upload(app, "manual.pdf", _pdf(tmp_path))
    assert status == 200
    payload = json.loads(body)
    assert payload["created"] is True
    assert payload["orig_name"] == "manual.pdf"
    assert original_path(root, payload["doc_id"]).is_file()
    records = list_originals(root)
    assert [record.orig_name for record in records] == ["manual.pdf"]
    assert records[0].shard_id == ""


def test_the_same_pdf_twice_is_stored_once(tmp_path: Path, library: tuple[Path, FastAPI]) -> None:
    _, app = library
    payload = _pdf(tmp_path)
    first = json.loads(_upload(app, "manual.pdf", payload)[1])
    second = json.loads(_upload(app, "manual.pdf", payload)[1])
    assert first["created"] is True
    assert second["created"] is False
    assert first["doc_id"] == second["doc_id"]


def test_only_a_whitelisted_format_is_accepted(
    tmp_path: Path, library: tuple[Path, FastAPI]
) -> None:
    _, app = library
    accepted = _upload(app, "notes.txt", b"Plain text that carries real words to index.")
    unknown = _upload(app, "sheet.xlsx", b"not a document")
    scanned = _upload(app, "album.tiff", b"not a scan either")
    traversal = _upload(app, "../escape.pdf", _pdf(tmp_path))
    disguised = _upload(app, "fake.pdf", b"not a pdf at all")
    assert accepted[0] == 200
    assert unknown[0] == 400
    assert json.loads(unknown[1])["code"] == "unsupported_format"
    assert scanned[0] == 400
    assert json.loads(scanned[1])["code"] == "unverified_format"
    assert traversal[0] == 400
    assert json.loads(traversal[1])["code"] == "bad_file"
    assert disguised[0] == 400
    assert json.loads(disguised[1])["code"] == "bad_file"


def test_a_comic_is_refused_while_the_ocr_binary_is_missing(
    library: tuple[Path, FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, app = library
    monkeypatch.setattr("arca.formats.ocr_available", lambda: False)
    refused = _upload(app, "story.cbz", build_cbz_bytes(["A page of the comic."]))
    assert refused[0] == 400
    assert json.loads(refused[1])["code"] == "no_ocr_binary"


def test_a_comic_is_taken_in_once_the_ocr_binary_is_there(
    library: tuple[Path, FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, app = library
    monkeypatch.setattr("arca.formats.ocr_available", lambda: True)
    accepted = _upload(app, "story.cbz", build_cbz_bytes(["A page of the comic."]))
    assert accepted[0] == 200
    assert json.loads(accepted[1])["created"] is True


def test_an_upload_leaves_no_staging_file_behind(
    tmp_path: Path, library: tuple[Path, FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, app = library
    staging = tmp_path / "session"
    monkeypatch.setattr("arca.api.session_dir", lambda root, config: staging)
    _upload(app, "manual.pdf", _pdf(tmp_path))
    assert not list(staging.rglob("*.pdf"))


def test_the_panel_reports_what_is_waiting_to_be_indexed(
    tmp_path: Path, library: tuple[Path, FastAPI]
) -> None:
    _, app = library
    _upload(app, "manual.pdf", _pdf(tmp_path))
    payload = json.loads(http_get(app, "/api/admin/documents")[1])
    assert [item["orig_name"] for item in payload["pending"]] == ["manual.pdf"]
    assert payload["documents"] == []


def test_indexing_from_the_panel_seals_the_batch_and_makes_it_searchable(
    tmp_path: Path, library: tuple[Path, FastAPI]
) -> None:
    _, app = library
    _upload(app, "manual.pdf", _pdf(tmp_path))
    status, body = http_request(app, "POST", "/api/admin/documents/index")
    assert status == 200
    report = json.loads(body)
    assert report["sealed"] is True
    assert report["documents"] == 1
    assert report["chunks"] > 0
    payload = json.loads(http_get(app, "/api/admin/documents")[1])
    assert payload["pending"] == []
    assert [item["orig_name"] for item in payload["documents"]] == ["manual.pdf"]
    results = json.loads(http_get(app, "/api/search", query="q=pump")[1])
    assert any(item["source_kind"] == "doc" for item in results["keyword"])


def test_indexing_with_nothing_pending_says_so(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, body = http_request(app, "POST", "/api/admin/documents/index")
    assert status == 200
    payload = json.loads(body)
    assert payload["sealed"] is False
    assert payload["reason"] == "nothing_to_index"


def test_indexing_without_the_embedding_server_keeps_the_documents(
    tmp_path: Path, library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, app = library
    write_gguf(root / "models" / "embed" / "e5.gguf")
    _upload(app, "manual.pdf", _pdf(tmp_path))
    server.healthy = False
    status, body = http_request(app, "POST", "/api/admin/documents/index")
    assert status == 200
    payload = json.loads(body)
    assert payload["sealed"] is False
    assert payload["reason"] == "embeddings_down"
    assert len(list_originals(root)) == 1


def test_indexing_without_any_embedding_model_names_that_cause(
    tmp_path: Path, library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, app = library
    _upload(app, "manual.pdf", _pdf(tmp_path))
    server.healthy = False
    status, body = http_request(app, "POST", "/api/admin/documents/index")
    assert status == 200
    assert json.loads(body)["reason"] == "no_model"
    assert not list((root / "models" / "embed").glob("*.gguf"))
