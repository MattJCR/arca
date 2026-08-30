from __future__ import annotations

import json
import urllib.request
from collections.abc import Generator, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, session_dir
from arca.history import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Attachment,
    Message,
    TalkStore,
    image_mimetype,
    talk_dir,
)
from arca.i18n import load_catalog
from arca.llama import (
    SSE_MEDIA_TYPE,
    VISION_MISSING_PROJECTOR,
    VISION_NO_ENGINE,
    VISION_NO_PROJECTOR,
    VISION_READY,
    Chunk,
    Completion,
    chat_stream,
    multimodal_message,
    vision_support,
)
from arca.talk import (
    STATE_CANCELLED,
    STATE_OK,
    STATE_UNAVAILABLE,
    CancelBoard,
    TalkEngine,
    TalkSettings,
    read_settings,
    settings_payload,
    window,
    write_settings,
)
from tests.test_api import http_get, http_request

CATALOG = load_catalog("es")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64


def store_at(tmp_path: Path) -> TalkStore:
    return TalkStore(tmp_path / "session" / "talk")


def talk_app(tmp_path: Path) -> FastAPI:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(
        session_dir=str(tmp_path / "session"), chat_url="http://127.0.0.1:1", auto_engine=False
    )
    return create_app(root, config)


def talk_json(
    app: FastAPI, method: str, path: str, body: dict[str, object] | None = None
) -> dict[str, Any]:
    status, raw = http_request(app, method, path, json_body=body)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    parsed["_status"] = status
    return parsed


def test_the_store_keeps_a_conversation_its_messages_and_its_image(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    conversation = store.create("First one")
    asked = store.add_message(conversation.id, ROLE_USER, "hello")
    assert asked is not None
    attachment = store.attach(conversation.id, PNG)
    assert attachment is not None
    store.bind_attachments(asked.id, [attachment.id])
    kept = store.messages(conversation.id)
    assert [item.content for item in kept] == ["hello"]
    assert kept[0].attachments[0].mimetype == "image/png"
    picture = store.attachment_path(kept[0].attachments[0])
    assert picture is not None and picture.is_file()
    assert store.attachment_bytes(kept[0].attachments[0]) == PNG
    store.close()


def test_deleting_a_conversation_leaves_no_row_and_no_file_on_disk(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    conversation = store.create("To be deleted")
    asked = store.add_message(conversation.id, ROLE_USER, "hello")
    assert asked is not None
    attachment = store.attach(conversation.id, PNG)
    assert attachment is not None
    directory = store.media_dir(conversation.id)
    assert directory is not None and directory.is_dir()
    assert store.delete(conversation.id)
    assert store.conversation(conversation.id) is None
    assert store.messages(conversation.id) == []
    assert store.attachment(attachment.id) is None
    assert not directory.exists()
    store.close()


def test_clearing_the_store_takes_every_conversation_and_every_image(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    directories = []
    for title in ("one", "another"):
        conversation = store.create(title)
        store.attach(conversation.id, PNG)
        directories.append(store.media_dir(conversation.id))
    assert store.clear() == 2
    assert store.conversations() == []
    assert all(directory is not None and not directory.exists() for directory in directories)
    store.close()


def test_the_store_never_lands_inside_the_library_root_even_if_asked_to(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(session_dir=str(root / "inside"))
    directory = talk_dir(root, config)
    assert root.resolve() not in directory.resolve().parents
    assert directory.resolve() != root.resolve()
    assert session_dir(root, config).resolve() != root.resolve()


def test_an_unwritable_session_directory_degrades_to_memory_and_says_so(tmp_path: Path) -> None:
    blocker = tmp_path / "session"
    blocker.write_text("not a directory", "utf-8")
    store = TalkStore(blocker / "talk")
    assert not store.persistent
    assert store.detail
    assert store.path is None
    conversation = store.create("in memory")
    assert store.add_message(conversation.id, ROLE_USER, "hello") is not None
    assert store.attach(conversation.id, PNG) is None
    store.close()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (WEBP, "image/webp"),
        (b"%PDF-1.7\n", ""),
        (b"", ""),
    ],
)
def test_an_image_is_recognised_by_its_bytes_and_never_by_its_name(
    data: bytes, expected: str
) -> None:
    assert image_mimetype(data) == expected


def test_a_conversation_settings_round_trip_lives_in_the_talk_database(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    assert read_settings(store) == TalkSettings()
    saved = write_settings(
        store,
        TalkSettings(
            system_prompt="answer briefly",
            max_history_tokens=1200,
            sampling={"temperature": 0.1, "top_p": 0.5, "n_predict": 64},
        ),
    )
    assert saved.system_prompt == "answer briefly"
    assert saved.sampling["temperature"] == 0.1
    assert settings_payload(read_settings(store))["max_history_tokens"] == 1200
    store.close()


def test_vision_is_declared_off_when_the_profile_carries_no_projector(tmp_path: Path) -> None:
    assert vision_support((), tmp_path).reason == VISION_NO_ENGINE
    assert vision_support(("llama-server", "--model", "m.gguf"), tmp_path).reason == (
        VISION_NO_PROJECTOR
    )
    missing = vision_support(("llama-server", "--mmproj", "mm.gguf"), tmp_path)
    assert missing.reason == VISION_MISSING_PROJECTOR
    assert not missing.available


def test_vision_is_declared_on_when_the_projector_is_really_on_disk(tmp_path: Path) -> None:
    projector = tmp_path / "models" / "chat" / "mmproj.gguf"
    projector.parent.mkdir(parents=True)
    projector.write_bytes(b"GGUF")
    support = vision_support(("llama-server", f"--mmproj={projector}"), tmp_path)
    assert support.available
    assert support.reason == VISION_READY
    assert support.projector == str(projector)


def test_a_message_without_images_stays_a_plain_string() -> None:
    assert multimodal_message(ROLE_USER, "hello") == {"role": "user", "content": "hello"}


def test_a_message_with_an_image_is_serialised_in_parts_with_a_data_uri() -> None:
    message = multimodal_message(ROLE_USER, "what do you see?", [("image/png", PNG)])
    parts = message["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "what do you see?"}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert json.dumps(message)


def message(index: int, role: str, text: str) -> Message:
    return Message(index, 1, role, text, "2026-08-29T00:00:00Z")


def long_history(count: int) -> list[Message]:
    return [
        message(index, ROLE_USER if index % 2 else ROLE_ASSISTANT, f"message {index} " + "x " * 60)
        for index in range(1, count + 1)
    ]


def test_a_long_conversation_is_trimmed_from_the_oldest_end() -> None:
    settings = TalkSettings(max_history_tokens=600)
    history = long_history(60)
    kept = window(history, settings)
    assert 0 < len(kept) < len(history)
    assert kept[-1].id == 60
    assert kept[0].id > 1


def test_a_single_message_over_the_budget_is_still_sent() -> None:
    settings = TalkSettings(max_history_tokens=1)
    kept = window(long_history(3), settings)
    assert [item.id for item in kept] == [3]


class FakeModel:
    def __init__(self, reply: Completion) -> None:
        self.reply = reply
        self.seen: list[Sequence[Mapping[str, object]]] = []

    def __call__(
        self,
        base_url: str,
        messages: Sequence[Mapping[str, object]],
        sampling: Mapping[str, object],
    ) -> Completion:
        self.seen.append(messages)
        return self.reply


def engine(model: FakeModel, available: bool = True, images: bytes = b"") -> TalkEngine:
    return TalkEngine(
        base_url=lambda: "http://127.0.0.1:1",
        available=lambda: available,
        images=lambda attachment: images,
        complete=model,
    )


def test_the_free_engine_keeps_the_system_prompt_and_drops_the_oldest_turns() -> None:
    model = FakeModel(Completion(text="done"))
    settings = TalkSettings(max_history_tokens=600)
    reply = engine(model).reply(long_history(60), settings, CATALOG)
    assert reply.state == STATE_OK
    assert reply.text == "done"
    assert reply.dropped > 0
    sent = model.seen[0]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == CATALOG.text("talk.prompt.system")
    assert len(sent) - 1 == 60 - reply.dropped


def test_with_the_engine_down_nothing_written_is_lost(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    conversation = store.create("no engine")
    store.add_message(conversation.id, ROLE_USER, "hello")
    model = FakeModel(Completion(error="llama-server unreachable"))
    reply = engine(model, available=False).reply(
        store.messages(conversation.id), TalkSettings(), CATALOG
    )
    assert reply.state == STATE_UNAVAILABLE
    assert reply.text == CATALOG.text("talk.unavailable")
    assert model.seen == []
    assert [item.content for item in store.messages(conversation.id)] == ["hello"]
    store.close()


def test_an_attached_image_reaches_the_transport_as_a_part(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    conversation = store.create("with a picture")
    asked = store.add_message(conversation.id, ROLE_USER, "what do you see?")
    assert asked is not None
    attachment = store.attach(conversation.id, PNG)
    assert attachment is not None
    store.bind_attachments(asked.id, [attachment.id])
    model = FakeModel(Completion(text="I see a square"))
    talk = TalkEngine(
        base_url=lambda: "http://127.0.0.1:1",
        available=lambda: True,
        images=store.attachment_bytes,
        complete=model,
    )
    talk.reply(store.messages(conversation.id), TalkSettings(), CATALOG)
    parts = model.seen[0][1]["content"]
    assert isinstance(parts, list)
    assert parts[1]["type"] == "image_url"
    store.close()


def test_the_talk_screen_is_reachable_and_navigates_to_the_other_two(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    status, body = http_get(app, "/talk")
    assert status == 200
    page = body.decode()
    assert 'href="/"' in page
    assert 'href="/admin"' in page
    assert '<script src="/static/talk.js"></script>' in page
    for path in ("/", "/admin"):
        assert b'href="/talk"' in http_get(app, path)[1]


def test_the_screen_still_answers_with_the_engine_down(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    payload = talk_json(app, "GET", "/api/talk")
    assert payload["_status"] == 200
    assert payload["ready"] is False
    assert payload["vision"]["available"] is False
    assert payload["conversations"] == []


def test_the_api_creates_renames_and_deletes_a_conversation(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    created = talk_json(app, "POST", "/api/talk", {"title": "  My chat  "})
    assert created["_status"] == 201
    assert created["title"] == "My chat"
    conversation_id = created["id"]
    renamed = talk_json(app, "PUT", f"/api/talk/{conversation_id}", {"title": "Another"})
    assert renamed["title"] == "Another"
    listed = talk_json(app, "GET", "/api/talk")
    assert [item["title"] for item in listed["conversations"]] == ["Another"]
    removed = talk_json(app, "DELETE", f"/api/talk/{conversation_id}")
    assert removed["deleted"] == conversation_id
    assert talk_json(app, "GET", f"/api/talk/{conversation_id}")["code"] == "unknown_conversation"


def test_an_untitled_conversation_gets_the_catalog_name(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    created = talk_json(app, "POST", "/api/talk", {"title": ""})
    assert created["title"] == CATALOG.text("talk.untitled")


def test_a_message_is_stored_with_the_answer_even_when_the_model_is_down(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    conversation_id = talk_json(app, "POST", "/api/talk", {"title": "a chat"})["id"]
    answered = talk_json(app, "POST", f"/api/talk/{conversation_id}/message", {"message": "hello"})
    assert answered["_status"] == 200
    assert answered["asked"]["content"] == "hello"
    assert answered["reply"]["state"] == "unavailable"
    assert answered["answered"]["role"] == ROLE_ASSISTANT
    opened = talk_json(app, "GET", f"/api/talk/{conversation_id}")
    assert [item["role"] for item in opened["messages"]] == [ROLE_USER, ROLE_ASSISTANT]


def test_an_empty_message_is_refused_with_a_code(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    conversation_id = talk_json(app, "POST", "/api/talk", {"title": "a chat"})["id"]
    refused = talk_json(app, "POST", f"/api/talk/{conversation_id}/message", {"message": "  "})
    assert refused["_status"] == 400
    assert refused["code"] == "empty_message"


def test_a_message_to_an_unknown_conversation_is_refused_with_a_code(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    refused = talk_json(app, "POST", "/api/talk/999/message", {"message": "hello"})
    assert refused["_status"] == 404
    assert refused["code"] == "unknown_conversation"


def test_an_image_upload_is_refused_while_the_model_has_no_vision(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    conversation_id = talk_json(app, "POST", "/api/talk", {"title": "a chat"})["id"]
    status, raw = http_request(
        app,
        "POST",
        f"/api/talk/{conversation_id}/image",
        raw_body=PNG,
        content_type="image/png",
    )
    assert status == 409
    assert json.loads(raw)["code"] == "no_vision"


def test_a_file_that_is_not_an_image_is_refused_however_it_is_named(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(
        session_dir=str(tmp_path / "session"), chat_url="http://127.0.0.1:1", auto_engine=False
    )
    store = TalkStore(talk_dir(root, config))
    conversation = store.create("a chat")
    assert store.attach(conversation.id, b"%PDF-1.7 pretending to be a png") is None
    assert store.messages(conversation.id) == []
    store.close()


def test_the_stored_image_is_served_back_with_its_own_type(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(
        session_dir=str(tmp_path / "session"), chat_url="http://127.0.0.1:1", auto_engine=False
    )
    store = TalkStore(talk_dir(root, config))
    conversation = store.create("a chat")
    attachment = store.attach(conversation.id, PNG)
    assert attachment is not None
    store.close()
    app = create_app(root, config)
    status, raw = http_get(app, f"/api/talk/{conversation.id}/image/{attachment.id}")
    assert status == 200
    assert raw == PNG
    assert http_get(app, f"/api/talk/{conversation.id}/image/9999")[0] == 404


def test_the_settings_endpoint_reads_back_what_it_wrote(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    saved = talk_json(
        app,
        "PUT",
        "/api/talk/settings",
        {
            "system_prompt": "answer in one line",
            "max_history_tokens": 900,
            "sampling": {"temperature": 0.3, "top_p": 0.9, "n_predict": 128},
        },
    )
    assert saved["system_prompt"] == "answer in one line"
    assert saved["max_history_tokens"] == 900
    assert talk_json(app, "GET", "/api/talk/settings")["sampling"]["n_predict"] == 128


def test_deleting_everything_from_the_api_empties_the_list(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    for title in ("one", "another"):
        talk_json(app, "POST", "/api/talk", {"title": title})
    assert talk_json(app, "DELETE", "/api/talk")["removed"] == 2
    assert talk_json(app, "GET", "/api/talk")["conversations"] == []


def test_the_attachment_payload_points_at_its_own_conversation(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    conversation = store.create("a chat")
    attachment = store.attach(conversation.id, JPEG)
    assert attachment is not None
    assert isinstance(attachment, Attachment)
    path = store.attachment_path(attachment)
    assert path is not None
    assert path.parent.name == str(conversation.id)
    assert path.suffix == ".jpg"
    store.close()


def sse(payload: str) -> list[bytes]:
    return [f"data: {payload}\n".encode(), b"\n"]


def delta_lines(*pieces: str) -> list[bytes]:
    lines: list[bytes] = []
    for piece in pieces:
        lines.extend(sse(json.dumps({"choices": [{"delta": {"content": piece}}]})))
    lines.extend(
        sse(
            json.dumps(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                }
            )
        )
    )
    lines.extend(sse("[DONE]"))
    return lines


class FakeResponse:
    def __init__(self, lines: Sequence[bytes], media_type: str = SSE_MEDIA_TYPE) -> None:
        self.headers = {"Content-Type": media_type}
        self.lines = list(lines)
        self.read = 0

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *unused: object) -> None:
        return None

    def __iter__(self) -> Iterator[bytes]:
        for line in self.lines:
            self.read += 1
            yield line


def serve(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=0.0: response)


def test_the_transport_turns_server_sent_events_into_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, FakeResponse(delta_lines("Hola", " ", "mundo")))
    chunks = list(chat_stream("http://127.0.0.1:1", [{"role": "user", "content": "hi"}]))
    assert [chunk.text for chunk in chunks if not chunk.done] == ["Hola", " ", "mundo"]
    tail = chunks[-1]
    assert tail.done
    assert tail.finish_reason == "stop"
    assert (tail.prompt_tokens, tail.completion_tokens) == (11, 3)


def test_a_server_that_answers_plain_json_asks_for_the_blocking_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, FakeResponse([b"{}"], media_type="application/json"))
    chunks = list(chat_stream("http://127.0.0.1:1", [{"role": "user", "content": "hi"}]))
    assert len(chunks) == 1
    assert chunks[0].fallback
    assert chunks[0].done


def test_a_stream_stops_reading_the_moment_the_caller_says_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(delta_lines("uno", "dos", "tres", "cuatro", "cinco"))
    serve(monkeypatch, response)
    seen: list[int] = []

    def halt() -> bool:
        seen.append(1)
        return len(seen) > 4

    chunks = list(chat_stream("http://127.0.0.1:1", [{"role": "user", "content": "hi"}], stop=halt))
    assert chunks[-1].cancelled
    assert chunks[-1].done
    assert response.read < len(response.lines)


def test_an_error_frame_inside_the_stream_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = sse(json.dumps({"choices": [{"delta": {"content": "empez"}}]}))
    lines.extend(sse(json.dumps({"error": {"message": "context shift failed"}})))
    lines.extend(sse("[DONE]"))
    serve(monkeypatch, FakeResponse(lines))
    chunks = list(chat_stream("http://127.0.0.1:1", [{"role": "user", "content": "hi"}]))
    assert chunks[0].text == "empez"
    assert chunks[-1].error == "context shift failed"
    assert chunks[-1].done


class FakeStream:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = list(chunks)
        self.seen: list[Sequence[Mapping[str, object]]] = []

    def __call__(
        self,
        base_url: str,
        messages: Sequence[Mapping[str, object]],
        sampling: Mapping[str, object],
        stop: object,
    ) -> Generator[Chunk, None, None]:
        self.seen.append(messages)
        yield from self.chunks


def streaming_engine(
    stream: FakeStream, model: FakeModel | None = None, available: bool = True
) -> TalkEngine:
    return TalkEngine(
        base_url=lambda: "http://127.0.0.1:1",
        available=lambda: available,
        images=lambda attachment: b"",
        complete=model or FakeModel(Completion(text="blocking")),
        stream=stream,
    )


def test_the_engine_hands_out_the_pieces_and_finishes_with_the_whole_answer() -> None:
    stream = FakeStream(
        [Chunk(text="Bue"), Chunk(text="nas"), Chunk(finish_reason="stop", done=True)]
    )
    events = list(streaming_engine(stream).stream(long_history(2), TalkSettings(), CATALOG))
    assert [event.delta for event in events if event.reply is None] == ["Bue", "nas"]
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_OK
    assert final.text == "Buenas"


def test_a_server_that_will_not_stream_is_answered_by_the_blocking_call() -> None:
    stream = FakeStream([Chunk(fallback=True, done=True, error="llama-server did not stream")])
    model = FakeModel(Completion(text="entera"))
    events = list(streaming_engine(stream, model).stream(long_history(2), TalkSettings(), CATALOG))
    assert [event.delta for event in events if event.reply is None] == []
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_OK
    assert final.text == "entera"
    assert len(model.seen) == 1


def test_a_stopped_answer_keeps_every_word_the_model_had_already_written() -> None:
    stream = FakeStream([Chunk(text="a medi"), Chunk(cancelled=True, done=True)])
    events = list(streaming_engine(stream).stream(long_history(2), TalkSettings(), CATALOG))
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_CANCELLED
    assert final.text == "a medi"


def test_a_stop_before_the_first_word_says_so_in_the_catalog_language() -> None:
    stream = FakeStream([Chunk(cancelled=True, done=True)])
    events = list(streaming_engine(stream).stream(long_history(2), TalkSettings(), CATALOG))
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_CANCELLED
    assert final.text == CATALOG.text("talk.cancelled")


def test_a_broken_stream_is_reported_as_an_engine_that_is_not_there() -> None:
    stream = FakeStream(
        [Chunk(text="ho"), Chunk(error="llama-server stopped streaming", done=True)]
    )
    events = list(streaming_engine(stream).stream(long_history(2), TalkSettings(), CATALOG))
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_UNAVAILABLE
    assert final.error == "llama-server stopped streaming"


def test_the_engine_never_opens_a_stream_while_the_model_is_down() -> None:
    stream = FakeStream([Chunk(text="never", done=False)])
    events = list(
        streaming_engine(stream, available=False).stream(long_history(2), TalkSettings(), CATALOG)
    )
    assert stream.seen == []
    final = events[-1].reply
    assert final is not None
    assert final.state == STATE_UNAVAILABLE


def test_the_board_only_cancels_an_answer_that_is_being_written() -> None:
    board = CancelBoard()
    assert board.cancel(7) is False
    board.begin(7)
    assert board.cancelled(7) is False
    assert board.cancel(7) is True
    assert board.cancelled(7) is True
    board.finish(7)
    assert board.cancelled(7) is False
    assert board.cancel(7) is False


def talk_events(app: FastAPI, path: str, body: dict[str, object]) -> list[dict[str, Any]]:
    status, raw = http_request(app, "POST", path, json_body=body)
    assert status == 200
    events: list[dict[str, Any]] = []
    for frame in raw.decode("utf-8").split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                parsed = json.loads(line[len("data:") :].strip())
                assert isinstance(parsed, dict)
                events.append(parsed)
    return events


def test_the_stream_endpoint_announces_the_question_and_closes_with_the_answer(
    tmp_path: Path,
) -> None:
    app = talk_app(tmp_path)
    created = talk_json(app, "POST", "/api/talk", {"title": "streamed"})
    events = talk_events(
        app, f"/api/talk/{created['id']}/message/stream", {"message": "hola", "lang": "es"}
    )
    assert events[0]["type"] == "asked"
    assert events[0]["message"]["content"] == "hola"
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"]["state"] == STATE_UNAVAILABLE
    assert events[-1]["answered"]["content"] == CATALOG.text("talk.unavailable")
    opened = talk_json(app, "GET", f"/api/talk/{created['id']}")
    assert [item["content"] for item in opened["messages"]] == [
        "hola",
        CATALOG.text("talk.unavailable"),
    ]


def test_the_stream_endpoint_refuses_an_empty_message_with_a_code(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    created = talk_json(app, "POST", "/api/talk", {"title": "empty"})
    refused = talk_json(app, "POST", f"/api/talk/{created['id']}/message/stream", {"message": "  "})
    assert refused["_status"] == 400
    assert refused["code"] == "empty_message"


def test_the_stream_endpoint_refuses_an_unknown_conversation_with_a_code(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    refused = talk_json(app, "POST", "/api/talk/404/message/stream", {"message": "hola"})
    assert refused["_status"] == 404
    assert refused["code"] == "unknown_conversation"


def test_stopping_an_answer_nobody_is_writing_reports_that_there_was_none(tmp_path: Path) -> None:
    app = talk_app(tmp_path)
    created = talk_json(app, "POST", "/api/talk", {"title": "quiet"})
    stopped = talk_json(app, "POST", f"/api/talk/{created['id']}/stop")
    assert stopped["stopped"] is False
