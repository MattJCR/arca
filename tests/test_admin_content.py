from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EmbeddingClient
from arca.llama import GgufError, list_models, read_gguf_header, read_model
from arca.pipeline import (
    SCOPE_CURATED,
    SCOPE_FULL,
    SCOPE_KIND_ZIM,
    SCOPE_OUT,
    estimate_scope,
    ingest_documents,
    read_scope,
    write_scope,
)
from tests.support import EmbeddingServer, article, build_pdf, build_zim, write_gguf
from tests.test_api import http_get, http_request

DIMS = 8
BODY = "The pump must be primed before the valve is opened and the gauge is checked. "


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
    source = build_pdf(
        tmp_path / "inbox" / "pump.pdf",
        [["Pump chapter. " + BODY * 4]],
        title="Pump manual",
    )
    connection = open_library_rw(library_db_path(root))
    try:
        report = ingest_documents(
            root,
            connection,
            [source],
            EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0),
        )
    finally:
        connection.close()
    assert report.sealed is True
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    yield root, app


def _json(app: FastAPI, path: str) -> dict[str, Any]:
    status, body = http_get(app, path)
    assert status == 200
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def test_gguf_headers_are_read_from_the_file(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "models" / "chat" / "model.gguf")
    header = read_gguf_header(path)
    assert header["general.architecture"] == "llama"
    assert header["llama.context_length"] == 8192
    model = read_model(path, "chat")
    assert model.architecture == "llama"
    assert model.quantization == "Q4_K_M"
    assert model.context_length == 8192
    assert model.block_count == 32
    assert model.error == ""


def test_a_file_that_is_not_gguf_is_reported_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "models" / "chat" / "broken.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a model at all")
    with pytest.raises(GgufError):
        read_gguf_header(path)
    model = read_model(path, "chat")
    assert model.architecture == ""
    assert "not a gguf file" in model.error


def test_models_are_listed_from_both_folders(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "chat.gguf")
    write_gguf(root / "models" / "embed" / "embed.gguf", architecture="bert", context=512)
    models = {model.name: model for model in list_models(root)}
    assert set(models) == {"chat.gguf", "embed.gguf"}
    assert models["chat.gguf"].kind == "chat"
    assert models["embed.gguf"].kind == "embed"
    assert models["embed.gguf"].context_length == 512


def test_the_documents_dashboard_lists_documents_shards_and_jobs(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    payload = _json(app, "/api/admin/documents")
    assert len(payload["documents"]) == 1
    document = payload["documents"][0]
    assert document["title"] == "Pump manual"
    assert document["readable_url"].startswith("/api/doc/")
    assert document["original_url"].endswith("/original")
    assert document["unindexable_pages"] == []
    assert payload["shards"][0]["state"] == "sealed"
    assert payload["shards"][0]["chunk_count"] > 0
    assert payload["jobs"]
    assert payload["jobs"][0]["status"] == "done"
    assert payload["provisional"] == []
    assert payload["embedding_available"] is True


def test_the_models_dashboard_warns_about_mismatched_batches(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    write_gguf(root / "models" / "chat" / "chat.gguf")
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    payload = _json(app, "/api/admin/models")
    assert payload["active_embed_dims"] == DIMS
    assert [model["name"] for model in payload["models"]] == ["chat.gguf"]
    assert payload["mismatched_shards"] == []
    other = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS + 1))
    warned = _json(other, "/api/admin/models")
    assert len(warned["mismatched_shards"]) == 1
    assert warned["mismatched_shards"][0]["embed_dims"] == DIMS


def test_the_scope_screen_lists_every_source_with_its_estimate(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    build_zim(
        root / "zim" / "wiki.zim",
        [article("a.html", "Alpha", "<p>text</p>")],
        title="Wiki",
        main_path="a.html",
    )
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    payload = _json(app, "/api/admin/scope")
    kinds = {source["source_kind"] for source in payload["sources"]}
    assert kinds == {"docs", "zim"}
    documents = next(s for s in payload["sources"] if s["source_kind"] == "docs")
    assert documents["scope"] == SCOPE_FULL
    assert documents["fixed"] is True
    collection = next(s for s in payload["sources"] if s["source_kind"] == "zim")
    assert collection["scope"] == SCOPE_OUT
    assert collection["estimate"]["chunks"] == 0
    assert payload["scopes"] == ["out", "full", "curated"]


def test_changing_the_scope_is_stored_and_estimated(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    build_zim(
        root / "zim" / "wiki.zim",
        [article("a.html", "Alpha", "<p>text</p>")],
        title="Wiki",
        main_path="a.html",
    )
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    uuid = next(
        source["source_ref"]
        for source in _json(app, "/api/admin/scope")["sources"]
        if source["source_kind"] == "zim"
    )
    status, body = http_request(
        app,
        "PUT",
        "/api/admin/scope",
        json_body={
            "source_kind": SCOPE_KIND_ZIM,
            "source_ref": uuid,
            "scope": SCOPE_CURATED,
            "article_list": "Alpha\nBeta\nAlpha\n\n",
        },
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["scope"] == SCOPE_CURATED
    assert payload["article_count"] == 2
    assert payload["estimate"]["chunks"] > 0
    again = _json(app, "/api/admin/scope")
    stored = next(s for s in again["sources"] if s["source_ref"] == uuid)
    assert stored["scope"] == SCOPE_CURATED
    assert stored["article_count"] == 2


def test_the_scope_endpoint_refuses_nonsense(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    for body in (
        {"source_kind": "nonsense", "source_ref": "x", "scope": SCOPE_FULL},
        {"source_kind": SCOPE_KIND_ZIM, "source_ref": "", "scope": SCOPE_FULL},
        {"source_kind": SCOPE_KIND_ZIM, "source_ref": "x", "scope": "everything"},
    ):
        status, _ = http_request(app, "PUT", "/api/admin/scope", json_body=body)
        assert status == 400


def test_the_scope_defaults_are_the_ones_the_specification_fixes(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    connection = open_library_rw(library_db_path(root))
    try:
        assert read_scope(connection, SCOPE_KIND_ZIM, "u", SCOPE_OUT).scope == SCOPE_OUT
        row = write_scope(connection, SCOPE_KIND_ZIM, "u", SCOPE_CURATED, "One\nTwo\n")
        assert row.article_count == 2
        assert read_scope(connection, SCOPE_KIND_ZIM, "u", SCOPE_OUT).scope == SCOPE_CURATED
    finally:
        connection.close()


def test_the_estimate_grows_with_the_article_count() -> None:
    small = estimate_scope(100)
    large = estimate_scope(10_000)
    assert small.chunks < large.chunks
    assert small.bytes < large.bytes
    assert large.seconds > small.seconds
    assert estimate_scope(0).chunks == 0
