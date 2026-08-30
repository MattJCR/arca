from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from http.cookies import CookieError, SimpleCookie
from importlib import resources
from typing import Any
from urllib.parse import parse_qs

DEFAULT_LANGUAGE = "es"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("es", "en")
LANGUAGE_COOKIE = "arca_lang"
LANGUAGE_COOKIE_MAX_AGE = 31_536_000
LANGUAGE_QUERY = "lang"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_ACTIVE_LANGUAGE: ContextVar[str] = ContextVar("arca_active_language", default="")


@dataclass(frozen=True)
class Catalog:
    language: str
    messages: dict[str, str] = field(default_factory=dict)

    def has(self, key: str) -> bool:
        return key in self.messages

    def text(self, key: str, **params: object) -> str:
        template = self.messages.get(key)
        if template is None:
            return key
        try:
            return template.format(**params)
        except (KeyError, IndexError):
            return template


def normalize_language(value: str | None) -> str:
    if value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def load_catalog(language: str | None = None) -> Catalog:
    normalized = normalize_language(language)
    raw = (
        resources.files("arca").joinpath("locale").joinpath(f"{normalized}.json").read_text("utf-8")
    )
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"catalog {normalized} must be a JSON object")
    messages: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str):
            messages[key] = value
    return Catalog(normalized, messages)


def active_language() -> str:
    return _ACTIVE_LANGUAGE.get()


def language_from_cookie(header: str) -> str:
    jar = SimpleCookie()
    try:
        jar.load(header)
    except CookieError:
        return ""
    morsel = jar.get(LANGUAGE_COOKIE)
    value = morsel.value if morsel is not None else ""
    return value if value in SUPPORTED_LANGUAGES else ""


def language_from_query(query: bytes) -> str:
    chosen = parse_qs(query.decode("latin-1", "replace")).get(LANGUAGE_QUERY) or [""]
    return chosen[0] if chosen[0] in SUPPORTED_LANGUAGES else ""


def language_cookie_header(language: str) -> bytes:
    return (
        f"{LANGUAGE_COOKIE}={language}; Path=/; Max-Age={LANGUAGE_COOKIE_MAX_AGE}; "
        "SameSite=Lax; HttpOnly"
    ).encode("latin-1")


class LanguageMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        stored = language_from_cookie(_header(scope, b"cookie"))
        chosen = language_from_query(scope.get("query_string", b""))
        previous = _ACTIVE_LANGUAGE.set(chosen or stored)
        try:
            if not chosen or chosen == stored:
                await self.app(scope, receive, send)
            else:
                await self.app(scope, receive, _remembering(send, chosen))
        finally:
            _ACTIVE_LANGUAGE.reset(previous)


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or ():
        if key == name:
            return str(value.decode("latin-1", "replace"))
    return ""


def _remembering(send: Send, language: str) -> Send:
    async def wrapped(message: Message) -> None:
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers") or [])
            headers.append((b"set-cookie", language_cookie_header(language)))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped
