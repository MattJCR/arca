from __future__ import annotations

import sqlite3
import urllib.error
import urllib.parse
from dataclasses import dataclass
from xml.etree import ElementTree

from arca.db import utc_now_iso
from arca.opds import HttpGet, OpdsEntry, OpdsError, entry_fields, feed_url, parse_feed
from arca.opds import http_get as opds_http_get

CATALOG_BASE_URL = "https://library.kiwix.org"
ENTRIES_PATH = "/catalog/v2/entries"
CATEGORIES_PATH = "/catalog/v2/categories"
LANGUAGES_PATH = "/catalog/v2/languages"
READER_BASE_URL = "https://browse.library.kiwix.org"
VIEWER_PATH = "/viewer#"
ZIM_SUFFIX = ".zim"
MIRROR_COUNTRY = "ES"
META4_SUFFIX = ".meta4"
ZIM_MEDIA_TYPE = "application/x-zim"
FULLTEXT_TAG = "_ftindex:yes"
METALINK_NAMESPACE = "{urn:ietf:params:xml:ns:metalink}"


class CatalogError(OpdsError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    uuid: str
    book_name: str
    filename: str
    title: str
    description: str
    language: str
    flavour: str
    tags: str
    article_count: int | None
    size_bytes: int | None
    has_fulltext: bool
    meta4_url: str


@dataclass(frozen=True)
class Resolution:
    size_bytes: int | None
    sha256: str
    mirrors: tuple[str, ...]
    hash_missing: bool


@dataclass(frozen=True)
class CatalogLanguage:
    code: str
    name: str
    book_count: int | None


def http_get(url: str) -> bytes:
    try:
        return opds_http_get(url)
    except OpdsError as error:
        raise CatalogError(str(error)) from error


def book_slug(entry: CatalogEntry) -> str:
    if entry.filename.endswith(ZIM_SUFFIX):
        return entry.filename[: -len(ZIM_SUFFIX)]
    return entry.book_name


def reader_url(entry: CatalogEntry, base_url: str = READER_BASE_URL) -> str:
    slug = book_slug(entry)
    if not slug:
        return ""
    return f"{base_url}{VIEWER_PATH}{urllib.parse.quote(slug)}"


def entries_url(
    base_url: str = CATALOG_BASE_URL,
    *,
    query: str = "",
    lang: str = "",
    category: str = "",
    name: str = "",
    start: int | None = None,
    count: int = -1,
) -> str:
    params: list[tuple[str, str]] = [("count", str(count))]
    if query:
        params.append(("q", query))
    if lang:
        params.append(("lang", lang))
    if category:
        params.append(("category", category))
    if name:
        params.append(("name", name))
    if start is not None:
        params.append(("start", str(start)))
    return feed_url(base_url, ENTRIES_PATH, params)


def parse_entries(payload: bytes) -> list[CatalogEntry]:
    entries = []
    for element in _feed(payload):
        entry = _from_opds(element)
        if entry is not None:
            entries.append(entry)
    return entries


def fetch_categories(base_url: str = CATALOG_BASE_URL, get: HttpGet = http_get) -> list[str]:
    return parse_categories(_download(f"{base_url}{CATEGORIES_PATH}", get))


def fetch_languages(
    base_url: str = CATALOG_BASE_URL, get: HttpGet = http_get
) -> list[CatalogLanguage]:
    return parse_languages(_download(f"{base_url}{LANGUAGES_PATH}", get))


def parse_categories(payload: bytes) -> list[str]:
    categories = []
    for fields in _fields(payload):
        title = fields.get("title", "")
        if title:
            categories.append(title)
    return sorted(set(categories), key=str.lower)


def parse_languages(payload: bytes) -> list[CatalogLanguage]:
    languages = []
    for fields in _fields(payload):
        code = fields.get("language", "")
        name = fields.get("title", "")
        if not code:
            continue
        count_text = fields.get("count", "")
        languages.append(
            CatalogLanguage(
                code=code,
                name=name or code,
                book_count=int(count_text) if count_text.isdigit() else None,
            )
        )
    languages.sort(key=lambda language: language.name.lower())
    return languages


def resolve_downloads(meta4_url: str, get: HttpGet = http_get) -> Resolution:
    request_url = f"{meta4_url}?country={MIRROR_COUNTRY}"
    try:
        payload = get(request_url)
    except urllib.error.HTTPError:
        return _fallback_resolution(meta4_url)
    except urllib.error.URLError as error:
        raise CatalogError(str(error)) from error
    try:
        return parse_meta4(payload)
    except CatalogError:
        return _fallback_resolution(meta4_url)


def parse_meta4(payload: bytes) -> Resolution:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise CatalogError(str(error)) from error
    file_element = root.find(f"{METALINK_NAMESPACE}file")
    if file_element is None:
        raise CatalogError("metalink file element missing")
    size_text = file_element.findtext(f"{METALINK_NAMESPACE}size", "")
    sha256 = ""
    for hash_element in file_element.findall(f"{METALINK_NAMESPACE}hash"):
        if hash_element.get("type") == "sha-256":
            sha256 = (hash_element.text or "").strip()
    prioritised = []
    for url_element in file_element.findall(f"{METALINK_NAMESPACE}url"):
        url = (url_element.text or "").strip()
        if url:
            prioritised.append((_priority(url_element.get("priority")), url))
    prioritised.sort(key=lambda item: item[0])
    mirrors = tuple(url for _, url in prioritised)
    if not mirrors:
        raise CatalogError("metalink has no mirror urls")
    return Resolution(
        size_bytes=int(size_text) if size_text.isdigit() else None,
        sha256=sha256,
        mirrors=mirrors,
        hash_missing=not sha256,
    )


def record_zim_entry(connection: sqlite3.Connection, entry: CatalogEntry) -> None:
    connection.execute(
        """
        INSERT INTO zim_files (
            uuid, book_name, filename, title, description, language, flavour,
            tags, size_bytes, article_count, has_fulltext, added_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'missing')
        ON CONFLICT(uuid) DO UPDATE SET
            book_name = excluded.book_name,
            filename = excluded.filename,
            title = excluded.title,
            description = excluded.description,
            language = excluded.language,
            flavour = excluded.flavour,
            tags = excluded.tags,
            size_bytes = excluded.size_bytes,
            article_count = excluded.article_count,
            has_fulltext = excluded.has_fulltext
        """,
        (
            entry.uuid,
            entry.book_name,
            entry.filename,
            entry.title,
            entry.description,
            entry.language,
            entry.flavour,
            entry.tags,
            entry.size_bytes,
            entry.article_count,
            int(entry.has_fulltext),
            utc_now_iso(),
        ),
    )
    connection.commit()


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "?"
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def safe_filename(meta4_url: str) -> str:
    raw = urllib.parse.unquote(meta4_url.rsplit("/", 1)[-1]).removesuffix(META4_SUFFIX)
    if not raw or raw in (".", "..") or "/" in raw or "\\" in raw:
        return ""
    return raw


def _download(url: str, get: HttpGet) -> bytes:
    try:
        return get(url)
    except urllib.error.URLError as error:
        raise CatalogError(str(error)) from error


def _feed(payload: bytes) -> list[OpdsEntry]:
    try:
        return parse_feed(payload)
    except OpdsError as error:
        raise CatalogError(str(error)) from error


def _fields(payload: bytes) -> list[dict[str, str]]:
    try:
        return entry_fields(payload)
    except OpdsError as error:
        raise CatalogError(str(error)) from error


def _from_opds(entry: OpdsEntry) -> CatalogEntry | None:
    link = entry.acquisition((ZIM_MEDIA_TYPE,), (META4_SUFFIX,))
    if not entry.entry_id or link is None:
        return None
    filename = safe_filename(link.href)
    if not filename:
        return None
    article_count_text = entry.fields.get("articleCount", "")
    tags = entry.fields.get("tags", "")
    return CatalogEntry(
        uuid=entry.entry_id,
        book_name=entry.fields.get("name", ""),
        filename=filename,
        title=entry.title,
        description=entry.summary,
        language=entry.language,
        flavour=entry.fields.get("flavour", ""),
        tags=tags,
        article_count=int(article_count_text) if article_count_text.isdigit() else None,
        size_bytes=link.length,
        has_fulltext=FULLTEXT_TAG in tags,
        meta4_url=link.href,
    )


def _fallback_resolution(meta4_url: str) -> Resolution:
    return Resolution(
        size_bytes=None,
        sha256="",
        mirrors=(meta4_url.removesuffix(META4_SUFFIX),),
        hash_missing=True,
    )


def _priority(value: str | None) -> int:
    if value is not None and value.isdigit():
        return int(value)
    return 999
