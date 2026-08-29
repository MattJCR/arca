from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.chat import (
    STATE_OK,
    STATE_TRUNCATED,
    STATE_UNAVAILABLE,
    STATE_UNGROUNDED,
    WARNING_RISK_WITHOUT_CITATION,
    ChatEngine,
    ChatSettings,
    citations_in,
    evidence_block,
    read_screen,
    read_settings,
    read_settings_payload,
    screen_state,
    select_evidence,
    write_settings,
)
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_rw
from arca.i18n import load_catalog
from arca.llama import Completion
from arca.search import SearchResult
from tests.support import free_port, stub_args, write_gguf
from tests.test_api import http_get, http_request

CATALOG = load_catalog("es")


def result(marker: int, text: str, kind: str = "doc", score: float = 0.0) -> SearchResult:
    return SearchResult(
        source_kind=kind,
        source_id="shard-1",
        source_title=f"Manual {marker}",
        title=f"Manual {marker}",
        path=f"p:000{marker}:01",
        url=f"/api/doc/{'a' * 64}#p:000{marker}:01",
        rank=marker - 1,
        doc_id="a" * 64,
        anchor=f"p:000{marker}:01",
        page=marker,
        snippet=text,
        original_url=f"/api/doc/{'a' * 64}/original#page={marker}",
        score=score,
    )


class FakeModel:
    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(
        self,
        base_url: str,
        messages: Sequence[Mapping[str, str]],
        sampling: Mapping[str, object] | None = None,
        timeout: float = 0.0,
    ) -> Completion:
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            return Completion(text="no more replies")
        return Completion(text=self.replies.pop(0))


def engine_for(
    results: Sequence[SearchResult],
    replies: Sequence[str],
    available: bool = True,
) -> tuple[ChatEngine, FakeModel, list[str]]:
    model = FakeModel(replies)
    asked: list[str] = []

    def searcher(query: str, scope: str) -> list[SearchResult]:
        asked.append(query)
        return list(results)

    engine = ChatEngine(
        searcher=searcher,
        base_url=lambda: "http://127.0.0.1:9",
        available=lambda: available,
        completer=model,
    )
    return engine, model, asked


def test_an_answer_cites_the_paragraph_it_came_from() -> None:
    engine, model, _ = engine_for(
        [result(1, "The pump is primed before opening the valve.")], ["Prime the pump first [1]."]
    )
    reply = engine.reply("How is the pump primed?", read_screen({}), ChatSettings(), CATALOG)
    assert reply.state == STATE_OK
    assert reply.citations == (1,)
    assert reply.evidence[0].anchor == "p:0001:01"
    assert reply.evidence[0].url.endswith("#p:0001:01")
    assert model.calls


def test_a_question_outside_the_corpus_is_answered_and_marked_as_unbacked() -> None:
    engine, model, asked = engine_for([], ["Kepler-22b takes about 290 days."])
    reply = engine.reply(
        "What is the orbit of Kepler-22b?", read_screen({}), ChatSettings(), CATALOG
    )
    assert reply.state == STATE_UNGROUNDED
    assert reply.text == "Kepler-22b takes about 290 days."
    assert reply.evidence == ()
    assert reply.citations == ()
    assert model.calls
    assert asked


def test_the_prompt_tells_the_model_it_may_answer_without_evidence() -> None:
    engine, model, _ = engine_for([], ["anything"])
    engine.reply("hola", read_screen({}), ChatSettings(), CATALOG)
    system = model.calls[0][0]["content"]
    context = model.calls[0][1]["content"]
    assert CATALOG.text("chat.prompt.no_evidence") in context
    assert "propio conocimiento" in system


def test_without_a_model_the_chat_says_so_and_search_is_untouched() -> None:
    engine, model, asked = engine_for([result(1, "text")], ["never used"], available=False)
    reply = engine.reply("anything", read_screen({}), ChatSettings(), CATALOG)
    assert reply.state == STATE_UNAVAILABLE
    assert reply.text == CATALOG.text("chat.unavailable")
    assert not model.calls
    assert not asked


def test_the_evidence_is_capped_at_five_chunks() -> None:
    results = [result(index, f"paragraph {index} " * 20) for index in range(1, 9)]
    chosen = select_evidence(results, ChatSettings())
    assert len(chosen) == 5
    assert [item.marker for item in chosen] == [1, 2, 3, 4, 5]


def test_the_evidence_is_capped_by_tokens_too() -> None:
    results = [result(index, "word " * 900) for index in range(1, 6)]
    chosen = select_evidence(results, ChatSettings())
    assert len(chosen) == 1


def test_the_model_may_search_twice_and_then_must_answer() -> None:
    engine, model, asked = engine_for(
        [result(1, "the valve seat and its gasket, question by question")],
        ["SEARCH: valve seat", "SEARCH: valve gasket", "SEARCH: third try"],
    )
    reply = engine.reply("valve question", read_screen({}), ChatSettings(), CATALOG)
    assert asked == ["valve question", "valve seat", "valve gasket"]
    assert len(model.calls) == 3
    assert reply.text == CATALOG.text("chat.search_budget_spent")


def test_a_risk_question_without_citations_raises_a_visible_warning() -> None:
    engine, _, _ = engine_for(
        [result(1, "The medicine dose for an adult is two tablets every eight hours.")],
        ["Take two of them and rest."],
    )
    reply = engine.reply(
        "What dose of the medicine do I take?", read_screen({}), ChatSettings(), CATALOG
    )
    assert reply.risk
    assert reply.warning == WARNING_RISK_WITHOUT_CITATION
    assert not reply.citations


def test_a_risk_question_with_a_citation_carries_no_warning() -> None:
    engine, _, _ = engine_for(
        [result(1, "The medicine dose for an adult is two tablets every eight hours.")],
        ["The manual says two [1]."],
    )
    reply = engine.reply(
        "What dose of the medicine do I take?", read_screen({}), ChatSettings(), CATALOG
    )
    assert reply.risk
    assert reply.warning == ""
    assert reply.citations == (1,)


def test_a_harmless_question_is_not_marked_as_risky() -> None:
    engine, _, _ = engine_for([result(1, "the pump is primed by hand")], ["Prime it."])
    reply = engine.reply("How is the pump primed?", read_screen({}), ChatSettings(), CATALOG)
    assert not reply.risk
    assert reply.warning == ""


def test_the_model_sees_a_structured_screen_never_raw_html() -> None:
    screen = read_screen(
        {
            "query": "pump",
            "results": [{"title": "<b>Pump</b>", "source": "Manual", "snippet": "primed"}],
            "open_document": {"doc_id": "b" * 64, "title": "Pump manual", "page": 7},
            "selection": {"text": "the valve", "doc_id": "b" * 64, "anchor": "p:0007:03"},
        }
    )
    state = screen_state(screen)
    assert set(state) == {"query", "results", "open_document", "selection"}
    assert state["open_document"]["page"] == 7
    assert state["selection"]["anchor"] == "p:0007:03"
    assert "<b>" in state["results"][0]["title"]
    assert json.loads(json.dumps(state))["query"] == "pump"


def test_a_selection_on_screen_is_searched_first() -> None:
    engine, _, asked = engine_for(
        [result(1, "the sealing ring is replaced with the pump cold")], ["Answer [1]."]
    )
    screen = read_screen({"selection": {"text": "sealing ring", "doc_id": "c" * 64}})
    engine.reply("what is this", screen, ChatSettings(), CATALOG)
    assert asked[0] == "sealing ring"


def test_screen_input_that_is_junk_is_ignored() -> None:
    screen = read_screen({"query": 5, "results": "no", "open_document": 3, "selection": []})
    assert screen.query == ""
    assert screen.results == ()
    assert screen.open_document is None
    assert screen.selection is None


def test_every_chunk_reaches_the_model_stamped_with_its_provenance() -> None:
    block = evidence_block(select_evidence([result(1, "primed")], ChatSettings()))
    assert "[1]" in block
    assert "p:0001:01" in block
    assert "Manual 1" in block


def test_citations_that_do_not_exist_are_not_reported() -> None:
    evidence = select_evidence([result(1, "one")], ChatSettings())
    assert citations_in("As [1] and [7] say", evidence) == (1,)


def test_chat_settings_round_trip_and_respect_the_hard_caps(tmp_path: Path) -> None:
    connection = open_library_rw(library_db_path(tmp_path))
    try:
        stored = write_settings(
            connection,
            read_settings_payload(
                {
                    "max_chunks": 99,
                    "max_evidence_tokens": 99999,
                    "max_tool_calls": 9,
                    "system_prompt": "be brief",
                    "risk_topics": ["voltaje"],
                    "sampling": {"temperature": 0.1},
                },
                ChatSettings(),
            ),
        )
        assert stored.max_chunks == 5
        assert stored.max_evidence_tokens == 1500
        assert stored.max_tool_calls == 2
        assert stored.system_prompt == "be brief"
        assert stored.risk_topics == ("voltaje",)
        assert stored.sampling == {"temperature": 0.1}
        assert read_settings(connection) == stored
    finally:
        connection.close()


def test_the_default_risk_topics_cover_both_languages() -> None:
    topics = ChatSettings().risk_topics
    assert "salud" in topics
    assert "health" in topics


@pytest.fixture
def library(tmp_path: Path) -> Iterator[tuple[Path, FastAPI]]:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(llama_bin=sys.executable, chat_url="http://127.0.0.1:1")
    app = create_app(root, config)
    yield root, app
    http_request(app, "POST", "/api/admin/llama/stop")


def _json(app: FastAPI, path: str, query: str = "") -> dict[str, Any]:
    status, body = http_get(app, path, query=query)
    assert status == 200
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def test_the_chat_endpoint_degrades_when_no_model_answers(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    status, body = http_request(app, "POST", "/api/chat", json_body={"message": "hola"})
    assert status == 200
    payload = json.loads(body)
    assert payload["state"] == STATE_UNAVAILABLE
    assert payload["text"] == CATALOG.text("chat.unavailable")
    assert http_get(app, "/api/search")[0] == 200


def test_the_dashboard_reports_a_binary_and_its_devices(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    payload = _json(app, "/api/admin/llama")
    assert payload["binary"]["present"] is True
    assert payload["server"]["running"] is False
    assert payload["profiles"] == []


def test_the_help_form_falls_back_to_the_vendored_capture(tmp_path: Path) -> None:
    from tests.fixtures.llama_server_stub import HELP_TEXT

    root = tmp_path / "library"
    init_library(root)
    capture = root / "vendor" / "llama.cpp" / "llama-server-help.txt"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text(HELP_TEXT, "utf-8")
    app = create_app(root, ArcaConfig(llama_bin=str(root / "absent")))
    payload = _json(app, "/api/admin/llama/help")
    assert payload["source"] == "capture"
    groups = {group["group"]: group for group in payload["groups"]}
    assert "context" in groups
    assert any(option["flag"] == "--ctx-size" for option in groups["context"]["options"])


def test_profiles_are_created_edited_and_deleted_through_the_api(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    status, body = http_request(
        app,
        "PUT",
        "/api/admin/llama/profiles/cpu",
        json_body={"mode": "form", "args": {"--ctx-size": "2048"}},
    )
    assert status == 200
    saved = json.loads(body)
    assert saved["is_default"] is True
    assert "--ctx-size 2048" in saved["command_line"]
    listing = _json(app, "/api/admin/llama/profiles")
    assert [profile["name"] for profile in listing["profiles"]] == ["cpu"]
    assert http_request(app, "DELETE", "/api/admin/llama/profiles/cpu")[0] == 200
    assert _json(app, "/api/admin/llama/profiles")["profiles"] == []


def test_a_forged_profile_name_is_refused(library: tuple[Path, FastAPI]) -> None:
    _, app = library
    status, _ = http_request(
        app, "PUT", "/api/admin/llama/profiles/bad$name", json_body={"mode": "form"}
    )
    assert status == 400


def test_the_llama_endpoints_name_every_refusal_with_a_code(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    forged = http_request(
        app, "PUT", "/api/admin/llama/profiles/bad$name", json_body={"mode": "form"}
    )
    mode = http_request(
        app, "PUT", "/api/admin/llama/profiles/cpu", json_body={"mode": "telepathy"}
    )
    absent = http_request(app, "DELETE", "/api/admin/llama/profiles/ghost")
    trial = http_request(app, "POST", "/api/admin/llama/profiles/ghost/test")
    start = http_request(app, "POST", "/api/admin/llama/start")
    assert json.loads(forged[1])["code"] == "invalid_name"
    assert json.loads(mode[1])["code"] == "unknown_mode"
    assert json.loads(absent[1])["code"] == "unknown_profile"
    assert json.loads(trial[1])["code"] == "unknown_profile"
    assert json.loads(start[1])["code"] == "no_profile"


def test_a_missing_binary_is_reported_with_its_own_code(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig(llama_bin=str(root / "absent")))
    trial = http_request(app, "POST", "/api/admin/llama/profiles/any/test")
    start = http_request(app, "POST", "/api/admin/llama/start")
    assert trial[0] == 409
    assert json.loads(trial[1])["code"] == "no_binary"
    assert start[0] == 409
    assert json.loads(start[1])["code"] == "no_binary"


def test_a_working_profile_starts_and_a_broken_one_rolls_back(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    good_args = stub_args(free_port())
    http_request(
        app,
        "PUT",
        "/api/admin/llama/profiles/good",
        json_body={"mode": "raw", "raw_cmd": " ".join(good_args)},
    )
    status, body = http_request(app, "POST", "/api/admin/llama/profiles/good/test")
    assert status == 200
    trial = json.loads(body)
    assert trial["ok"] is True
    assert "stub reply" in trial["reply"]
    broken_args = stub_args(free_port(), "--stub-fail")
    http_request(
        app,
        "PUT",
        "/api/admin/llama/profiles/broken",
        json_body={"mode": "raw", "raw_cmd": " ".join(broken_args)},
    )
    status, body = http_request(app, "POST", "/api/admin/llama/profiles/broken/test")
    assert status == 200
    failed = json.loads(body)
    assert failed["ok"] is False
    assert failed["stage"] == "health"
    assert failed["rolled_back_to"] == "good"
    assert failed["rollback_ok"] is True
    assert any("refusing to start" in line for line in failed["log"])
    state = _json(app, "/api/admin/llama")
    assert state["active"] == "good"
    assert state["server"]["health"] == "ok"


def test_the_server_can_be_stopped_and_started_from_the_dashboard(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    http_request(
        app,
        "PUT",
        "/api/admin/llama/profiles/good",
        json_body={"mode": "raw", "raw_cmd": " ".join(stub_args(free_port()))},
    )
    status, body = http_request(app, "POST", "/api/admin/llama/start")
    assert status == 200
    assert json.loads(body)["server"]["running"] is True
    status, body = http_request(app, "POST", "/api/admin/llama/stop")
    assert json.loads(body)["server"]["running"] is False
    assert http_request(app, "POST", "/api/admin/llama/fly")[0] == 400


def test_chat_answers_over_a_running_stub_and_stops_when_it_dies(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    http_request(
        app,
        "PUT",
        "/api/admin/llama/profiles/good",
        json_body={"mode": "raw", "raw_cmd": " ".join(stub_args(free_port()))},
    )
    http_request(app, "POST", "/api/admin/llama/profiles/good/test")
    status, body = http_request(app, "POST", "/api/chat", json_body={"message": "hola"})
    assert status == 200
    assert json.loads(body)["state"] == STATE_UNGROUNDED
    http_request(app, "POST", "/api/admin/llama/stop")
    status, body = http_request(app, "POST", "/api/chat", json_body={"message": "hola"})
    payload = json.loads(body)
    assert payload["state"] == STATE_UNAVAILABLE
    assert http_get(app, "/api/search")[0] == 200


def test_chat_settings_are_read_and_written_through_the_settings_endpoint(
    library: tuple[Path, FastAPI],
) -> None:
    _, app = library
    payload = _json(app, "/api/admin/settings")
    assert payload["chat"]["limits"]["max_chunks"] == 5
    status, body = http_request(
        app,
        "PUT",
        "/api/admin/settings",
        json_body={"chat": {"max_chunks": 3, "system_prompt": "cita siempre"}},
    )
    assert status == 200
    stored = json.loads(body)["chat"]
    assert stored["max_chunks"] == 3
    assert stored["system_prompt"] == "cita siempre"
    assert http_request(app, "PUT", "/api/admin/settings", json_body={})[0] == 400


def test_a_missing_chat_model_is_red_in_the_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    write_gguf(root / "models" / "chat" / "chat.gguf")
    config = ArcaConfig(chat_model="chat.gguf")
    checks = {
        check["id"]: check
        for check in _json(create_app(root, config), "/api/diagnostics")["checks"]
    }
    assert checks["chat_model"]["state"] == "ok"
    (root / "models" / "chat" / "chat.gguf").unlink()
    app = create_app(root, config)
    payload = _json(app, "/api/diagnostics")
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["chat_model"]["state"] == "broken"
    assert checks["chat_model"]["action"]
    assert http_get(app, "/api/search")[0] == 200
    assert http_get(app, "/")[0] == 200


def test_an_empty_library_reports_the_chat_as_degraded_not_broken(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(llama_bin=str(root / "absent-llama-server"), chat_url="http://127.0.0.1:1")
    payload = _json(create_app(root, config), "/api/diagnostics")
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["chat_model"]["state"] == "degraded"
    assert checks["llama_server"]["state"] == "degraded"


def test_evidence_that_shares_nothing_with_the_question_is_not_evidence() -> None:
    engine, model, _ = engine_for(
        [result(1, "The feed pump is primed through the casing plug.")],
        ["invented answer"],
    )
    reply = engine.reply(
        "What is the orbital period of Kepler-22b?", read_screen({}), ChatSettings(), CATALOG
    )
    assert reply.evidence == ()
    assert reply.state == STATE_UNGROUNDED
    assert model.calls


def test_a_strong_semantic_score_keeps_evidence_without_any_shared_word() -> None:
    close = result(1, "Se ceba la bomba por el tapon de la carcasa", score=0.9)
    far = result(2, "Se ceba la bomba por el tapon de la carcasa", score=0.1)
    assert len(select_evidence([close], ChatSettings(), query="How is the pump primed?")) == 1
    assert select_evidence([far], ChatSettings(), query="How is the pump primed?") == []


def test_the_open_page_is_read_and_offered_as_evidence() -> None:
    model = FakeModel(["The page says photosynthesis needs light [1]."])
    pages: list[tuple[str, str, str]] = []

    def reader(kind: str, source_id: str, path: str) -> str:
        pages.append((kind, source_id, path))
        return "<p>Photosynthesis needs light.</p>"

    engine = ChatEngine(
        searcher=lambda query, scope: [],
        base_url=lambda: "http://127.0.0.1:9",
        available=lambda: True,
        completer=model,
        reader=reader,
    )
    screen = read_screen(
        {
            "open_document": {
                "title": "Plants",
                "source_kind": "zim",
                "source_id": "uuid-1",
                "path": "plants.html",
            }
        }
    )
    reply = engine.reply("¿de qué va esta página?", screen, ChatSettings(), CATALOG)
    assert pages == [("zim", "uuid-1", "plants.html")]
    assert reply.evidence[0].text.startswith("<p>Photosynthesis")
    assert reply.state == STATE_OK
    assert reply.citations == (1,)


def test_a_page_that_cannot_be_read_does_not_break_the_turn() -> None:
    model = FakeModel(["I could not read it, so from my own knowledge…"])

    def reader(kind: str, source_id: str, path: str) -> str:
        raise LookupError("gone")

    engine = ChatEngine(
        searcher=lambda query, scope: [],
        base_url=lambda: "http://127.0.0.1:9",
        available=lambda: True,
        completer=model,
        reader=reader,
    )
    screen = read_screen(
        {"open_document": {"title": "Plants", "source_kind": "zim", "source_id": "gone"}}
    )
    reply = engine.reply("¿de qué va?", screen, ChatSettings(), CATALOG)
    assert reply.evidence == ()
    assert reply.state == STATE_UNGROUNDED


def test_a_selection_inside_a_zim_article_keeps_its_provenance() -> None:
    screen = read_screen(
        {
            "selection": {
                "text": "photosynthesis",
                "source_kind": "zim",
                "source_id": "uuid-1",
                "path": "plants.html",
            }
        }
    )
    assert screen.selection is not None
    assert screen.selection.source_kind == "zim"
    assert screen.selection.source_id == "uuid-1"
    assert screen.selection.doc_id == ""
    state = screen_state(screen)
    assert state["selection"]["path"] == "plants.html"


def test_a_model_that_runs_out_of_room_says_so_instead_of_blaming_the_library() -> None:
    def starved(base_url, messages, sampling=None, timeout=0.0):  # type: ignore[no-untyped-def]
        return Completion(text="", finish_reason="length", reasoning="thinking " * 200)

    engine = ChatEngine(
        searcher=lambda query, scope: [],
        base_url=lambda: "http://127.0.0.1:9",
        available=lambda: True,
        completer=starved,
    )
    reply = engine.reply("anything", read_screen({}), ChatSettings(), CATALOG)
    assert reply.state == STATE_TRUNCATED
    assert reply.text == CATALOG.text("chat.truncated")
    assert reply.text != CATALOG.text("chat.no_evidence")


def test_the_reasoning_of_a_thinking_model_is_read_but_never_shown_as_the_answer() -> None:
    from arca.llama import _completion

    raw = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "let me think"},
                }
            ]
        }
    ).encode()
    completion = _completion(raw)
    assert completion.reasoning == "let me think"
    assert completion.text == ""
    assert completion.out_of_room


def test_the_default_token_budget_leaves_room_for_a_thinking_model() -> None:
    assert ChatSettings().sampling["n_predict"] >= 2048
