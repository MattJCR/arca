from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EmbeddingClient
from arca.fetch import file_sha256
from arca.ingest import original_path
from arca.pipeline import ingest_documents
from tests.support import EmbeddingServer, build_pdf
from tests.test_api import http_get, http_headers

DIMS = 8
PUMP = "The pump must be primed before the valve is opened and the gauge is checked. "
SOLAR = "Photovoltaic panels lose efficiency when their surface temperature rises. "


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture
def ready(
    tmp_path: Path, server: EmbeddingServer
) -> Iterator[tuple[Path, FastAPI, str, sqlite3.Connection]]:
    root = tmp_path / "library"
    init_library(root)
    sources = [
        build_pdf(
            tmp_path / "inbox" / "pump.pdf",
            [["Pump chapter. " + PUMP * 4], ["Maintenance. " + PUMP * 4]],
            title="Pump manual",
        ),
        build_pdf(
            tmp_path / "inbox" / "solar.pdf",
            [["Solar chapter. " + SOLAR * 4]],
            title="Solar manual",
        ),
    ]
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    report = ingest_documents(root, connection, sources, client)
    assert report.sealed is True
    doc_id = next(result.doc_id for result in report.results if result.orig_name == "pump.pdf")
    config = ArcaConfig(embed_url=server.url, embed_dims=DIMS)
    app = create_app(root, config)
    yield root, app, doc_id, connection
    connection.close()


def _search(app: FastAPI, query: str, **extra: str) -> dict[str, Any]:
    _, body = http_get(app, "/api/search", query=urlencode({"q": query, **extra}))
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def _items(payload: dict[str, Any], band: str) -> list[dict[str, Any]]:
    values = payload[band]
    assert isinstance(values, list)
    return values


def _documents(payload: dict[str, Any], band: str = "keyword") -> list[dict[str, Any]]:
    return [item for item in _items(payload, band) if item["source_kind"] == "doc"]


def test_document_chunks_appear_in_the_keyword_band(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = ready
    payload = _search(app, "pump")
    documents = _documents(payload)
    assert documents
    first = documents[0]
    assert first["doc_id"] == doc_id
    assert first["anchor"].startswith("p:")
    assert first["page"] >= 1
    assert first["url"].startswith("/api/doc/")
    assert first["original_url"].endswith("#page=" + str(first["page"]))
    assert first["snippet"]


def test_the_semantic_band_answers_with_the_exact_paragraph(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, _, _ = ready
    payload = _search(app, "pump")
    assert payload["semantic_available"] is True
    semantic = _items(payload, "semantic")
    assert semantic
    for item in semantic:
        assert item["source_kind"] == "doc"
        assert item["anchor"].startswith("p:")
        assert item["snippet"]
        assert item["url"].endswith(item["anchor"])


def test_the_keyword_only_mode_leaves_the_semantic_band_alone(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, _, _ = ready
    payload = _search(app, "pump", mode="keyword")
    assert payload["semantic"] == []
    assert payload["keyword"]


def test_the_readable_version_lands_on_the_cited_anchor(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, doc_id, _ = ready
    payload = _search(app, "pump")
    cited = _documents(payload)[0]
    status, body = http_get(app, "/api/doc/" + doc_id)
    assert status == 200
    page = body.decode()
    assert f'id="{cited["anchor"]}"' in page
    assert 'class="seal"' in page
    assert 'id="page:0001"' in page
    assert "/static/selection.js" in page


def test_the_original_is_served_byte_identical(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    root, app, doc_id, _ = ready
    status, body = http_get(app, "/api/doc/" + doc_id + "/original")
    assert status == 200
    stored = original_path(root, doc_id)
    assert body == stored.read_bytes()
    assert file_sha256(stored) == doc_id
    headers = http_headers(app, "/api/doc/" + doc_id + "/original")
    assert headers["content-type"] == "application/pdf"
    assert headers["x-content-type-options"] == "nosniff"
    assert "inline" in headers["content-disposition"]


def test_a_forged_document_identifier_is_refused(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, _, _ = ready
    for forged in ("../../arca.json", "..%2F..%2Farca.json", "not-a-hash", "a" * 63):
        assert http_get(app, "/api/doc/" + forged)[0] == 404
        assert http_get(app, "/api/doc/" + forged + "/original")[0] == 404


def test_an_unknown_but_well_formed_identifier_is_refused(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, _, _ = ready
    unknown = "b" * 64
    status, body = http_get(app, "/api/doc/" + unknown, query="lang=en")
    assert status == 404
    assert "No document with identifier" in body.decode()
    assert http_get(app, "/api/doc/" + unknown + "/original")[0] == 404


def test_the_search_payload_reports_the_sealed_batches(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection],
) -> None:
    _, app, _, _ = ready
    payload = _search(app, "pump")
    documents = payload["documents"]
    assert isinstance(documents, dict)
    assert documents["semantic_ready"] is True
    shards = documents["shards"]
    assert len(shards) == 1
    assert shards[0]["state"] == "sealed"
    assert shards[0]["doc_count"] == 2
    assert shards[0]["chunk_count"] > 0


def test_without_an_embedding_server_the_keyword_band_still_answers(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, _, _, _ = ready
    offline = create_app(root, ArcaConfig(embed_url="http://127.0.0.1:1", embed_dims=DIMS))
    payload = _search(offline, "pump")
    assert payload["keyword"]
    assert payload["semantic"] == []
    assert payload["semantic_available"] is True


def test_documents_and_collections_share_one_result_list(
    ready: tuple[Path, FastAPI, str, sqlite3.Connection], tmp_path: Path
) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root, _, _, _ = ready
    build_zim(
        root / "zim" / "manual.zim",
        [article("pumps.html", "Pumps", "<p>A pump primes itself.</p>")],
        title="Collection",
        main_path="pumps.html",
    )
    library = ZimLibrary(root)
    library.refresh()
    library.close()
    app = create_app(root, ArcaConfig(embed_dims=DIMS))
    payload = _search(app, "pump")
    kinds = {item["source_kind"] for item in _items(payload, "keyword")}
    assert kinds == {"zim", "doc"}
