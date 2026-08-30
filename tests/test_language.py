from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library
from arca.i18n import LANGUAGE_COOKIE, load_catalog

SCREENS = ("/", "/talk", "/admin")


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    root = tmp_path / "library"
    init_library(root)
    yield create_app(root, ArcaConfig(auto_engine=False))


def call(
    app: FastAPI,
    path: str,
    query: str = "",
    cookie: str = "",
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], str]:
    messages: list[MutableMapping[str, Any]] = []
    body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = [(b"host", b"testserver")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if body:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(body)).encode()))
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": unquote(path),
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    delivered = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal delivered
        if delivered:
            await asyncio.Event().wait()
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    pairs = [
        (bytes(key).decode().lower(), bytes(value).decode()) for key, value in start["headers"]
    ]
    text = b"".join(
        bytes(message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    ).decode("utf-8", "replace")
    return int(start["status"]), dict(pairs), text


def cookies_of(headers: dict[str, str]) -> str:
    return headers.get("set-cookie", "")


def test_choosing_a_language_remembers_it_in_a_cookie(app: FastAPI) -> None:
    _status, headers, _body = call(app, "/", "lang=en")
    assert f"{LANGUAGE_COOKIE}=en" in cookies_of(headers)
    assert "Path=/" in cookies_of(headers)
    assert "SameSite=Lax" in cookies_of(headers)


@pytest.mark.parametrize("screen", SCREENS)
def test_the_remembered_language_survives_a_bare_link(app: FastAPI, screen: str) -> None:
    status, _headers, body = call(app, screen, cookie=f"{LANGUAGE_COOKIE}=en")
    assert status == 200
    assert '<html lang="en">' in body


@pytest.mark.parametrize("screen", SCREENS)
def test_without_a_cookie_the_screens_keep_the_default_language(app: FastAPI, screen: str) -> None:
    status, _headers, body = call(app, screen)
    assert status == 200
    assert '<html lang="es">' in body


def test_the_query_still_beats_the_cookie(app: FastAPI) -> None:
    _status, headers, body = call(app, "/", "lang=es", cookie=f"{LANGUAGE_COOKIE}=en")
    assert '<html lang="es">' in body
    assert f"{LANGUAGE_COOKIE}=es" in cookies_of(headers)


def test_choosing_what_is_already_remembered_sets_no_cookie_again(app: FastAPI) -> None:
    _status, headers, _body = call(app, "/", "lang=en", cookie=f"{LANGUAGE_COOKIE}=en")
    assert cookies_of(headers) == ""


def test_a_cookie_that_is_not_a_language_is_ignored(app: FastAPI) -> None:
    status, _headers, body = call(app, "/", cookie=f"{LANGUAGE_COOKIE}=klingon")
    assert status == 200
    assert '<html lang="es">' in body


def test_an_api_error_speaks_the_remembered_language(app: FastAPI) -> None:
    english = load_catalog("en").text("doc.error.unknown", doc_id="nope")
    status, _headers, body = call(app, "/api/doc/nope", cookie=f"{LANGUAGE_COOKIE}=en")
    assert status == 404
    assert english in body


def test_a_body_without_a_language_falls_back_to_the_cookie(app: FastAPI) -> None:
    status, _headers, body = call(
        app, "/api/talk", cookie=f"{LANGUAGE_COOKIE}=en", method="POST", json_body={}
    )
    assert status == 201
    assert json.loads(body)["title"] == load_catalog("en").text("talk.untitled")


def test_the_selector_works_without_javascript(app: FastAPI) -> None:
    _status, _headers, body = call(app, "/")
    assert '<form class="lang" method="get">' in body
    assert '<noscript><button type="submit">OK</button></noscript>' in body
