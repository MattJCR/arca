from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from html import escape
from pathlib import Path

from libzim.reader import Archive, Entry
from libzim.search import Query, Searcher
from libzim.suggestion import SuggestionSearcher

ZIM_SUBDIR = "zim"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_REDIRECT_HOPS = 8
SEARCH_MODE_FULLTEXT = "fulltext"
SEARCH_MODE_TITLE = "title"
SEARCH_MODE_NONE = "none"
HTML_MIMETYPES = ("text/html", "application/xhtml+xml")
TEXT_MIMETYPES = (
    "text/html",
    "text/plain",
    "text/css",
    "text/javascript",
    "application/xhtml+xml",
    "application/javascript",
    "application/json",
    "image/svg+xml",
)

_HEAD_PATTERN = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_BASE_PATTERN = re.compile(rb"<base(?:\s[^>]*)?>", re.IGNORECASE)


class ZimError(RuntimeError):
    pass


class ZimNotFoundError(ZimError):
    pass


class ZimEntryNotFoundError(ZimError):
    pass


@dataclass(frozen=True)
class ZimInfo:
    uuid: str
    filename: str
    path: Path
    title: str
    description: str
    language: str
    article_count: int
    media_count: int
    has_fulltext: bool
    has_title_index: bool
    main_path: str
    size_bytes: int


@dataclass(frozen=True)
class ZimFailure:
    filename: str
    error: str


@dataclass(frozen=True)
class ZimHit:
    uuid: str
    source_title: str
    path: str
    title: str
    rank: int


@dataclass(frozen=True)
class ZimResults:
    uuid: str
    source_title: str
    mode: str
    estimated_matches: int
    hits: list[ZimHit]


@dataclass(frozen=True)
class ZimContent:
    path: str
    title: str
    mimetype: str
    data: bytes


def open_archive(path: Path) -> Archive:
    try:
        return Archive(path)
    except Exception as error:
        raise ZimError(f"cannot open zim: {path.name}: {error}") from error


def read_info(path: Path) -> ZimInfo:
    return archive_info(open_archive(path), path)


def archive_info(archive: Archive, path: Path) -> ZimInfo:
    return ZimInfo(
        uuid=str(archive.uuid),
        filename=path.name,
        path=path,
        title=_metadata(archive, "Title") or path.stem,
        description=_metadata(archive, "Description"),
        language=_metadata(archive, "Language"),
        article_count=int(archive.article_count),
        media_count=int(archive.media_count),
        has_fulltext=bool(archive.has_fulltext_index),
        has_title_index=bool(archive.has_title_index),
        main_path=_main_path(archive),
        size_bytes=_file_size(path),
    )


def search_archive(
    archive: Archive, info: ZimInfo, query: str, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> ZimResults:
    text = query.strip()
    limit = max(0, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    mode = index_mode(info)
    if not text or limit == 0 or mode == SEARCH_MODE_NONE:
        return ZimResults(info.uuid, info.title, mode, 0, [])
    if mode == SEARCH_MODE_FULLTEXT:
        return _fulltext_search(archive, info, text, limit, offset)
    return _title_search(archive, info, text, limit, offset)


def read_entry(archive: Archive, path: str) -> ZimContent:
    if not path:
        raise ZimEntryNotFoundError("empty entry path")
    entry = _resolve(_lookup(archive, path))
    item = entry.get_item()
    return ZimContent(
        path=entry.path,
        title=entry.title,
        mimetype=str(item.mimetype),
        data=bytes(item.content),
    )


class ZimLibrary:
    def __init__(self, root: Path) -> None:
        self._directory = root / ZIM_SUBDIR
        self._lock = threading.Lock()
        self._archives: dict[str, Archive] = {}
        self._infos: dict[str, ZimInfo] = {}
        self._signatures: dict[str, tuple[int, int]] = {}
        self._failures: list[ZimFailure] = []

    def refresh(self) -> list[ZimInfo]:
        with self._lock:
            self._scan()
            return self._sorted_infos()

    def infos(self) -> list[ZimInfo]:
        with self._lock:
            return self._sorted_infos()

    def failures(self) -> list[ZimFailure]:
        with self._lock:
            return list(self._failures)

    def info(self, uuid: str) -> ZimInfo:
        _, info = self._entry_for(uuid)
        return info

    def search(
        self, uuid: str, query: str, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> ZimResults:
        archive, info = self._entry_for(uuid)
        return search_archive(archive, info, query, limit, offset)

    def search_all(self, query: str, limit: int = DEFAULT_LIMIT) -> list[ZimResults]:
        results = []
        for info in self.infos():
            try:
                archive, current = self._entry_for(info.uuid)
            except ZimNotFoundError:
                continue
            results.append(search_archive(archive, current, query, limit))
        return results

    def content(self, uuid: str, path: str) -> ZimContent:
        archive, _ = self._entry_for(uuid)
        return read_entry(archive, path)

    def release(self, filename: str) -> bool:
        with self._lock:
            self._signatures.pop(filename, None)
            for uuid, info in list(self._infos.items()):
                if info.filename == filename:
                    self._infos.pop(uuid, None)
                    self._archives.pop(uuid, None)
                    return True
        return False

    def close(self) -> None:
        with self._lock:
            self._archives.clear()
            self._infos.clear()
            self._signatures.clear()
            self._failures.clear()

    def _entry_for(self, uuid: str) -> tuple[Archive, ZimInfo]:
        with self._lock:
            archive = self._archives.get(uuid)
            info = self._infos.get(uuid)
            if archive is None or info is None:
                self._scan()
                archive = self._archives.get(uuid)
                info = self._infos.get(uuid)
        if archive is None or info is None:
            raise ZimNotFoundError(uuid)
        return archive, info

    def _scan(self) -> None:
        self._failures = []
        seen: dict[str, tuple[int, int]] = {}
        keep: set[str] = set()
        for path in self._files():
            signature = _signature(path)
            if signature is None:
                continue
            seen[path.name] = signature
            uuid = self._reuse(path.name, signature)
            if uuid is not None:
                keep.add(uuid)
                continue
            try:
                archive = open_archive(path)
                info = archive_info(archive, path)
            except ZimError as error:
                self._failures.append(ZimFailure(path.name, str(error)))
                continue
            self._archives[info.uuid] = archive
            self._infos[info.uuid] = info
            keep.add(info.uuid)
        self._signatures = seen
        for uuid in list(self._infos):
            if uuid not in keep:
                self._infos.pop(uuid, None)
                self._archives.pop(uuid, None)

    def _reuse(self, filename: str, signature: tuple[int, int]) -> str | None:
        if self._signatures.get(filename) != signature:
            return None
        for uuid, info in self._infos.items():
            if info.filename == filename:
                return uuid
        return None

    def _files(self) -> list[Path]:
        if not self._directory.is_dir():
            return []
        return sorted(
            (path for path in self._directory.glob("*.zim") if path.is_file()),
            key=lambda path: path.name,
        )

    def _sorted_infos(self) -> list[ZimInfo]:
        return sorted(self._infos.values(), key=lambda info: (info.title.lower(), info.filename))


def is_html(mimetype: str) -> bool:
    return _base_mimetype(mimetype) in HTML_MIMETYPES


def media_type(mimetype: str) -> str:
    base = _base_mimetype(mimetype) or "application/octet-stream"
    if "charset=" in mimetype.lower() or base not in TEXT_MIMETYPES:
        return mimetype or base
    return f"{base}; charset=utf-8"


def entry_directory(path: str) -> str:
    head, separator, _ = path.rpartition("/")
    return f"{head}{separator}" if separator else ""


def inject_base(data: bytes, href: str) -> bytes:
    tag = b'<base href="' + escape(href, quote=True).encode("utf-8") + b'">'
    without_base = _BASE_PATTERN.sub(b"", data, count=1)
    head = _HEAD_PATTERN.search(without_base)
    if head is None:
        return tag + without_base
    return without_base[: head.end()] + tag + without_base[head.end() :]


def _base_mimetype(mimetype: str) -> str:
    return mimetype.split(";")[0].strip().lower()


def index_mode(info: ZimInfo) -> str:
    if info.has_fulltext:
        return SEARCH_MODE_FULLTEXT
    if info.has_title_index:
        return SEARCH_MODE_TITLE
    return SEARCH_MODE_NONE


def _fulltext_search(
    archive: Archive, info: ZimInfo, text: str, limit: int, offset: int
) -> ZimResults:
    try:
        search = Searcher(archive).search(Query().set_query(text))
        estimated = int(search.getEstimatedMatches())
        paths = list(search.getResults(offset, limit))
    except Exception as error:
        raise ZimError(f"fulltext search failed on {info.filename}: {error}") from error
    return ZimResults(
        info.uuid, info.title, SEARCH_MODE_FULLTEXT, estimated, _hits(archive, info, paths, offset)
    )


def _title_search(
    archive: Archive, info: ZimInfo, text: str, limit: int, offset: int
) -> ZimResults:
    try:
        search = SuggestionSearcher(archive).suggest(text)
        estimated = int(search.getEstimatedMatches())
        paths = list(search.getResults(offset, limit))
    except Exception as error:
        raise ZimError(f"title search failed on {info.filename}: {error}") from error
    return ZimResults(
        info.uuid, info.title, SEARCH_MODE_TITLE, estimated, _hits(archive, info, paths, offset)
    )


def _hits(archive: Archive, info: ZimInfo, paths: list[str], offset: int) -> list[ZimHit]:
    hits = []
    for position, path in enumerate(paths):
        hits.append(
            ZimHit(
                uuid=info.uuid,
                source_title=info.title,
                path=path,
                title=_entry_title(archive, path),
                rank=offset + position,
            )
        )
    return hits


def _entry_title(archive: Archive, path: str) -> str:
    try:
        return archive.get_entry_by_path(path).title or path
    except (KeyError, RuntimeError):
        return path


def _main_path(archive: Archive) -> str:
    if not archive.has_main_entry:
        return ""
    try:
        return str(_resolve(archive.main_entry).path)
    except (ZimEntryNotFoundError, KeyError, RuntimeError):
        return ""


def _resolve(entry: Entry) -> Entry:
    for _ in range(MAX_REDIRECT_HOPS):
        if not entry.is_redirect:
            return entry
        entry = entry.get_redirect_entry()
    raise ZimEntryNotFoundError(f"redirect loop: {entry.path}")


def _lookup(archive: Archive, path: str) -> Entry:
    try:
        return archive.get_entry_by_path(path)
    except (KeyError, RuntimeError) as error:
        raise ZimEntryNotFoundError(path) from error


def _metadata(archive: Archive, key: str) -> str:
    try:
        raw = archive.get_metadata(key)
    except (KeyError, RuntimeError):
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace").strip()
    return str(raw).strip()


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)
