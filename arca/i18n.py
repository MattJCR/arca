from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

DEFAULT_LANGUAGE = "es"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("es", "en")


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
