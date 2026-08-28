from __future__ import annotations

from collections.abc import Iterator

import pytest

from arca.embed import (
    EMBED_DIMS,
    EMBED_MODEL,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    EmbeddingClient,
    EmbeddingError,
    EmbeddingMismatchError,
    EmbeddingUnavailableError,
    prefixed,
)
from tests.support import EmbeddingServer

DIMS = 8


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


def _client(server: EmbeddingServer, **kwargs: object) -> EmbeddingClient:
    return EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0, **kwargs)  # type: ignore[arg-type]


def test_the_e5_prefixes_are_the_ones_the_specification_fixes() -> None:
    assert QUERY_PREFIX == "query: "
    assert PASSAGE_PREFIX == "passage: "
    assert EMBED_MODEL == "multilingual-e5-base"
    assert EMBED_DIMS == 768


def test_prefixed_is_the_single_place_that_applies_them() -> None:
    assert prefixed("query", "how does it work") == "query: how does it work"
    assert prefixed("passage", "the pump primes") == "passage: the pump primes"
    with pytest.raises(EmbeddingError):
        prefixed("something-else", "text")


def test_queries_go_out_with_the_query_prefix(server: EmbeddingServer) -> None:
    vector = _client(server).embed_query("how does it work")
    assert len(vector) == DIMS
    assert server.texts() == ["query: how does it work"]


def test_passages_go_out_with_the_passage_prefix(server: EmbeddingServer) -> None:
    vectors = _client(server).embed_passages(["first block", "second block"])
    assert len(vectors) == 2
    assert all(len(vector) == DIMS for vector in vectors)
    assert server.texts() == ["passage: first block", "passage: second block"]


def test_passages_are_sent_in_batches(server: EmbeddingServer) -> None:
    client = _client(server, batch=2)
    vectors = client.embed_passages([f"block {index}" for index in range(5)])
    assert len(vectors) == 5
    assert len(server.requests) == 3
    assert len(server.texts()) == 5


def test_embedding_nothing_asks_the_server_nothing(server: EmbeddingServer) -> None:
    assert _client(server).embed_passages([]) == []
    assert server.requests == []


def test_the_model_name_travels_with_the_request(server: EmbeddingServer) -> None:
    _client(server).embed_query("text")
    assert server.requests[0]["model"] == EMBED_MODEL


def test_a_server_that_is_down_degrades_instead_of_crashing() -> None:
    client = EmbeddingClient(base_url="http://127.0.0.1:1", dims=DIMS, timeout=0.5)
    assert client.available() is False
    with pytest.raises(EmbeddingUnavailableError):
        client.embed_query("text")


def test_health_reports_what_the_server_says(server: EmbeddingServer) -> None:
    client = _client(server)
    assert client.available() is True
    server.healthy = False
    assert client.available() is False


def test_a_server_error_is_reported_as_unavailable(server: EmbeddingServer) -> None:
    server.status = 500
    with pytest.raises(EmbeddingUnavailableError):
        _client(server).embed_query("text")


def test_wrong_dimensions_are_refused_instead_of_stored(server: EmbeddingServer) -> None:
    client = EmbeddingClient(base_url=server.url, dims=EMBED_DIMS, timeout=5.0)
    with pytest.raises(EmbeddingMismatchError):
        client.embed_query("text")


def test_a_missing_vector_in_the_answer_is_refused(server: EmbeddingServer) -> None:
    server.drop_last = True
    with pytest.raises(EmbeddingError):
        _client(server).embed_passages(["a", "b"])


def test_an_unparseable_answer_is_refused(
    server: EmbeddingServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(server)
    monkeypatch.setattr(EmbeddingClient, "_open", lambda self, request: b"not json")
    with pytest.raises(EmbeddingError):
        client.embed_query("text")
