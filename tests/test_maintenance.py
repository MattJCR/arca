from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_ro, open_library_rw
from arca.diagnostics import (
    KIND_DATABASE,
    KIND_DOCUMENT,
    KIND_SHARD,
    KIND_ZIM,
    verify_integrity,
)
from arca.embed import EMBED_CONTEXT_TOKENS, EmbeddingClient
from arca.fetch import write_sha256_sidecar
from arca.ingest import original_path
from arca.pipeline import ingest_documents
from arca.search import (
    SearchResult,
    SearchSettings,
    interleave,
    read_search_payload,
    read_search_settings,
    write_search_settings,
)
from arca.zim import MAX_LIMIT
from tests.support import EmbeddingServer, article, build_pdf, build_zim
from tests.test_api import http_get, http_request

DIMS = 8
BODY = "The feed pump must be primed before the discharge valve is opened and checked. "


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
    build_zim(
        root / "zim" / "manual.zim",
        [article("page.html", "Pump page", "<p>photosynthesis</p>")],
        title="Manual",
        main_path="page.html",
    )
    source = build_pdf(tmp_path / "inbox" / "pump.pdf", [[BODY * 4]], title="Pump manual")
    connection = open_library_rw(library_db_path(root))
    try:
        report = ingest_documents(
            root,
            connection,
            [source],
            EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0),
        )
        connection.execute(
            "INSERT INTO zim_files (uuid, book_name, filename, sha256, status) "
            "VALUES ('u1', 'manual', 'manual.zim', '', 'present')"
        )
        connection.commit()
    finally:
        connection.close()
    assert report.sealed
    app = create_app(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    yield root, app


def _json(app: FastAPI, path: str) -> dict[str, Any]:
    status, body = http_get(app, path)
    assert status == 200
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def _items(root: Path, config: ArcaConfig) -> list[tuple[str, str, str]]:
    connection = open_library_rw(library_db_path(root))
    try:
        return [
            (item.kind, item.name, item.result)
            for item in verify_integrity(root, config, connection)
        ]
    finally:
        connection.close()


def test_the_whole_library_verification_reports_element_by_element(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    config = ArcaConfig(embed_url=server.url, embed_dims=DIMS)
    items = _items(root, config)
    kinds = {kind for kind, _, _ in items}
    assert KIND_ZIM in kinds
    assert KIND_SHARD in kinds
    assert KIND_DATABASE in kinds
    assert ("zim", "manual.zim", "no_hash") in items
    assert all(result == "ok" for kind, _, result in items if kind == KIND_SHARD)


def test_a_corrupt_zim_is_named_by_the_verification(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    path = root / "zim" / "manual.zim"
    write_sha256_sidecar(path, "0" * 64)
    items = _items(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    assert ("zim", "manual.zim", "corrupt") in items


def test_a_missing_original_is_named_by_the_verification(
    library: tuple[Path, FastAPI], server: EmbeddingServer
) -> None:
    root, _ = library
    for original in (root / "docs" / "originals").glob("*.pdf"):
        original.unlink()
    items = _items(root, ArcaConfig(embed_url=server.url, embed_dims=DIMS))
    assert any(kind == KIND_DOCUMENT and result == "missing" for kind, _, result in items)


def test_the_verification_endpoint_counts_by_result(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, body = http_request(app, "POST", "/api/admin/maintenance/verify")
    assert status == 200
    payload = json.loads(body)
    assert payload["items"]
    assert sum(payload["counts"].values()) == len(payload["items"])
    assert {"kind", "name", "result", "detail"} == set(payload["items"][0])


def test_a_document_original_survives_the_verification(library: tuple[Path, FastAPI]) -> None:
    root, app = library
    originals = sorted((root / "docs" / "originals").glob("*.pdf"))
    before = originals[0].read_bytes()
    http_request(app, "POST", "/api/admin/maintenance/verify")
    assert originals[0].read_bytes() == before
    assert original_path(root, originals[0].stem) == originals[0]


def test_maintenance_reports_paths_logs_and_rotation(library: tuple[Path, FastAPI]) -> None:
    root, app = library
    payload = _json(app, "/api/admin/maintenance")
    assert payload["paths"]["root"] == str(root)
    assert payload["paths"]["database"].endswith("library.db")
    assert str(root) not in payload["paths"]["session"]
    assert payload["rotation"]["backups"] >= 1


def test_the_configuration_is_exported_and_imported_back(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    exported = _json(app, "/api/admin/maintenance/config")
    assert exported["config"]["language"] == "es"
    assert exported["settings"]["search"]["per_source"] > 0
    exported["settings"]["search"]["per_source"] = 3
    exported["settings"]["chat"]["max_chunks"] = 2
    status, body = http_request(
        app, "PUT", "/api/admin/maintenance/config", json_body={"settings": exported["settings"]}
    )
    assert status == 200
    stored = json.loads(body)
    assert stored["search"]["per_source"] == 3
    assert stored["chat"]["max_chunks"] == 2
    assert http_request(app, "PUT", "/api/admin/maintenance/config", json_body={})[0] == 400


def test_search_settings_round_trip_and_stay_within_bounds(tmp_path: Path) -> None:
    connection = open_library_rw(library_db_path(tmp_path))
    try:
        stored = write_search_settings(
            connection,
            read_search_payload(
                {
                    "per_source": 9999,
                    "zim_weight": 99,
                    "doc_weight": 0,
                    "snippet_chars": 5,
                    "chunk_min_tokens": 480,
                    "chunk_max_tokens": 200,
                    "semantic_floor": 91,
                },
                SearchSettings(),
            ),
        )
        assert stored.per_source == SearchSettings().per_source
        assert SearchSettings().per_source < MAX_LIMIT
        assert stored.zim_weight == 1
        assert stored.doc_weight == 1
        assert stored.snippet_chars == SearchSettings().snippet_chars
        assert stored.chunk_min_tokens == 200
        assert stored.chunk_max_tokens == 480
        assert stored.semantic_floor == 91
        assert read_search_settings(connection) == stored
        assert read_search_settings(connection).semantic_floor == 91
        beyond = write_search_settings(
            connection,
            read_search_payload({"chunk_max_tokens": 900}, SearchSettings()),
        )
        assert beyond.chunk_max_tokens <= EMBED_CONTEXT_TOKENS
    finally:
        connection.close()


def _hit(source: str, index: int) -> SearchResult:
    return SearchResult(source, source, source, f"{source}-{index}", "", "", index)


def test_interleaving_weights_change_how_many_each_source_contributes() -> None:
    zim = [_hit("zim", index) for index in range(4)]
    docs = [_hit("doc", index) for index in range(4)]
    plain = [result.title for result in interleave([zim, docs])]
    assert plain[:4] == ["zim-0", "doc-0", "zim-1", "doc-1"]
    weighted = [result.title for result in interleave([zim, docs], [2, 1])]
    assert weighted[:6] == ["zim-0", "zim-1", "doc-0", "zim-2", "zim-3", "doc-1"]


def test_the_extract_length_setting_reaches_the_results(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    assert (
        http_request(
            app, "PUT", "/api/admin/settings", json_body={"search": {"snippet_chars": 90}}
        )[0]
        == 200
    )
    payload = json.loads(http_get(app, "/api/search", query="q=primed")[1])
    results = payload["keyword"] + payload["semantic"]
    assert results
    assert all(len(result["snippet"]) <= 100 for result in results)


def test_the_semantic_floor_the_panel_saves_is_the_one_the_search_uses(
    library: tuple[Path, FastAPI],
) -> None:
    root, app = library
    shown = _json(app, "/api/admin/settings")["search"]
    assert shown["semantic_floor"] == SearchSettings().semantic_floor
    status, _ = http_request(
        app, "PUT", "/api/admin/settings", json_body={"search": {"semantic_floor": 91}}
    )
    assert status == 200
    assert _json(app, "/api/admin/settings")["search"]["semantic_floor"] == 91
    loose = http_request(
        app, "PUT", "/api/admin/settings", json_body={"search": {"semantic_floor": 0}}
    )
    assert loose[0] == 200
    assert _json(app, "/api/admin/settings")["search"]["semantic_floor"] == 0
    connection = open_library_ro(library_db_path(root))
    try:
        assert read_search_settings(connection).semantic_floor == 0
    finally:
        connection.close()


def test_the_status_counts_batches_documents_and_chunks(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    counts = _json(app, "/api/admin/status")["counts"]
    assert counts["shards"] == 1
    assert counts["documents"] == 1
    assert counts["chunks"] >= 1
    assert counts["zim"] == 1


def test_rebuilding_the_documentation_batch_says_when_there_is_nothing_to_index(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    status, body = http_request(app, "POST", "/api/admin/vendor/index")
    assert status == 200
    payload = json.loads(body)
    assert payload["sealed"] is False
    assert payload["reason"] in ("nothing_to_index", "embeddings_down")
