from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_ro
from arca.hub import (
    HubGatedError,
    HubUnavailableError,
    download_url,
    is_model_id,
    model_files,
    part_of,
    quantization_of,
    search_models,
)
from tests.support import ModelHubServer
from tests.test_api import http_get, http_request

FILES = [
    ("gemma-4-12b-qat-Q4_K_M.gguf", 7_000_000_000, "a" * 64),
    ("gemma-4-12b-qat-Q8_0.gguf", 13_000_000_000, "b" * 64),
    ("gemma-4-12b-qat-BF16-00001-of-00002.gguf", 12_000_000_000, ""),
    ("README.md", 400, ""),
]


@pytest.fixture
def hub() -> Iterator[ModelHubServer]:
    server = ModelHubServer()
    server.add("google/gemma-4-12b-qat", FILES, downloads=4200)
    server.add("google/gemma-4-26b-a4b-qat", FILES[:1], downloads=900, gated=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def library(tmp_path: Path, hub: ModelHubServer) -> Iterator[tuple[Path, FastAPI]]:
    root = tmp_path / "library"
    init_library(root)
    yield root, create_app(root, ArcaConfig(hub_url=hub.url))


def _json(app: FastAPI, path: str, query: str = "") -> dict[str, Any]:
    status, body = http_get(app, path, query=query)
    assert status == 200
    parsed: dict[str, Any] = json.loads(body)
    return parsed


@pytest.mark.parametrize(
    ("filename", "quantization", "part"),
    [
        ("gemma-4-12b-qat-Q4_K_M.gguf", "Q4_K_M", ""),
        ("model.IQ2_XXS.gguf", "IQ2_XXS", ""),
        ("weights-BF16-00001-of-00003.gguf", "BF16", "00001/00003"),
        ("nothing-special.gguf", "", ""),
    ],
)
def test_the_quantization_is_read_from_the_file_name(
    filename: str, quantization: str, part: str
) -> None:
    assert quantization_of(filename) == quantization
    assert part_of(filename) == part


def test_a_forged_model_identifier_is_refused() -> None:
    assert is_model_id("google/gemma-4-12b-qat")
    assert not is_model_id("../../etc/passwd")
    assert not is_model_id("google")
    assert not is_model_id("/absolute/path")


def test_search_reports_downloads_and_the_gated_flag(hub: ModelHubServer) -> None:
    found = {model.model_id: model for model in search_models(hub.url, "gemma")}
    assert found["google/gemma-4-12b-qat"].downloads == 4200
    assert found["google/gemma-4-12b-qat"].gated is False
    assert found["google/gemma-4-26b-a4b-qat"].gated is True
    assert found["google/gemma-4-12b-qat"].author == "google"


def test_only_gguf_files_are_listed_with_size_and_hash(hub: ModelHubServer) -> None:
    model, files = model_files(hub.url, "google/gemma-4-12b-qat")
    assert model.model_id == "google/gemma-4-12b-qat"
    assert [item.path for item in files] == [
        "gemma-4-12b-qat-BF16-00001-of-00002.gguf",
        "gemma-4-12b-qat-Q4_K_M.gguf",
        "gemma-4-12b-qat-Q8_0.gguf",
    ]
    by_name = {item.path: item for item in files}
    assert by_name["gemma-4-12b-qat-Q4_K_M.gguf"].sha256 == "a" * 64
    assert by_name["gemma-4-12b-qat-Q4_K_M.gguf"].size_bytes == 7_000_000_000
    assert by_name["gemma-4-12b-qat-BF16-00001-of-00002.gguf"].sha256 == ""


def test_a_gated_model_is_reported_as_gated(hub: ModelHubServer) -> None:
    with pytest.raises(HubGatedError):
        model_files(hub.url, "google/gemma-4-26b-a4b-qat")


def test_a_hub_that_does_not_answer_is_reported_not_raised_raw() -> None:
    with pytest.raises(HubUnavailableError):
        search_models("http://127.0.0.1:1", "gemma")


def test_the_download_url_is_built_at_the_moment(hub: ModelHubServer) -> None:
    url = download_url(hub.url, "google/gemma-4-12b-qat", "gemma-4-12b-qat-Q4_K_M.gguf")
    assert url.endswith("/google/gemma-4-12b-qat/resolve/main/gemma-4-12b-qat-Q4_K_M.gguf")


def test_the_search_endpoint_needs_no_network_without_a_query(
    library: tuple[Path, FastAPI], hub: ModelHubServer
) -> None:
    _, app = library
    payload = _json(app, "/api/admin/hub")
    assert payload["models"] == []
    assert hub.requests == []


def test_the_search_endpoint_lists_models(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    payload = _json(app, "/api/admin/hub", query="q=gemma")
    assert [model["model_id"] for model in payload["models"]] == [
        "google/gemma-4-12b-qat",
        "google/gemma-4-26b-a4b-qat",
    ]


def test_the_detail_endpoint_returns_the_quantizations(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    payload = _json(app, "/api/admin/hub/google/gemma-4-12b-qat")
    quantizations = {item["quantization"] for item in payload["files"]}
    assert quantizations == {"Q4_K_M", "Q8_0", "BF16"}
    assert payload["local"] == []


def test_a_gated_model_answers_with_its_own_code(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, body = http_get(app, "/api/admin/hub/google/gemma-4-26b-a4b-qat")
    assert status == 403
    payload = json.loads(body)
    assert payload["gated"] is True
    assert payload["code"] == "gated"


def test_an_unreachable_hub_answers_502(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig(hub_url="http://127.0.0.1:1"))
    status, body = http_get(app, "/api/admin/hub", query="q=gemma")
    assert status == 502
    payload = json.loads(body)
    assert payload["error"]
    assert payload["code"] == "unreachable"


def test_downloading_a_variant_queues_it_with_its_hash(library: tuple[Path, FastAPI]) -> None:
    root, app = library
    status, body = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={
            "model_id": "google/gemma-4-12b-qat",
            "path": "gemma-4-12b-qat-Q4_K_M.gguf",
            "kind": "chat",
        },
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["hash_missing"] is False
    assert payload["dest"].endswith(str(Path("models") / "chat" / "gemma-4-12b-qat-Q4_K_M.gguf"))
    connection = open_library_ro(library_db_path(root))
    try:
        row = connection.execute(
            "SELECT kind, ref, sha256, mirrors, dest_path FROM downloads WHERE id = ?",
            (payload["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row["kind"] == "model"
    assert row["sha256"] == "a" * 64
    assert "resolve/main" in row["mirrors"]


def test_a_variant_without_a_hash_is_queued_and_declared(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, body = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={
            "model_id": "google/gemma-4-12b-qat",
            "path": "gemma-4-12b-qat-BF16-00001-of-00002.gguf",
            "kind": "embed",
        },
    )
    assert status == 200
    assert json.loads(body)["hash_missing"] is True


def test_the_download_endpoint_refuses_forged_input(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    bad_id = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={"model_id": "../etc", "path": "a.gguf", "kind": "chat"},
    )
    bad_kind = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={
            "model_id": "google/gemma-4-12b-qat",
            "path": "gemma-4-12b-qat-Q4_K_M.gguf",
            "kind": "roms",
        },
    )
    traversal = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={
            "model_id": "google/gemma-4-12b-qat",
            "path": "../../escape.gguf",
            "kind": "chat",
        },
    )
    unknown = http_request(
        app,
        "POST",
        "/api/admin/hub/download",
        json_body={
            "model_id": "google/gemma-4-12b-qat",
            "path": "not-published.gguf",
            "kind": "chat",
        },
    )
    assert bad_id[0] == 400
    assert bad_kind[0] == 400
    assert traversal[0] == 400
    assert unknown[0] == 404
    assert json.loads(bad_id[1])["code"] == "bad_model_id"
    assert json.loads(bad_kind[1])["code"] == "bad_kind"
    assert json.loads(traversal[1])["code"] == "bad_file"
    assert json.loads(unknown[1])["code"] == "unknown_file"
