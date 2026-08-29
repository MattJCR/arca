from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from arca.db import read_setting, write_setting
from arca.i18n import SUPPORTED_LANGUAGES, Catalog, load_catalog
from arca.ingest import estimate_tokens
from arca.llama import Completion, chat_completion
from arca.search import SearchResult

SETTINGS_KEY = "chat"
MAX_CHUNKS = 5
MAX_EVIDENCE_TOKENS = 1500
MAX_TOOL_CALLS = 2
MAX_MESSAGE_CHARS = 4000
MAX_SCREEN_RESULTS = 5
MAX_SNIPPET_CHARS = 320
MAX_PAGE_CHARS = 3000
SEARCH_PREFIX = "SEARCH:"
MARKER_PATTERN = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")
TERM_PATTERN = re.compile(r"[a-z0-9]+")
MIN_TERM_LENGTH = 4
MIN_SEMANTIC_SCORE = 0.75
DEFAULT_SAMPLING: dict[str, Any] = {"temperature": 0.2, "top_p": 0.9, "n_predict": 2048}
RISK_TOPICS_KEY = "chat.risk_topics"

STATE_OK = "ok"
STATE_UNAVAILABLE = "unavailable"
STATE_NO_EVIDENCE = "no_evidence"
STATE_UNGROUNDED = "ungrounded"
STATE_TRUNCATED = "truncated"
STATE_ERROR = "error"
WARNING_RISK_WITHOUT_CITATION = "risk_without_citation"


def default_risk_topics() -> tuple[str, ...]:
    topics: set[str] = set()
    for language in SUPPORTED_LANGUAGES:
        listed = load_catalog(language).text(RISK_TOPICS_KEY)
        topics.update(part.strip().lower() for part in listed.split(",") if part.strip())
    return tuple(sorted(topics))


@dataclass(frozen=True)
class ChatSettings:
    max_chunks: int = MAX_CHUNKS
    max_evidence_tokens: int = MAX_EVIDENCE_TOKENS
    max_tool_calls: int = MAX_TOOL_CALLS
    system_prompt: str = ""
    risk_topics: tuple[str, ...] = field(default_factory=default_risk_topics)
    sampling: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SAMPLING))


@dataclass(frozen=True)
class Selection:
    text: str = ""
    doc_id: str = ""
    anchor: str = ""
    page: int = 0
    source_kind: str = ""
    source_id: str = ""
    path: str = ""


@dataclass(frozen=True)
class OpenDocument:
    doc_id: str = ""
    title: str = ""
    page: int = 0
    source_kind: str = ""
    source_id: str = ""
    path: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.source_kind and self.source_id)


@dataclass(frozen=True)
class ScreenResult:
    title: str
    source: str
    snippet: str


@dataclass(frozen=True)
class Screen:
    query: str = ""
    results: tuple[ScreenResult, ...] = ()
    open_document: OpenDocument | None = None
    selection: Selection | None = None


@dataclass(frozen=True)
class Evidence:
    marker: int
    source_kind: str
    source_title: str
    title: str
    doc_id: str
    anchor: str
    page: int
    text: str
    url: str
    original_url: str


@dataclass(frozen=True)
class ChatReply:
    text: str
    state: str
    evidence: tuple[Evidence, ...] = ()
    citations: tuple[int, ...] = ()
    searches: tuple[str, ...] = ()
    risk: bool = False
    warning: str = ""
    error: str = ""


Searcher = Callable[[str, str], list[SearchResult]]
PageReader = Callable[[str, str, str], str]


def read_settings(connection: sqlite3.Connection) -> ChatSettings:
    try:
        parsed: object = json.loads(read_setting(connection, SETTINGS_KEY, "{}"))
    except ValueError:
        return ChatSettings()
    if not isinstance(parsed, dict):
        return ChatSettings()
    defaults = ChatSettings()
    return ChatSettings(
        max_chunks=_positive(parsed.get("max_chunks"), defaults.max_chunks, MAX_CHUNKS),
        max_evidence_tokens=_positive(
            parsed.get("max_evidence_tokens"), defaults.max_evidence_tokens, MAX_EVIDENCE_TOKENS
        ),
        max_tool_calls=_bounded(parsed.get("max_tool_calls"), defaults.max_tool_calls),
        system_prompt=_text(parsed.get("system_prompt")),
        risk_topics=_topics(parsed.get("risk_topics"), defaults.risk_topics),
        sampling=_sampling(parsed.get("sampling")),
    )


def write_settings(connection: sqlite3.Connection, settings: ChatSettings) -> ChatSettings:
    payload = {
        "max_chunks": settings.max_chunks,
        "max_evidence_tokens": settings.max_evidence_tokens,
        "max_tool_calls": settings.max_tool_calls,
        "system_prompt": settings.system_prompt,
        "risk_topics": list(settings.risk_topics),
        "sampling": settings.sampling,
    }
    write_setting(connection, SETTINGS_KEY, json.dumps(payload, ensure_ascii=False))
    return read_settings(connection)


def read_screen(payload: Mapping[str, object]) -> Screen:
    results: list[ScreenResult] = []
    raw_results = payload.get("results")
    if isinstance(raw_results, list):
        for item in raw_results[:MAX_SCREEN_RESULTS]:
            if not isinstance(item, dict):
                continue
            results.append(
                ScreenResult(
                    title=_text(item.get("title"))[:200],
                    source=_text(item.get("source"))[:200],
                    snippet=_text(item.get("snippet"))[:MAX_SNIPPET_CHARS],
                )
            )
    return Screen(
        query=_text(payload.get("query"))[:MAX_MESSAGE_CHARS],
        results=tuple(results),
        open_document=_open_document(payload.get("open_document")),
        selection=_selection(payload.get("selection")),
    )


def screen_state(screen: Screen) -> dict[str, Any]:
    state: dict[str, Any] = {
        "query": screen.query,
        "results": [
            {"title": item.title, "source": item.source, "snippet": item.snippet}
            for item in screen.results
        ],
    }
    if screen.open_document is not None:
        state["open_document"] = {
            "doc_id": screen.open_document.doc_id,
            "source_kind": screen.open_document.source_kind,
            "source_id": screen.open_document.source_id,
            "path": screen.open_document.path,
            "title": screen.open_document.title,
            "page": screen.open_document.page,
        }
    if screen.selection is not None:
        state["selection"] = {
            "text": screen.selection.text,
            "source_kind": screen.selection.source_kind,
            "source_id": screen.selection.source_id,
            "path": screen.selection.path,
            "doc_id": screen.selection.doc_id,
            "anchor": screen.selection.anchor,
        }
    return state


def terms_of(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(letter for letter in folded if not unicodedata.combining(letter))
    return {word for word in TERM_PATTERN.findall(stripped) if len(word) >= MIN_TERM_LENGTH}


def is_relevant(result: SearchResult, terms: set[str]) -> bool:
    if not terms:
        return True
    if result.score >= MIN_SEMANTIC_SCORE:
        return True
    return bool(terms & terms_of(f"{result.title} {result.snippet}"))


def select_evidence(
    results: Sequence[SearchResult],
    settings: ChatSettings,
    taken: Sequence[Evidence] = (),
    query: str = "",
) -> list[Evidence]:
    chosen = list(taken)
    seen = {(item.doc_id, item.anchor, item.url) for item in chosen}
    budget = settings.max_evidence_tokens - sum(estimate_tokens(item.text) for item in chosen)
    terms = terms_of(query)
    for result in results:
        if len(chosen) >= settings.max_chunks:
            break
        key = (result.doc_id, result.anchor, result.url)
        if key in seen or not is_relevant(result, terms):
            continue
        text = result.snippet or result.title
        cost = estimate_tokens(text)
        if cost > budget:
            continue
        seen.add(key)
        budget -= cost
        chosen.append(
            Evidence(
                marker=len(chosen) + 1,
                source_kind=result.source_kind,
                source_title=result.source_title,
                title=result.title,
                doc_id=result.doc_id,
                anchor=result.anchor,
                page=result.page,
                text=text,
                url=result.url,
                original_url=result.original_url,
            )
        )
    return chosen


def evidence_block(evidence: Sequence[Evidence]) -> str:
    lines: list[str] = []
    for item in evidence:
        stamp = f"[{item.marker}] {item.source_title}"
        if item.anchor:
            stamp = f"{stamp} · {item.anchor}"
        if item.page:
            stamp = f"{stamp} · p. {item.page}"
        lines.append(f"{stamp}\n{item.text}")
    return "\n\n".join(lines)


def is_risky(text: str, topics: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(topic and topic.lower() in lowered for topic in topics)


def citations_in(text: str, evidence: Sequence[Evidence]) -> tuple[int, ...]:
    markers = {item.marker for item in evidence}
    found = [int(part) for value in MARKER_PATTERN.findall(text) for part in value.split(",")]
    return tuple(sorted({marker for marker in found if marker in markers}))


def system_prompt(settings: ChatSettings, catalog: Catalog) -> str:
    if settings.system_prompt.strip():
        return settings.system_prompt.strip()
    return catalog.text("chat.prompt.system", search=SEARCH_PREFIX)


class ChatEngine:
    def __init__(
        self,
        searcher: Searcher,
        base_url: Callable[[], str],
        available: Callable[[], bool],
        completer: Callable[..., Completion] = chat_completion,
        reader: PageReader | None = None,
    ) -> None:
        self._search = searcher
        self._base_url = base_url
        self._available = available
        self._complete = completer
        self._reader = reader

    def reply(
        self,
        message: str,
        screen: Screen,
        settings: ChatSettings,
        catalog: Catalog,
    ) -> ChatReply:
        question = message.strip()[:MAX_MESSAGE_CHARS]
        risk = is_risky(f"{question} {screen.query}", settings.risk_topics)
        if not question:
            return ChatReply(catalog.text("chat.empty_question"), STATE_ERROR, risk=risk)
        if not self._available():
            return ChatReply(catalog.text("chat.unavailable"), STATE_UNAVAILABLE, risk=risk)
        searches: list[str] = []
        evidence = self._gather(question, screen, settings, searches)
        return self._answer(question, screen, settings, catalog, evidence, searches, risk)

    def _gather(
        self,
        question: str,
        screen: Screen,
        settings: ChatSettings,
        searches: list[str],
    ) -> list[Evidence]:
        queries = [question]
        if screen.selection is not None and screen.selection.text.strip():
            queries.insert(0, screen.selection.text.strip()[:MAX_SNIPPET_CHARS])
        elif screen.query.strip() and screen.query.strip() != question:
            queries.append(screen.query.strip())
        evidence: list[Evidence] = self._open_page(screen)
        for query in queries:
            if len(evidence) >= settings.max_chunks:
                break
            searches.append(query)
            evidence = select_evidence(self._search(query, ""), settings, evidence, query)
        return evidence

    def _open_page(self, screen: Screen) -> list[Evidence]:
        page = screen.open_document
        if self._reader is None or page is None or not page.readable:
            return []
        try:
            text = self._reader(page.source_kind, page.source_id, page.path)
        except LookupError:
            return []
        text = " ".join(text.split())[:MAX_PAGE_CHARS]
        if not text:
            return []
        return [
            Evidence(
                marker=1,
                source_kind=page.source_kind,
                source_title=page.title or page.path,
                title=page.title or page.path,
                doc_id=page.doc_id,
                anchor=page.path,
                page=page.page,
                text=text,
                url="",
                original_url="",
            )
        ]

    def _answer(
        self,
        question: str,
        screen: Screen,
        settings: ChatSettings,
        catalog: Catalog,
        evidence: list[Evidence],
        searches: list[str],
        risk: bool,
    ) -> ChatReply:
        calls = 0
        while True:
            completion = self._complete(
                self._base_url(),
                self._messages(question, screen, settings, catalog, evidence),
                settings.sampling,
            )
            if completion.error:
                return ChatReply(
                    catalog.text("chat.unavailable"),
                    STATE_UNAVAILABLE,
                    evidence=tuple(evidence),
                    searches=tuple(searches),
                    risk=risk,
                    error=completion.error,
                )
            text = completion.text.strip()
            if completion.out_of_room:
                return ChatReply(
                    catalog.text("chat.truncated"),
                    STATE_TRUNCATED,
                    evidence=tuple(evidence),
                    searches=tuple(searches),
                    risk=risk,
                )
            request = _search_request(text)
            if request and calls < settings.max_tool_calls:
                calls += 1
                searches.append(request)
                evidence = select_evidence(self._search(request, ""), settings, evidence, request)
                continue
            if request:
                text = catalog.text("chat.search_budget_spent")
            return self._finish(text, evidence, searches, settings, risk, catalog)

    def _finish(
        self,
        text: str,
        evidence: list[Evidence],
        searches: list[str],
        settings: ChatSettings,
        risk: bool,
        catalog: Catalog,
    ) -> ChatReply:
        citations = citations_in(text, evidence)
        warning = WARNING_RISK_WITHOUT_CITATION if risk and not citations else ""
        if not text:
            state = STATE_NO_EVIDENCE
        elif citations:
            state = STATE_OK
        else:
            state = STATE_UNGROUNDED
        return ChatReply(
            text=text or catalog.text("chat.no_evidence"),
            state=state,
            evidence=tuple(evidence),
            citations=citations,
            searches=tuple(searches),
            risk=risk,
            warning=warning,
        )

    def _messages(
        self,
        question: str,
        screen: Screen,
        settings: ChatSettings,
        catalog: Catalog,
        evidence: Sequence[Evidence],
    ) -> list[dict[str, str]]:
        context = catalog.text(
            "chat.prompt.context",
            screen=json.dumps(screen_state(screen), ensure_ascii=False),
            evidence=evidence_block(evidence) or catalog.text("chat.prompt.no_evidence"),
            question=question,
        )
        return [
            {"role": "system", "content": system_prompt(settings, catalog)},
            {"role": "user", "content": context},
        ]


def evidence_payload(item: Evidence) -> dict[str, Any]:
    return {
        "marker": item.marker,
        "source_kind": item.source_kind,
        "source_title": item.source_title,
        "title": item.title,
        "doc_id": item.doc_id,
        "anchor": item.anchor,
        "page": item.page,
        "text": item.text,
        "url": item.url,
        "original_url": item.original_url,
    }


def reply_payload(reply: ChatReply) -> dict[str, Any]:
    return {
        "text": reply.text,
        "state": reply.state,
        "evidence": [evidence_payload(item) for item in reply.evidence],
        "citations": list(reply.citations),
        "searches": list(reply.searches),
        "risk": reply.risk,
        "warning": reply.warning,
        "error": reply.error,
    }


def settings_payload(settings: ChatSettings) -> dict[str, Any]:
    return {
        "max_chunks": settings.max_chunks,
        "max_evidence_tokens": settings.max_evidence_tokens,
        "max_tool_calls": settings.max_tool_calls,
        "system_prompt": settings.system_prompt,
        "risk_topics": list(settings.risk_topics),
        "sampling": settings.sampling,
        "limits": {
            "max_chunks": MAX_CHUNKS,
            "max_evidence_tokens": MAX_EVIDENCE_TOKENS,
            "max_tool_calls": MAX_TOOL_CALLS,
        },
    }


def read_settings_payload(payload: Mapping[str, object], current: ChatSettings) -> ChatSettings:
    return ChatSettings(
        max_chunks=_positive(payload.get("max_chunks"), current.max_chunks, MAX_CHUNKS),
        max_evidence_tokens=_positive(
            payload.get("max_evidence_tokens"), current.max_evidence_tokens, MAX_EVIDENCE_TOKENS
        ),
        max_tool_calls=_bounded(payload.get("max_tool_calls"), current.max_tool_calls),
        system_prompt=_text(payload.get("system_prompt")),
        risk_topics=_topics(payload.get("risk_topics"), current.risk_topics),
        sampling=_sampling(payload.get("sampling")),
    )


def _search_request(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(SEARCH_PREFIX):
            return stripped[len(SEARCH_PREFIX) :].strip().strip('"')
    return ""


def _open_document(value: object) -> OpenDocument | None:
    if not isinstance(value, dict):
        return None
    doc_id = _text(value.get("doc_id"))
    title = _text(value.get("title"))
    if not doc_id and not title:
        return None
    return OpenDocument(
        doc_id=doc_id[:128],
        title=title[:200],
        page=_page(value.get("page")),
        source_kind=_text(value.get("source_kind"))[:16],
        source_id=_text(value.get("source_id"))[:128],
        path=_text(value.get("path"))[:512],
    )


def _selection(value: object) -> Selection | None:
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text"))
    if not text.strip():
        return None
    return Selection(
        text=text[:MAX_SNIPPET_CHARS],
        doc_id=_text(value.get("doc_id"))[:128],
        anchor=_text(value.get("anchor"))[:64],
        page=_page(value.get("page")),
        source_kind=_text(value.get("source_kind"))[:16],
        source_id=_text(value.get("source_id"))[:128],
        path=_text(value.get("path"))[:512],
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _page(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def _positive(value: object, fallback: int, ceiling: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, ceiling)
    return min(fallback, ceiling)


def _bounded(value: object, fallback: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_TOOL_CALLS:
        return value
    return min(fallback, MAX_TOOL_CALLS)


def _topics(value: object, fallback: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, list):
        topics = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
        return topics
    return tuple(fallback)


def _sampling(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(DEFAULT_SAMPLING)
    allowed: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, int | float | str | bool):
            allowed[key] = item
    return allowed or dict(DEFAULT_SAMPLING)
