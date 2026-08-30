from __future__ import annotations

import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from xml.etree import ElementTree

from arca import __version__

ALLOWED_URL_SCHEMES = ("http", "https")
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = f"arca/{__version__}"
UUID_PREFIX = "urn:uuid:"
ACQUISITION_REL = "http://opds-spec.org/acquisition"

HttpGet = Callable[[str], bytes]


class OpdsError(Exception):
    pass


@dataclass(frozen=True)
class OpdsLink:
    rel: str
    href: str
    media_type: str
    length: int | None


@dataclass(frozen=True)
class OpdsEntry:
    entry_id: str
    fields: dict[str, str]
    links: tuple[OpdsLink, ...]

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    @property
    def summary(self) -> str:
        return self.fields.get("summary", "")

    @property
    def language(self) -> str:
        return self.fields.get("language", "")

    def acquisition(
        self, media_types: tuple[str, ...], suffixes: tuple[str, ...]
    ) -> OpdsLink | None:
        for link in self.links:
            if media_types and link.media_type in media_types:
                return link
            if suffixes and link.href.lower().endswith(suffixes):
                return link
            if not media_types and not suffixes and link.rel.startswith(ACQUISITION_REL):
                return link
        return None


def http_get(url: str) -> bytes:
    if urllib.parse.urlsplit(url).scheme not in ALLOWED_URL_SCHEMES:
        raise OpdsError(f"unsupported url scheme: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return bytes(response.read())


def feed_url(base_url: str, path: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return f"{base_url}{path}"
    separator = "&" if "?" in path else "?"
    return f"{base_url}{path}{separator}{urllib.parse.urlencode(params)}"


def parse_feed(payload: bytes) -> list[OpdsEntry]:
    return [_parse_entry(element) for element in _entry_elements(payload)]


def entry_fields(payload: bytes) -> list[dict[str, str]]:
    return [_direct_children_text(element) for element in _entry_elements(payload)]


def _parse_entry(element: ElementTree.Element) -> OpdsEntry:
    fields = _direct_children_text(element)
    return OpdsEntry(
        entry_id=fields.get("id", "").removeprefix(UUID_PREFIX),
        fields=fields,
        links=tuple(_links(element)),
    )


def _links(element: ElementTree.Element) -> list[OpdsLink]:
    links = []
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = child.get("href", "")
        if not href:
            continue
        length = child.get("length", "")
        links.append(
            OpdsLink(
                rel=child.get("rel", ""),
                href=href,
                media_type=child.get("type", ""),
                length=int(length) if length.isdigit() else None,
            )
        )
    return links


def _entry_elements(payload: bytes) -> list[ElementTree.Element]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise OpdsError(str(error)) from error
    return [element for element in root.iter() if _local_name(element.tag) == "entry"]


def _direct_children_text(element: ElementTree.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for child in element:
        name = _local_name(child.tag)
        if name not in fields:
            fields[name] = (child.text or "").strip()
    return fields


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
