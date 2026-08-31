from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import quote

from arca.db import read_setting, write_setting
from arca.embed import EMBED_CONTEXT_TOKENS
from arca.formats import PDF_MIME, format_for_mime
from arca.ingest import MAX_CHUNK_TOKENS, MIN_CHUNK_TOKENS
from arca.shards import DocumentRow, Shard, ShardHit
from arca.vectors import VectorHit, hit_order
from arca.zim import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ZimError,
    ZimInfo,
    ZimLibrary,
    ZimResults,
    index_mode,
)

SOURCE_ZIM = "zim"
SOURCE_DOC = "doc"
SNIPPET_CHARS = 320
MODE_KEYWORD = "keyword"
MODE_SEMANTIC = "semantic"
MODE_BOTH = "both"
SEARCH_MODES = (MODE_KEYWORD, MODE_SEMANTIC, MODE_BOTH)
SETTINGS_KEY = "search"
MAX_WEIGHT = 5
SEMANTIC_FLOOR = 82
MAX_FLOOR = 100
MIN_SNIPPET_CHARS = 80
MAX_SNIPPET_CHARS = 1000
MIN_TOKENS_FLOOR = 100
MAX_TOKENS_CEILING = EMBED_CONTEXT_TOKENS


@dataclass(frozen=True)
class SearchResult:
    source_kind: str
    source_id: str
    source_title: str
    title: str
    path: str
    url: str
    rank: int
    doc_id: str = ""
    anchor: str = ""
    page: int = 0
    snippet: str = ""
    original_url: str = ""
    original_download: bool = False
    score: float = 0.0


@dataclass(frozen=True)
class SourceReport:
    kind: str
    source_id: str
    title: str
    mode: str
    estimated_matches: int
    returned: int
    error: str


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    mode: str
    keyword: list[SearchResult]
    semantic: list[SearchResult]
    semantic_available: bool
    sources: list[SourceReport]
    semantic_offline: bool = False


def normalize_mode(value: str | None) -> str:
    if value in SEARCH_MODES:
        return value
    return MODE_BOTH


def normalize_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def search_library(
    library: ZimLibrary,
    query: str,
    scope: Sequence[str] = (),
    mode: str = MODE_BOTH,
    limit: int = DEFAULT_LIMIT,
    disabled: Sequence[str] = (),
    shards: Sequence[Shard] = (),
    query_vector: Sequence[float] | None = None,
    settings: SearchSettings | None = None,
) -> SearchOutcome:
    text = query.strip()
    tuning = settings if settings is not None else SearchSettings()
    effective_mode = normalize_mode(mode)
    effective_limit = normalize_limit(limit)
    per_source = normalize_limit(tuning.per_source)
    infos = _selected(library, scope, disabled)
    semantic_ready = any(shard.state.searchable_by_vector for shard in shards)
    wanted = effective_mode in (MODE_SEMANTIC, MODE_BOTH)
    if not text:
        return SearchOutcome(text, effective_mode, [], [], semantic_ready, _idle_reports(infos))
    keyword: list[SearchResult] = []
    reports: list[SourceReport] = []
    if effective_mode in (MODE_KEYWORD, MODE_BOTH):
        groups, reports = _zim_groups(library, infos, text, per_source)
        shard_groups, shard_reports = search_shards_by_text(
            shards, text, per_source, tuning.snippet_chars
        )
        weights = [tuning.zim_weight] * len(groups) + [tuning.doc_weight] * len(shard_groups)
        keyword = interleave(groups + shard_groups, weights)[:effective_limit]
        reports = reports + shard_reports
    else:
        reports = _idle_reports(infos)
    semantic: list[SearchResult] = []
    if wanted and query_vector is not None:
        semantic = search_shards_by_vector(
            shards, query_vector, effective_limit, tuning.snippet_chars, tuning.semantic_floor
        )
    offline = wanted and semantic_ready and query_vector is None
    return SearchOutcome(text, effective_mode, keyword, semantic, semantic_ready, reports, offline)


def _zim_groups(
    library: ZimLibrary, infos: Sequence[ZimInfo], text: str, limit: int
) -> tuple[list[list[SearchResult]], list[SourceReport]]:
    groups: list[list[SearchResult]] = []
    reports: list[SourceReport] = []
    for info in infos:
        results, error = _search_one(library, info, text, limit)
        if results is None:
            reports.append(
                SourceReport(SOURCE_ZIM, info.uuid, info.title, index_mode(info), 0, 0, error)
            )
            continue
        hits = [_result(info, results, position) for position in range(len(results.hits))]
        if hits:
            groups.append(hits)
        reports.append(
            SourceReport(
                SOURCE_ZIM,
                info.uuid,
                info.title,
                results.mode,
                results.estimated_matches,
                len(hits),
                "",
            )
        )
    return groups, reports


def interleave(
    groups: Sequence[Sequence[SearchResult]], weights: Sequence[int] = ()
) -> list[SearchResult]:
    depths = [max(1, weights[index]) if index < len(weights) else 1 for index in range(len(groups))]
    taken = [0] * len(groups)
    merged: list[SearchResult] = []
    while any(taken[index] < len(groups[index]) for index in range(len(groups))):
        for index, group in enumerate(groups):
            for _ in range(depths[index]):
                if taken[index] < len(group):
                    merged.append(group[taken[index]])
                    taken[index] += 1
    return merged


def entry_url(uuid: str, path: str) -> str:
    return f"/zim/{quote(uuid, safe='')}/{quote(path, safe='/')}"


def _selected(
    library: ZimLibrary, scope: Sequence[str], disabled: Sequence[str] = ()
) -> list[ZimInfo]:
    excluded = set(disabled)
    infos = [info for info in library.refresh() if info.uuid not in excluded]
    if not scope:
        return infos
    wanted = set(scope)
    return [info for info in infos if info.uuid in wanted]


def _search_one(
    library: ZimLibrary, info: ZimInfo, text: str, limit: int
) -> tuple[ZimResults | None, str]:
    try:
        return library.search(info.uuid, text, limit=limit), ""
    except ZimError as error:
        return None, str(error)


def _result(info: ZimInfo, results: ZimResults, position: int) -> SearchResult:
    hit = results.hits[position]
    return SearchResult(
        source_kind=SOURCE_ZIM,
        source_id=info.uuid,
        source_title=info.title,
        title=hit.title,
        path=hit.path,
        url=entry_url(info.uuid, hit.path),
        rank=hit.rank,
    )


def _idle_reports(infos: Sequence[ZimInfo]) -> list[SourceReport]:
    return [
        SourceReport(SOURCE_ZIM, info.uuid, info.title, index_mode(info), 0, 0, "")
        for info in infos
    ]


def document_url(doc_id: str, anchor: str = "") -> str:
    base = f"/api/doc/{quote(doc_id, safe='')}"
    return f"{base}#{quote(anchor, safe=':')}" if anchor else base


def original_url(doc_id: str, page: int = 0) -> str:
    base = f"/api/doc/{quote(doc_id, safe='')}/original"
    return f"{base}#page={page}" if page else base


def passage(text: str, terms: Sequence[str], limit: int = SNIPPET_CHARS) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    lowered = collapsed.lower()
    hits = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    if not hits:
        return snippet(collapsed, limit)
    start = max(0, _anchor(lowered, terms, limit) - limit // 3)
    window = collapsed[start : start + limit]
    prefix = "…" if start else ""
    suffix = "…" if start + limit < len(collapsed) else ""
    return f"{prefix}{window.strip()}{suffix}"


def _anchor(lowered: str, terms: Sequence[str], limit: int) -> int:
    seen = {term: _occurrences(lowered, term) for term in set(terms) if term}
    present = {term: found for term, found in seen.items() if found}
    if not present:
        return 0
    rarest = min(present, key=lambda term: (len(present[term]), -len(term)))
    best, score = present[rarest][0], -1
    for anchor in present[rarest]:
        window = lowered[anchor : anchor + limit]
        distinct = sum(1 for term in present if term in window)
        if distinct > score:
            best, score = anchor, distinct
    return best


def _occurrences(text: str, term: str, cap: int = 12) -> list[int]:
    found: list[int] = []
    start = text.find(term)
    while start >= 0 and len(found) < cap:
        found.append(start)
        start = text.find(term, start + 1)
    return found


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] + "…"


def _original_for(document: DocumentRow | None, doc_id: str, page: int) -> str:
    if document is None or format_for_mime(document.mime) is None:
        return ""
    return original_url(doc_id, page if document.mime == PDF_MIME else 0)


def _original_is_download(document: DocumentRow | None) -> bool:
    if document is None or format_for_mime(document.mime) is None:
        return False
    return document.mime != PDF_MIME


def shard_results(
    shard: Shard, hits: Sequence[ShardHit], snippet_chars: int = SNIPPET_CHARS
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for hit in hits:
        document = shard.document(hit.doc_id)
        title = document.title if document is not None else hit.doc_id[:12]
        results.append(
            SearchResult(
                source_kind=SOURCE_DOC,
                source_id=shard.manifest.shard_id,
                source_title=title,
                title=title,
                path=hit.anchor,
                url=document_url(hit.doc_id, hit.anchor),
                rank=hit.rank,
                doc_id=hit.doc_id,
                anchor=hit.anchor,
                page=hit.page,
                snippet=snippet(hit.text, snippet_chars),
                original_url=_original_for(document, hit.doc_id, hit.page),
                original_download=_original_is_download(document),
            )
        )
    return results


def search_shards_by_text(
    shards: Sequence[Shard], query: str, limit: int, snippet_chars: int = SNIPPET_CHARS
) -> tuple[list[list[SearchResult]], list[SourceReport]]:
    groups: list[list[SearchResult]] = []
    reports: list[SourceReport] = []
    for shard in shards:
        try:
            hits = shard.search_text(query, limit)
        except Exception as error:
            reports.append(
                SourceReport(
                    SOURCE_DOC,
                    shard.manifest.shard_id,
                    shard.manifest.shard_id,
                    "fts5",
                    0,
                    0,
                    str(error),
                )
            )
            continue
        results = shard_results(shard, hits, snippet_chars)
        if results:
            groups.append(results)
        reports.append(
            SourceReport(
                SOURCE_DOC,
                shard.manifest.shard_id,
                shard.manifest.shard_id,
                "fts5",
                len(hits),
                len(results),
                "",
            )
        )
    return groups, reports


def search_shards_by_vector(
    shards: Sequence[Shard],
    vector: Sequence[float],
    limit: int,
    snippet_chars: int = SNIPPET_CHARS,
    floor: int = SEMANTIC_FLOOR,
) -> list[SearchResult]:
    least = floor / 100
    scored: list[tuple[Shard, VectorHit]] = []
    for shard in shards:
        if not shard.state.searchable_by_vector:
            continue
        for hit in shard.search_vectors(vector, limit):
            if floor <= 0 or hit.score >= least:
                scored.append((shard, hit))
    scored.sort(key=lambda item: hit_order(item[1]))
    results: list[SearchResult] = []
    for position, (shard, found) in enumerate(scored[:limit]):
        score, chunk_id = found.score, found.chunk_id
        row = shard.chunk(chunk_id)
        if row is None:
            continue
        document = shard.document(row.doc_id)
        title = document.title if document is not None else row.doc_id[:12]
        results.append(
            SearchResult(
                source_kind=SOURCE_DOC,
                source_id=shard.manifest.shard_id,
                source_title=title,
                title=title,
                path=row.anchor,
                url=document_url(row.doc_id, row.anchor),
                rank=position,
                doc_id=row.doc_id,
                anchor=row.anchor,
                page=row.page,
                snippet=snippet(row.text, snippet_chars),
                original_url=_original_for(document, row.doc_id, row.page),
                original_download=_original_is_download(document),
                score=score,
            )
        )
    return results


@dataclass(frozen=True)
class SearchSettings:
    per_source: int = DEFAULT_LIMIT
    zim_weight: int = 1
    doc_weight: int = 1
    snippet_chars: int = SNIPPET_CHARS
    chunk_min_tokens: int = MIN_CHUNK_TOKENS
    chunk_max_tokens: int = MAX_CHUNK_TOKENS
    semantic_floor: int = SEMANTIC_FLOOR


def read_search_settings(connection: sqlite3.Connection) -> SearchSettings:
    try:
        parsed: object = json.loads(read_setting(connection, SETTINGS_KEY, "{}"))
    except ValueError:
        return SearchSettings()
    if not isinstance(parsed, dict):
        return SearchSettings()
    defaults = SearchSettings()
    minimum = _bounded(
        parsed.get("chunk_min_tokens"),
        defaults.chunk_min_tokens,
        MIN_TOKENS_FLOOR,
        MAX_TOKENS_CEILING,
    )
    maximum = _bounded(
        parsed.get("chunk_max_tokens"),
        defaults.chunk_max_tokens,
        MIN_TOKENS_FLOOR,
        MAX_TOKENS_CEILING,
    )
    return SearchSettings(
        per_source=_bounded(parsed.get("per_source"), defaults.per_source, 1, MAX_LIMIT),
        zim_weight=_bounded(parsed.get("zim_weight"), defaults.zim_weight, 1, MAX_WEIGHT),
        doc_weight=_bounded(parsed.get("doc_weight"), defaults.doc_weight, 1, MAX_WEIGHT),
        snippet_chars=_bounded(
            parsed.get("snippet_chars"),
            defaults.snippet_chars,
            MIN_SNIPPET_CHARS,
            MAX_SNIPPET_CHARS,
        ),
        chunk_min_tokens=min(minimum, maximum),
        chunk_max_tokens=max(minimum, maximum),
        semantic_floor=_bounded(
            parsed.get("semantic_floor"), defaults.semantic_floor, 0, MAX_FLOOR
        ),
    )


def write_search_settings(
    connection: sqlite3.Connection, settings: SearchSettings
) -> SearchSettings:
    write_setting(
        connection,
        SETTINGS_KEY,
        json.dumps(
            {
                "per_source": settings.per_source,
                "zim_weight": settings.zim_weight,
                "doc_weight": settings.doc_weight,
                "snippet_chars": settings.snippet_chars,
                "chunk_min_tokens": settings.chunk_min_tokens,
                "chunk_max_tokens": settings.chunk_max_tokens,
                "semantic_floor": settings.semantic_floor,
            }
        ),
    )
    return read_search_settings(connection)


def read_search_payload(payload: Mapping[str, object], current: SearchSettings) -> SearchSettings:
    merged = {
        "per_source": payload.get("per_source", current.per_source),
        "zim_weight": payload.get("zim_weight", current.zim_weight),
        "doc_weight": payload.get("doc_weight", current.doc_weight),
        "snippet_chars": payload.get("snippet_chars", current.snippet_chars),
        "chunk_min_tokens": payload.get("chunk_min_tokens", current.chunk_min_tokens),
        "chunk_max_tokens": payload.get("chunk_max_tokens", current.chunk_max_tokens),
        "semantic_floor": payload.get("semantic_floor", current.semantic_floor),
    }
    defaults = SearchSettings()
    minimum = _bounded(
        merged["chunk_min_tokens"], defaults.chunk_min_tokens, MIN_TOKENS_FLOOR, MAX_TOKENS_CEILING
    )
    maximum = _bounded(
        merged["chunk_max_tokens"], defaults.chunk_max_tokens, MIN_TOKENS_FLOOR, MAX_TOKENS_CEILING
    )
    return SearchSettings(
        per_source=_bounded(merged["per_source"], defaults.per_source, 1, MAX_LIMIT),
        zim_weight=_bounded(merged["zim_weight"], defaults.zim_weight, 1, MAX_WEIGHT),
        doc_weight=_bounded(merged["doc_weight"], defaults.doc_weight, 1, MAX_WEIGHT),
        snippet_chars=_bounded(
            merged["snippet_chars"], defaults.snippet_chars, MIN_SNIPPET_CHARS, MAX_SNIPPET_CHARS
        ),
        chunk_min_tokens=min(minimum, maximum),
        chunk_max_tokens=max(minimum, maximum),
        semantic_floor=_bounded(merged["semantic_floor"], defaults.semantic_floor, 0, MAX_FLOOR),
    )


def _bounded(value: object, fallback: int, low: int, high: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    return max(low, min(fallback, high))
