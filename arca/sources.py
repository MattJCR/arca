from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from arca.catalog import (
    CATALOG_BASE_URL,
    CatalogEntry,
    CatalogError,
    Resolution,
    entries_url,
    parse_entries,
    reader_url,
    resolve_downloads,
)
from arca.db import utc_now_iso
from arca.formats import format_for_mime, format_for_suffix
from arca.opds import HttpGet, OpdsEntry, OpdsError, OpdsLink, feed_url, http_get, parse_feed

KIND_OPDS = "opds"
KIND_JSON = "json"
KIND_MANUAL = "manual"
SOURCE_KINDS = (KIND_OPDS, KIND_JSON, KIND_MANUAL)

FLAVOUR_KIWIX = "kiwix"
FLAVOUR_PLAIN = "plain"
SOURCE_FLAVOURS = (FLAVOUR_KIWIX, FLAVOUR_PLAIN)

ITEM_ZIM = "zim"
ITEM_BOOK = "book"
ITEM_KINDS = (ITEM_ZIM, ITEM_BOOK)

STATUS_OK = "ok"
STATUS_DOWN = "down"
STATUS_UNVERIFIED = "unverified"
STATUS_UNKNOWN = "unknown"

FILTER_QUERY = "query"
FILTER_LANGUAGE = "language"
FILTER_CATEGORY = "category"
FILTER_FLAVOUR = "flavour"

ACQUISITION_ENTRY = "entry"
ACQUISITION_SUBSECTION = "subsection"
ACQUISITION_MODES = (ACQUISITION_ENTRY, ACQUISITION_SUBSECTION)
SUBSECTION_REL = "subsection"

KIWIX_SOURCE_ID = "kiwix"
JSON_FIELDS = (
    "id",
    "title",
    "summary",
    "language",
    "url",
    "sha256",
    "size",
    "filename",
    "mime",
)
EPUB_MEDIA_TYPE = "application/epub+zip"
GUTENBERG_BOOK_PATTERN = r"^/ebooks/[0-9]+\.opds$"
OPENSTAX_BOOKS_PATH = "/apps/cms/api/v2/pages/?type=books.Book&fields=*"
ARXIV_PROBE_QUERY = "all:electron"
ARCHIVE_SEARCH_PATH = (
    "/advancedsearch.php?fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=creator"
    "&fl%5B%5D=year&rows=25&output=json"
)
ARCHIVE_TEXTS_FILTER = "mediatype:texts"
SOURCE_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-_"
MAX_SOURCE_ID = 40


class SourceError(CatalogError):
    pass


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    kind: str = KIND_OPDS
    base_url: str = ""
    flavour: str = FLAVOUR_PLAIN
    item_kind: str = ITEM_BOOK
    entries_path: str = ""
    query_param: str = "q"
    language_param: str = ""
    media_type: str = ""
    suffixes: tuple[str, ...] = ()
    items_key: str = ""
    files_key: str = ""
    detail_path: str = ""
    download_path: str = ""
    query_filter: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    requires_query: bool = False
    probe_query: str = ""
    acquisition_via: str = ACQUISITION_ENTRY
    item_pattern: str = ""
    enabled: bool = True

    @property
    def two_step(self) -> bool:
        return self.acquisition_via == ACQUISITION_SUBSECTION

    @property
    def detailed(self) -> bool:
        return bool(self.detail_path)

    @property
    def is_kiwix(self) -> bool:
        return self.kind == KIND_OPDS and self.flavour == FLAVOUR_KIWIX

    @property
    def searchable(self) -> bool:
        return self.kind in (KIND_OPDS, KIND_JSON)

    @property
    def verifies_hash(self) -> bool:
        if self.is_kiwix:
            return True
        if self.kind == KIND_JSON:
            return bool(self.fields.get("sha256"))
        return False

    @property
    def filters(self) -> tuple[str, ...]:
        if self.is_kiwix:
            return (FILTER_QUERY, FILTER_LANGUAGE, FILTER_CATEGORY, FILTER_FLAVOUR)
        if not self.searchable:
            return ()
        if self.language_param:
            return (FILTER_QUERY, FILTER_LANGUAGE)
        return (FILTER_QUERY,)


@dataclass(frozen=True)
class RemoteItem:
    source_id: str
    item_id: str
    item_kind: str
    title: str
    description: str
    language: str
    flavour: str
    filename: str
    url: str
    group_id: str = ""
    tags: str = ""
    sha256: str = ""
    size_bytes: int | None = None
    article_count: int | None = None
    has_fulltext: bool = False
    verified: bool = False
    reader_url: str = ""

    @property
    def ref(self) -> str:
        return f"{self.source_id}:{self.item_id}"


@dataclass(frozen=True)
class SourceState:
    source_id: str
    name: str
    kind: str
    flavour: str
    item_kind: str
    base_url: str
    enabled: bool
    verifies_hash: bool
    searchable: bool
    filters: tuple[str, ...]
    status: str
    detail: str = ""
    checked_at: str = ""


def kiwix_source() -> Source:
    return Source(
        source_id=KIWIX_SOURCE_ID,
        name="Kiwix Library",
        kind=KIND_OPDS,
        base_url=CATALOG_BASE_URL,
        flavour=FLAVOUR_KIWIX,
        item_kind=ITEM_ZIM,
    )


def default_sources() -> tuple[Source, ...]:
    return (
        kiwix_source(),
        Source(
            source_id="standard-ebooks",
            name="Standard Ebooks",
            kind=KIND_OPDS,
            base_url="https://standardebooks.org",
            entries_path="/feeds/opds/all",
            query_param="query",
            requires_query=True,
            media_type=EPUB_MEDIA_TYPE,
            item_kind=ITEM_BOOK,
            enabled=False,
        ),
        Source(
            source_id="gutenberg",
            name="Project Gutenberg",
            kind=KIND_OPDS,
            base_url="https://m.gutenberg.org",
            entries_path="/ebooks/search.opds/",
            query_param="query",
            requires_query=True,
            acquisition_via=ACQUISITION_SUBSECTION,
            item_pattern=GUTENBERG_BOOK_PATTERN,
            media_type=EPUB_MEDIA_TYPE,
            item_kind=ITEM_BOOK,
            enabled=False,
        ),
        Source(
            source_id="arxiv",
            name="arXiv",
            kind=KIND_OPDS,
            base_url="https://export.arxiv.org",
            entries_path="/api/query",
            query_param="search_query",
            requires_query=True,
            probe_query=ARXIV_PROBE_QUERY,
            media_type="application/pdf",
            item_kind=ITEM_BOOK,
            enabled=False,
        ),
        Source(
            source_id="internet-archive",
            name="Internet Archive",
            kind=KIND_JSON,
            base_url="https://archive.org",
            entries_path=ARCHIVE_SEARCH_PATH,
            query_param="q",
            query_filter=ARCHIVE_TEXTS_FILTER,
            items_key="response.docs",
            files_key="files",
            detail_path="/metadata/{id}",
            download_path="/download/{id}/{name}",
            suffixes=(".pdf", ".epub"),
            fields={"id": "identifier", "title": "title", "filename": "name", "size": "size"},
            item_kind=ITEM_BOOK,
            enabled=False,
        ),
        Source(
            source_id="openstax",
            name="OpenStax",
            kind=KIND_JSON,
            base_url="https://openstax.org",
            entries_path=OPENSTAX_BOOKS_PATH,
            query_param="search",
            items_key="items",
            fields={"id": "id", "title": "title", "url": "pdf_url"},
            item_kind=ITEM_BOOK,
            enabled=False,
        ),
    )


def parse_source(raw: object) -> Source | None:
    return _build_source(raw)


def parse_sources(raw: object) -> tuple[Source, ...]:
    if not isinstance(raw, list):
        return default_sources()
    parsed: list[Source] = []
    seen: set[str] = set()
    for item in raw:
        source = _build_source(item)
        if source is None or source.source_id in seen:
            continue
        seen.add(source.source_id)
        parsed.append(source)
    if not parsed:
        return default_sources()
    return tuple(parsed)


def state_payload(state: SourceState) -> dict[str, Any]:
    return {
        "source_id": state.source_id,
        "name": state.name,
        "kind": state.kind,
        "flavour": state.flavour,
        "item_kind": state.item_kind,
        "base_url": state.base_url,
        "enabled": state.enabled,
        "verifies_hash": state.verifies_hash,
        "searchable": state.searchable,
        "filters": list(state.filters),
        "status": state.status,
        "detail": state.detail,
        "checked_at": state.checked_at,
    }


def item_payload(item: RemoteItem, source_name: str, local: dict[str, str]) -> dict[str, Any]:
    local_filename = local.get(item.group_id or item.item_id, "")
    return {
        "source_id": item.source_id,
        "source_name": source_name,
        "uuid": item.item_id,
        "ref": item.ref,
        "item_kind": item.item_kind,
        "title": item.title,
        "description": item.description,
        "language": item.language,
        "flavour": item.flavour,
        "filename": item.filename,
        "size_bytes": item.size_bytes,
        "article_count": item.article_count,
        "has_fulltext": item.has_fulltext,
        "verified": item.verified,
        "in_library": bool(local_filename),
        "is_update": bool(local_filename) and local_filename != item.filename,
        "reader_url": item.reader_url,
    }


def filename_from_url(url: str) -> str:
    raw = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    if not raw or raw in (".", "..") or "/" in raw or "\\" in raw:
        return ""
    return raw


def download_filename(url: str, media_type: str) -> str:
    name = filename_from_url(url)
    if not name or format_for_suffix(Path(name).suffix.lower()) is not None:
        return name
    entry = format_for_mime(media_type)
    if entry is None:
        return name
    parts = name.split(".")
    for index in range(1, len(parts)):
        if f".{parts[index]}".lower() == entry.suffix:
            return ".".join(parts[: index + 1])
    return f"{name}{entry.suffix}"


def search(
    source: Source,
    *,
    query: str = "",
    language: str = "",
    category: str = "",
    get: HttpGet | None = None,
) -> list[RemoteItem]:
    if not source.searchable:
        return []
    getter = get or http_get
    if source.is_kiwix:
        return _search_kiwix(source, query, language, category, getter)
    if source.kind == KIND_JSON:
        return _search_json(source, query, language, getter)
    return _search_opds(source, query, language, getter)


def probe(source: Source, get: HttpGet | None = None) -> None:
    if not source.searchable:
        return
    getter = get or http_get
    if source.is_kiwix:
        _download(entries_url(source.base_url or CATALOG_BASE_URL, count=1), getter)
        return
    params = _search_params(source, source.probe_query, "")
    _download(feed_url(source.base_url, source.entries_path, params), getter)


def acquire(source: Source, item: RemoteItem, get: HttpGet | None = None) -> RemoteItem:
    if source.detailed:
        return _acquire_detail(source, item, get or http_get)
    if not source.two_step:
        return item
    payload = _download(_absolute(source, item.url), get or http_get)
    try:
        entries = parse_feed(payload)
    except OpdsError as error:
        raise SourceError(str(error)) from error
    media_types = (source.media_type,) if source.media_type else ()
    for entry in entries:
        link = entry.acquisition(media_types, source.suffixes)
        if link is None:
            continue
        filename = download_filename(link.href, link.media_type)
        if not filename:
            continue
        return replace(
            item,
            url=link.href,
            filename=filename,
            size_bytes=link.length if link.length is not None else item.size_bytes,
        )
    raise SourceError(f"no download link behind {item.url}")


def catalog_entry(item: RemoteItem) -> CatalogEntry:
    return CatalogEntry(
        uuid=item.item_id,
        book_name=item.group_id,
        filename=item.filename,
        title=item.title,
        description=item.description,
        language=item.language,
        flavour=item.flavour,
        tags=item.tags,
        article_count=item.article_count,
        size_bytes=item.size_bytes,
        has_fulltext=item.has_fulltext,
        meta4_url=item.url,
    )


def resolve(source: Source, item: RemoteItem, get: HttpGet | None = None) -> Resolution:
    if source.is_kiwix:
        return resolve_downloads(item.url, get or http_get)
    return direct_resolution(item.url, item.sha256, item.size_bytes)


def direct_resolution(url: str, sha256: str, size_bytes: int | None = None) -> Resolution:
    return Resolution(
        size_bytes=size_bytes,
        sha256=sha256,
        mirrors=(url,),
        hash_missing=not sha256,
    )


def find_item(items: list[RemoteItem], item_id: str) -> RemoteItem | None:
    for item in items:
        if item.item_id == item_id:
            return item
    return None


def source_states(sources: tuple[Source, ...]) -> list[SourceState]:
    return [_state(source, STATUS_UNKNOWN, "", "") for source in sources]


class SourceRegistry:
    def __init__(self, sources: tuple[Source, ...]) -> None:
        self._lock = threading.Lock()
        self._sources = sources
        self._observed: dict[str, tuple[str, str, str]] = {}

    def replace(self, sources: tuple[Source, ...]) -> None:
        with self._lock:
            self._sources = sources
            known = {source.source_id for source in sources}
            self._observed = {key: value for key, value in self._observed.items() if key in known}

    def all(self) -> tuple[Source, ...]:
        with self._lock:
            return self._sources

    def enabled(self) -> tuple[Source, ...]:
        return tuple(source for source in self.all() if source.enabled)

    def get(self, source_id: str) -> Source | None:
        for source in self.all():
            if source.source_id == source_id:
                return source
        return None

    def observe(self, source_id: str, reachable: bool, detail: str = "") -> None:
        with self._lock:
            self._observed[source_id] = (
                STATUS_OK if reachable else STATUS_DOWN,
                detail,
                utc_now_iso(),
            )

    def states(self) -> list[SourceState]:
        with self._lock:
            observed = dict(self._observed)
            sources = self._sources
        states = []
        for source in sources:
            status, detail, checked_at = observed.get(source.source_id, (STATUS_UNKNOWN, "", ""))
            states.append(_state(source, status, detail, checked_at))
        return states


def _state(source: Source, status: str, detail: str, checked_at: str) -> SourceState:
    resolved = status
    if status == STATUS_OK and not source.verifies_hash:
        resolved = STATUS_UNVERIFIED
    return SourceState(
        source_id=source.source_id,
        name=source.name,
        kind=source.kind,
        flavour=source.flavour,
        item_kind=source.item_kind,
        base_url=source.base_url,
        enabled=source.enabled,
        verifies_hash=source.verifies_hash,
        searchable=source.searchable,
        filters=source.filters,
        status=resolved,
        detail=detail,
        checked_at=checked_at,
    )


def _search_kiwix(
    source: Source, query: str, language: str, category: str, get: HttpGet
) -> list[RemoteItem]:
    url = entries_url(
        source.base_url or CATALOG_BASE_URL, query=query, lang=language, category=category
    )
    payload = _download(url, get)
    return [_from_catalog_entry(source, entry) for entry in parse_entries(payload)]


def _from_catalog_entry(source: Source, entry: CatalogEntry) -> RemoteItem:
    return RemoteItem(
        source_id=source.source_id,
        item_id=entry.uuid,
        item_kind=source.item_kind,
        title=entry.title,
        description=entry.description,
        language=entry.language,
        flavour=entry.flavour,
        filename=entry.filename,
        url=entry.meta4_url,
        group_id=entry.book_name,
        tags=entry.tags,
        size_bytes=entry.size_bytes,
        article_count=entry.article_count,
        has_fulltext=entry.has_fulltext,
        verified=True,
        reader_url=reader_url(entry),
    )


def _search_term(source: Source, query: str) -> str:
    if not source.query_filter:
        return query
    if not query:
        return source.query_filter
    return f"{query} AND {source.query_filter}"


def _search_params(source: Source, query: str, language: str) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    term = _search_term(source, query)
    if source.query_param and (term or source.requires_query):
        params.append((source.query_param, term))
    if language and source.language_param:
        params.append((source.language_param, language))
    return params


def _absolute(source: Source, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{source.base_url}{href}"


def _download_link(source: Source, entry: OpdsEntry) -> OpdsLink | None:
    if source.two_step:
        for link in entry.links:
            if link.rel == SUBSECTION_REL:
                return link
        return None
    media_types = (source.media_type,) if source.media_type else ()
    return entry.acquisition(media_types, source.suffixes)


def _search_opds(source: Source, query: str, language: str, get: HttpGet) -> list[RemoteItem]:
    params = _search_params(source, query, language)
    payload = _download(feed_url(source.base_url, source.entries_path, params), get)
    try:
        entries = parse_feed(payload)
    except OpdsError as error:
        raise SourceError(str(error)) from error
    pattern = re.compile(source.item_pattern) if source.item_pattern else None
    items = []
    for entry in entries:
        link = _download_link(source, entry)
        if link is None or not entry.entry_id:
            continue
        if pattern is not None and not pattern.search(link.href):
            continue
        filename = "" if source.two_step else download_filename(link.href, link.media_type)
        if not source.two_step and not filename:
            continue
        items.append(
            RemoteItem(
                source_id=source.source_id,
                item_id=entry.entry_id,
                item_kind=source.item_kind,
                title=entry.title,
                description=entry.summary,
                language=entry.language,
                flavour="",
                filename=filename,
                url=link.href,
                size_bytes=link.length,
                verified=False,
            )
        )
    return items


def _template(path: str, identifier: str, name: str) -> str:
    return path.replace("{id}", urllib.parse.quote(identifier, safe="")).replace(
        "{name}", urllib.parse.quote(name)
    )


def _wanted_file(source: Source, name: str, mime: str) -> bool:
    if source.suffixes:
        return name.lower().endswith(source.suffixes)
    if source.media_type:
        return mime == source.media_type
    return True


def _named_files(
    source: Source, records: list[dict[str, object]]
) -> list[tuple[str, dict[str, object]]]:
    field_name = source.fields.get("filename", "name")
    named = [(_json_text(record, field_name), record) for record in records]
    return [(name, record) for name, record in named if name]


def _pick_file(
    source: Source, records: list[dict[str, object]]
) -> tuple[str, dict[str, object]] | None:
    named = _named_files(source, records)
    for suffix in source.suffixes:
        for name, record in named:
            if name.lower().endswith(suffix):
                return name, record
    if source.suffixes:
        return None
    for name, record in named:
        mime = _json_text(record, source.fields.get("mime", "mimeType"))
        if _wanted_file(source, name, mime):
            return name, record
    return None


def _acquire_detail(source: Source, item: RemoteItem, get: HttpGet) -> RemoteItem:
    payload = _download(_absolute(source, _template(source.detail_path, item.item_id, "")), get)
    chosen = _pick_file(source, _json_rows(_json_document(payload), source.files_key))
    if chosen is None:
        raise SourceError(f"no downloadable file behind {item.item_id}")
    name, record = chosen
    mime = _json_text(record, source.fields.get("mime", "mimeType"))
    declared = _json_text(record, source.fields.get("url", ""))
    url = declared or _template(source.download_path, item.item_id, name)
    size = _json_text(record, source.fields.get("size", "size"))
    return replace(
        item,
        url=_absolute(source, url),
        filename=download_filename(url, mime or source.media_type),
        size_bytes=int(size) if size.isdigit() else item.size_bytes,
    )


def _json_document(payload: bytes) -> object:
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except ValueError as error:
        raise SourceError(str(error)) from error


def _search_json(source: Source, query: str, language: str, get: HttpGet) -> list[RemoteItem]:
    params = _search_params(source, query, language)
    payload = _download(feed_url(source.base_url, source.entries_path, params), get)
    rows = _json_rows(_json_document(payload), source.items_key)
    items = []
    for index, row in enumerate(rows):
        item = _from_json_row(source, row, index)
        if item is not None:
            items.append(item)
    return items


def _json_rows(parsed: object, items_key: str) -> list[dict[str, object]]:
    container = parsed
    for step in items_key.split(".") if items_key else ():
        if not isinstance(container, dict):
            raise SourceError(f"expected an object with key {items_key}")
        container = container.get(step)
    if not isinstance(container, list):
        raise SourceError("expected a list of items")
    return [row for row in container if isinstance(row, dict)]


def _from_json_row(source: Source, row: dict[str, object], index: int) -> RemoteItem | None:
    identifier = _json_text(row, source.fields.get("id", "id")) or str(index)
    if source.detailed:
        return _pointer_item(source, row, identifier)
    url = _json_text(row, source.fields.get("url", "url"))
    if not url:
        return None
    filename = _json_text(row, source.fields.get("filename", "")) or filename_from_url(url)
    if not filename:
        return None
    size = _json_text(row, source.fields.get("size", ""))
    digest = _json_text(row, source.fields.get("sha256", ""))
    return RemoteItem(
        source_id=source.source_id,
        item_id=identifier,
        item_kind=source.item_kind,
        title=_json_text(row, source.fields.get("title", "title")) or filename,
        description=_json_text(row, source.fields.get("summary", "")),
        language=_json_text(row, source.fields.get("language", "")),
        flavour="",
        filename=filename,
        url=url,
        sha256=digest,
        size_bytes=int(size) if size.isdigit() else None,
        verified=bool(digest),
    )


def _pointer_item(source: Source, row: dict[str, object], identifier: str) -> RemoteItem | None:
    if not identifier:
        return None
    return RemoteItem(
        source_id=source.source_id,
        item_id=identifier,
        item_kind=source.item_kind,
        title=_json_text(row, source.fields.get("title", "title")) or identifier,
        description=_json_text(row, source.fields.get("summary", "")),
        language=_json_text(row, source.fields.get("language", "")),
        flavour="",
        filename="",
        url="",
    )


def _json_text(row: dict[str, object], key: str) -> str:
    if not key:
        return ""
    value = row.get(key)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _download(url: str, get: HttpGet) -> bytes:
    try:
        return get(url)
    except urllib.error.URLError as error:
        raise SourceError(str(error)) from error
    except OpdsError as error:
        raise SourceError(str(error)) from error


def _build_source(raw: object) -> Source | None:
    if not isinstance(raw, dict):
        return None
    source_id = _clean_id(_text(raw, "source_id"))
    kind = _text(raw, "kind") or KIND_OPDS
    if not source_id or kind not in SOURCE_KINDS:
        return None
    base_url = _text(raw, "base_url")
    if kind != KIND_MANUAL and not base_url:
        return None
    flavour = _text(raw, "flavour") or FLAVOUR_PLAIN
    item_kind = _text(raw, "item_kind") or ITEM_BOOK
    acquisition_via = _text(raw, "acquisition_via") or ACQUISITION_ENTRY
    item_pattern = _text(raw, "item_pattern")
    if acquisition_via not in ACQUISITION_MODES or not _compiles(item_pattern):
        return None
    base = Source(
        source_id=source_id,
        name=_text(raw, "name") or source_id,
        kind=kind,
        base_url=base_url,
        flavour=flavour if flavour in SOURCE_FLAVOURS else FLAVOUR_PLAIN,
        item_kind=item_kind if item_kind in ITEM_KINDS else ITEM_BOOK,
        entries_path=_text(raw, "entries_path"),
        query_param=_text(raw, "query_param") or "q",
        language_param=_text(raw, "language_param"),
        media_type=_text(raw, "media_type"),
        suffixes=_suffixes(raw.get("suffixes")),
        items_key=_text(raw, "items_key"),
        files_key=_text(raw, "files_key"),
        detail_path=_text(raw, "detail_path"),
        download_path=_text(raw, "download_path"),
        query_filter=_text(raw, "query_filter"),
        fields=_field_map(raw.get("fields")),
        requires_query=raw.get("requires_query") is True,
        probe_query=_text(raw, "probe_query"),
        acquisition_via=acquisition_via,
        item_pattern=item_pattern,
        enabled=raw.get("enabled") is not False,
    )
    if base.is_kiwix:
        return replace(base, item_kind=ITEM_ZIM)
    return base


def _compiles(pattern: str) -> bool:
    if not pattern:
        return True
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def _clean_id(value: str) -> str:
    lowered = value.strip().lower()
    if not lowered or len(lowered) > MAX_SOURCE_ID:
        return ""
    if any(character not in SOURCE_ID_ALPHABET for character in lowered):
        return ""
    return lowered


def _text(raw: dict[object, object], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _suffixes(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip().lower() for item in raw if isinstance(item, str) and item.strip())


def _field_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value.strip()
        for key, value in raw.items()
        if key in JSON_FIELDS and isinstance(value, str) and value.strip()
    }
