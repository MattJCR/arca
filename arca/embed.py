from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

EMBED_MODEL = "multilingual-e5-base"
EMBED_DIMS = 768
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
KIND_QUERY = "query"
KIND_PASSAGE = "passage"
PREFIXES = {KIND_QUERY: QUERY_PREFIX, KIND_PASSAGE: PASSAGE_PREFIX}
DEFAULT_BATCH = 16
DEFAULT_TIMEOUT_SECONDS = 60.0
EMBEDDINGS_PATH = "/v1/embeddings"
HEALTH_PATH = "/health"


class EmbeddingError(RuntimeError):
    pass


class EmbeddingUnavailableError(EmbeddingError):
    pass


class EmbeddingMismatchError(EmbeddingError):
    pass


def prefixed(kind: str, text: str) -> str:
    try:
        prefix = PREFIXES[kind]
    except KeyError as error:
        raise EmbeddingError(f"unknown embedding kind: {kind}") from error
    return f"{prefix}{text}"


@dataclass(frozen=True)
class EmbeddingClient:
    base_url: str
    model: str = EMBED_MODEL
    dims: int = EMBED_DIMS
    batch: int = DEFAULT_BATCH
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def available(self) -> bool:
        try:
            self._get(HEALTH_PATH)
        except EmbeddingError:
            return False
        return True

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text], KIND_QUERY)[0]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts, KIND_PASSAGE)

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), max(1, self.batch)):
            window = texts[start : start + max(1, self.batch)]
            vectors.extend(self._request([prefixed(kind, text) for text in window]))
        return vectors

    def _request(self, inputs: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        request = urllib.request.Request(
            self._url(EMBEDDINGS_PATH),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw = self._open(request)
        vectors = _parse_embeddings(raw, len(inputs))
        for vector in vectors:
            if len(vector) != self.dims:
                raise EmbeddingMismatchError(
                    f"embedding server returned {len(vector)} dimensions, expected {self.dims}"
                )
        return vectors

    def _get(self, path: str) -> bytes:
        return self._open(urllib.request.Request(self._url(path), method="GET"))

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body: bytes = response.read()
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise EmbeddingUnavailableError(f"embedding server unreachable: {error}") from error
        return body

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"


def _parse_embeddings(raw: bytes, expected: int) -> list[list[float]]:
    try:
        parsed: object = json.loads(raw)
    except ValueError as error:
        raise EmbeddingError(f"embedding server returned invalid json: {error}") from error
    if not isinstance(parsed, dict):
        raise EmbeddingError("embedding server returned an unexpected payload")
    data = parsed.get("data")
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingError("embedding server returned a different number of vectors")
    vectors: list[list[float]] = []
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingError("embedding server returned an unexpected entry")
        vector = item.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("embedding server returned an entry without a vector")
        vectors.append([float(value) for value in vector])
    return vectors
