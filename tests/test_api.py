import asyncio
import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library


def http_get(app: FastAPI, path: str, query: str = "") -> tuple[int, bytes]:
    messages: list[MutableMapping[str, Any]] = []
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        bytes(m.get("body", b"")) for m in messages if m["type"] == "http.response.body"
    )
    assert isinstance(status, int)
    return status, body


def _app(tmp_path: Path, config: ArcaConfig | None = None) -> FastAPI:
    root = tmp_path / "library"
    init_library(root)
    return create_app(root, config or ArcaConfig())


def test_diagnostics_endpoint_reports_checks_in_spanish_by_default(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/diagnostics")
    assert status == 200
    payload = json.loads(body)
    assert payload["language"] == "es"
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["library_path"]["state"] == "ok"
    assert checks["library_path"]["state_label"] == "correcto"


def test_diagnostics_endpoint_switches_to_english(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/diagnostics", query="lang=en")
    assert status == 200
    payload = json.loads(body)
    assert payload["language"] == "en"
    labels = {check["state_label"] for check in payload["checks"]}
    assert "correcto" not in labels


def test_diagnostics_endpoint_honours_configured_language(tmp_path: Path) -> None:
    _, body = http_get(_app(tmp_path, ArcaConfig(language="en")), "/api/diagnostics")
    assert json.loads(body)["language"] == "en"


def test_home_page_explains_the_empty_state(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/")
    text = body.decode("utf-8")
    assert status == 200
    assert 'lang="es"' in text
    assert "Biblioteca de conocimiento offline" in text
    assert 'class="invite"' in text
    assert "/static/style.css" in text


def test_home_page_renders_in_english_on_demand(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/", query="lang=en")
    text = body.decode("utf-8")
    assert status == 200
    assert 'lang="en"' in text
    assert "Offline knowledge library" in text
    assert "No collections downloaded yet" in text


def test_admin_page_is_bilingual_placeholder(tmp_path: Path) -> None:
    status_es, body_es = http_get(_app(tmp_path), "/admin")
    status_en, body_en = http_get(_app(tmp_path), "/admin", query="lang=en")
    assert status_es == status_en == 200
    assert "Administraci" in body_es.decode("utf-8")
    assert "Administration" in body_en.decode("utf-8")


def test_static_css_is_served(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/static/style.css")
    assert status == 200
    assert b"--accent" in body


def test_query_routes_write_nothing_under_the_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig())
    before = {str(p.relative_to(root)) for p in root.rglob("*")}
    for path, query in [
        ("/", ""),
        ("/", "lang=en"),
        ("/api/diagnostics", ""),
        ("/api/diagnostics", "lang=en"),
        ("/admin", ""),
        ("/static/style.css", ""),
    ]:
        status, _ = http_get(app, path, query=query)
        assert status == 200
    after = {str(p.relative_to(root)) for p in root.rglob("*")}
    assert after == before


def test_api_schema_endpoints_are_disabled(tmp_path: Path) -> None:
    status, _ = http_get(_app(tmp_path), "/docs")
    assert status == 404
    status, _ = http_get(_app(tmp_path), "/openapi.json")
    assert status == 404
