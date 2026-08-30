from __future__ import annotations

import json
import threading
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from arca.history import Attachment, Message, TalkStore
from arca.i18n import Catalog
from arca.ingest import estimate_tokens
from arca.llama import (
    FINISH_LENGTH,
    Chunk,
    Completion,
    chat_completion,
    chat_stream,
    multimodal_message,
)

SETTINGS_KEY = "talk"
SYSTEM_PROMPT_KEY = "talk.prompt.system"
DEFAULT_SAMPLING: dict[str, Any] = {"temperature": 0.7, "top_p": 0.95, "n_predict": 1024}
MAX_HISTORY_TOKENS = 6000
MAX_SYSTEM_PROMPT_CHARS = 4000
IMAGE_TOKEN_COST = 600
TEMPERATURE_CEILING = 2.0
TOP_P_CEILING = 1.0
PREDICT_CEILING = 8192

STATE_OK = "ok"
STATE_UNAVAILABLE = "unavailable"
STATE_TRUNCATED = "truncated"
STATE_EMPTY = "empty"
STATE_CANCELLED = "cancelled"

ImageLoader = Callable[[Attachment], bytes]
Completer = Callable[[str, Sequence[Mapping[str, object]], Mapping[str, object]], Completion]
Streamer = Callable[
    [str, Sequence[Mapping[str, object]], Mapping[str, object], Callable[[], bool]],
    Generator[Chunk, None, None],
]


@dataclass(frozen=True)
class TalkSettings:
    system_prompt: str = ""
    max_history_tokens: int = MAX_HISTORY_TOKENS
    sampling: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SAMPLING))


class CancelBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[int] = set()
        self._cancelled: set[int] = set()

    def begin(self, key: int) -> None:
        with self._lock:
            self._running.add(key)
            self._cancelled.discard(key)

    def cancel(self, key: int) -> bool:
        with self._lock:
            if key not in self._running:
                return False
            self._cancelled.add(key)
            return True

    def cancelled(self, key: int) -> bool:
        with self._lock:
            return key in self._cancelled

    def finish(self, key: int) -> None:
        with self._lock:
            self._running.discard(key)
            self._cancelled.discard(key)


@dataclass(frozen=True)
class TalkReply:
    text: str
    state: str
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    dropped: int = 0


@dataclass(frozen=True)
class TalkEvent:
    delta: str = ""
    reply: TalkReply | None = None


def read_settings(store: TalkStore) -> TalkSettings:
    try:
        parsed: object = json.loads(store.setting(SETTINGS_KEY, "{}"))
    except ValueError:
        return TalkSettings()
    if not isinstance(parsed, dict):
        return TalkSettings()
    return read_settings_payload(parsed, TalkSettings())


def write_settings(store: TalkStore, settings: TalkSettings) -> TalkSettings:
    store.write_setting(SETTINGS_KEY, json.dumps(settings_payload(settings), ensure_ascii=False))
    return read_settings(store)


def settings_payload(settings: TalkSettings) -> dict[str, Any]:
    return {
        "system_prompt": settings.system_prompt,
        "max_history_tokens": settings.max_history_tokens,
        "sampling": dict(settings.sampling),
    }


def read_settings_payload(payload: Mapping[str, object], current: TalkSettings) -> TalkSettings:
    return TalkSettings(
        system_prompt=_text(payload.get("system_prompt"), current.system_prompt),
        max_history_tokens=_positive(
            payload.get("max_history_tokens"), current.max_history_tokens, MAX_HISTORY_TOKENS
        ),
        sampling=_sampling(payload.get("sampling"), current.sampling),
    )


def system_prompt(settings: TalkSettings, catalog: Catalog) -> str:
    return settings.system_prompt.strip() or catalog.text(SYSTEM_PROMPT_KEY)


def message_cost(message: Message) -> int:
    return estimate_tokens(message.content) + IMAGE_TOKEN_COST * len(message.attachments)


def window(messages: Sequence[Message], settings: TalkSettings) -> list[Message]:
    budget = settings.max_history_tokens
    kept: list[Message] = []
    for message in reversed(messages):
        cost = message_cost(message)
        if kept and cost > budget:
            break
        budget -= cost
        kept.append(message)
    kept.reverse()
    return kept


class TalkEngine:
    def __init__(
        self,
        base_url: Callable[[], str],
        available: Callable[[], bool],
        images: ImageLoader,
        complete: Completer | None = None,
        stream: Streamer | None = None,
    ) -> None:
        self._base_url = base_url
        self._available = available
        self._images = images
        self._complete: Completer = complete or _blocking_completion
        self._stream: Streamer = stream or _streaming_completion

    def transcript(
        self, messages: Sequence[Message], settings: TalkSettings, catalog: Catalog
    ) -> list[Mapping[str, object]]:
        kept = window(messages, settings)
        payload: list[Mapping[str, object]] = [
            {"role": "system", "content": system_prompt(settings, catalog)}
        ]
        for message in kept:
            payload.append(
                multimodal_message(message.role, message.content, self._pictures(message))
            )
        return payload

    def reply(
        self, messages: Sequence[Message], settings: TalkSettings, catalog: Catalog
    ) -> TalkReply:
        dropped = len(messages) - len(window(messages, settings))
        if not self._available():
            return self._offline(catalog, dropped)
        transcript = self.transcript(messages, settings, catalog)
        return self._blocking(transcript, settings, catalog, dropped)

    def stream(
        self,
        messages: Sequence[Message],
        settings: TalkSettings,
        catalog: Catalog,
        stop: Callable[[], bool] | None = None,
    ) -> Iterator[TalkEvent]:
        dropped = len(messages) - len(window(messages, settings))
        if not self._available():
            yield TalkEvent(reply=self._offline(catalog, dropped))
            return
        transcript = self.transcript(messages, settings, catalog)
        halt: Callable[[], bool] = stop or _never
        pieces: list[str] = []
        tail = Chunk(done=True)
        with closing(self._stream(self._base_url(), transcript, settings.sampling, halt)) as flow:
            for chunk in flow:
                if chunk.fallback:
                    yield TalkEvent(reply=self._blocking(transcript, settings, catalog, dropped))
                    return
                if chunk.error:
                    yield TalkEvent(reply=self._offline(catalog, dropped, chunk.error))
                    return
                if chunk.done:
                    tail = chunk
                    break
                if chunk.text:
                    pieces.append(chunk.text)
                    yield TalkEvent(delta=chunk.text)
        yield TalkEvent(reply=self._streamed(pieces, tail, catalog, dropped))

    def _offline(self, catalog: Catalog, dropped: int, error: str = "") -> TalkReply:
        return TalkReply(
            catalog.text("talk.unavailable"), STATE_UNAVAILABLE, error=error, dropped=dropped
        )

    def _blocking(
        self,
        transcript: Sequence[Mapping[str, object]],
        settings: TalkSettings,
        catalog: Catalog,
        dropped: int,
    ) -> TalkReply:
        completion = self._complete(self._base_url(), transcript, settings.sampling)
        if completion.error:
            return self._offline(catalog, dropped, completion.error)
        text = completion.text.strip()
        if completion.out_of_room:
            return TalkReply(catalog.text("talk.truncated"), STATE_TRUNCATED, dropped=dropped)
        if not text:
            return TalkReply(catalog.text("talk.empty"), STATE_EMPTY, dropped=dropped)
        return TalkReply(
            text,
            STATE_OK,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            dropped=dropped,
        )

    def _streamed(
        self, pieces: Sequence[str], tail: Chunk, catalog: Catalog, dropped: int
    ) -> TalkReply:
        text = "".join(pieces).strip()
        if tail.cancelled:
            return TalkReply(
                text or catalog.text("talk.cancelled"), STATE_CANCELLED, dropped=dropped
            )
        if not text and tail.finish_reason == FINISH_LENGTH:
            return TalkReply(catalog.text("talk.truncated"), STATE_TRUNCATED, dropped=dropped)
        if not text:
            return TalkReply(catalog.text("talk.empty"), STATE_EMPTY, dropped=dropped)
        return TalkReply(
            text,
            STATE_OK,
            prompt_tokens=tail.prompt_tokens,
            completion_tokens=tail.completion_tokens,
            dropped=dropped,
        )

    def _pictures(self, message: Message) -> list[tuple[str, bytes]]:
        loaded: list[tuple[str, bytes]] = []
        for attachment in message.attachments:
            data = self._images(attachment)
            if data:
                loaded.append((attachment.mimetype, data))
        return loaded


def reply_payload(reply: TalkReply) -> dict[str, Any]:
    return {
        "text": reply.text,
        "state": reply.state,
        "error": reply.error,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "dropped": reply.dropped,
    }


def _blocking_completion(
    base_url: str, messages: Sequence[Mapping[str, object]], sampling: Mapping[str, object]
) -> Completion:
    return chat_completion(base_url, messages, sampling)


def _streaming_completion(
    base_url: str,
    messages: Sequence[Mapping[str, object]],
    sampling: Mapping[str, object],
    stop: Callable[[], bool],
) -> Generator[Chunk, None, None]:
    return chat_stream(base_url, messages, sampling, stop=stop)


def _never() -> bool:
    return False


def _text(value: object, default: str) -> str:
    if isinstance(value, str):
        return value[:MAX_SYSTEM_PROMPT_CHARS]
    return default


def _positive(value: object, default: int, ceiling: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, ceiling)
    return default


def _sampling(value: object, default: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(default)
    sampling = dict(DEFAULT_SAMPLING)
    sampling["temperature"] = _bounded(value.get("temperature"), TEMPERATURE_CEILING, 0.7)
    sampling["top_p"] = _bounded(value.get("top_p"), TOP_P_CEILING, 0.95)
    sampling["n_predict"] = _positive(value.get("n_predict"), 1024, PREDICT_CEILING)
    return sampling


def _bounded(value: object, ceiling: float, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= value <= ceiling:
        return float(value)
    return default
