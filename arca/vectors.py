from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

VECTORS_FILENAME = "vectors.i8"
IDS_FILENAME = "vectors.ids"
VECTOR_SCALE = 127.0
QUERY_SCALE = 1024.0
QUERY_LIMIT = 32767
SEARCH_BLOCK_ROWS = 65536
SEARCH_MAX_THREADS = 8
SEARCH_PARALLEL_ROWS = 50_000
MIN_NORM = 1e-12

VectorSource = Sequence[Sequence[float]] | npt.NDArray[np.float32]
QuerySource = Sequence[float] | npt.NDArray[np.float32]


class VectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class VectorHit:
    chunk_id: int
    score: float


def normalize_rows(matrix: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, MIN_NORM)
    return (matrix / norms).astype(np.float32)


def quantize(matrix: npt.NDArray[np.float32], scale: float = VECTOR_SCALE) -> npt.NDArray[np.int8]:
    scaled = np.rint(matrix * scale)
    return np.clip(scaled, -127, 127).astype(np.int8)


def quantize_query(vector: npt.NDArray[np.float32]) -> npt.NDArray[np.int16]:
    scaled = np.rint(vector * QUERY_SCALE)
    return np.clip(scaled, -QUERY_LIMIT, QUERY_LIMIT).astype(np.int16)


def as_matrix(vectors: VectorSource, dims: int) -> npt.NDArray[np.float32]:
    if len(vectors) == 0:
        return np.zeros((0, dims), dtype=np.float32)
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != dims:
        raise VectorError(f"expected vectors of {dims} dimensions, got {matrix.shape}")
    return matrix


def vectors_path(directory: Path) -> Path:
    return directory / VECTORS_FILENAME


def ids_path(directory: Path) -> Path:
    return directory / IDS_FILENAME


def write_index(
    directory: Path,
    vectors: VectorSource,
    chunk_ids: Sequence[int],
    dims: int,
    scale: float = VECTOR_SCALE,
) -> int:
    if len(vectors) != len(chunk_ids):
        raise VectorError("vectors and chunk ids do not have the same length")
    directory.mkdir(parents=True, exist_ok=True)
    matrix = quantize(normalize_rows(as_matrix(vectors, dims)), scale)
    _write_mapped(vectors_path(directory), matrix, np.int8, (len(vectors), dims))
    _write_mapped(
        ids_path(directory),
        np.asarray(chunk_ids, dtype=np.int64),
        np.int64,
        (len(chunk_ids),),
    )
    return len(vectors)


def _write_mapped(path: Path, data: npt.NDArray[Any], dtype: Any, shape: tuple[int, ...]) -> None:
    if shape[0] == 0:
        path.write_bytes(b"")
        return
    mapping = np.memmap(path, dtype=dtype, mode="w+", shape=shape)
    mapping[:] = data
    mapping.flush()


class VectorIndex:
    def __init__(self, directory: Path, dims: int, scale: float = VECTOR_SCALE) -> None:
        self._directory = directory
        self._dims = dims
        self._scale = scale
        matrix_file = vectors_path(directory)
        ids_file = ids_path(directory)
        if not matrix_file.is_file() or not ids_file.is_file():
            raise VectorError(f"vector index is incomplete in {directory}")
        size = matrix_file.stat().st_size
        if dims <= 0 or size % dims != 0:
            raise VectorError(f"vector file size {size} is not a multiple of {dims}")
        rows = size // dims
        if ids_file.stat().st_size != rows * 8:
            raise VectorError("vector ids do not match the number of vectors")
        if rows == 0:
            self._matrix: npt.NDArray[np.int8] = np.empty((0, dims), dtype=np.int8)
            self._ids: npt.NDArray[np.int64] = np.empty((0,), dtype=np.int64)
            return
        self._matrix = np.memmap(matrix_file, dtype=np.int8, mode="r", shape=(rows, dims))
        self._ids = np.memmap(ids_file, dtype=np.int64, mode="r", shape=(rows,))

    @property
    def rows(self) -> int:
        return int(self._matrix.shape[0])

    @property
    def dims(self) -> int:
        return self._dims

    def search(self, query: QuerySource, limit: int = 10) -> list[VectorHit]:
        if self.rows == 0 or limit <= 0:
            return []
        normalized = normalize_rows(as_matrix(np.asarray([query], dtype=np.float32), self._dims))
        quantized = quantize_query(normalized[0])
        scores = np.empty(self.rows, dtype=np.int32)
        self._score(scores, quantized)
        taken = min(limit, self.rows)
        top = np.argpartition(-scores, taken - 1)[:taken]
        top = top[np.argsort(-scores[top], kind="stable")]
        divisor = self._scale * QUERY_SCALE
        return [
            VectorHit(
                chunk_id=int(self._ids[position]),
                score=float(scores[position]) / divisor,
            )
            for position in top
        ]

    def _score(self, scores: npt.NDArray[np.int32], query: npt.NDArray[np.int16]) -> None:
        workers = self._workers()
        if workers == 1:
            self._score_range(scores, query, 0, self.rows)
            return
        bounds = [
            (index * self.rows // workers, (index + 1) * self.rows // workers)
            for index in range(workers)
        ]
        threads = [
            threading.Thread(target=self._score_range, args=(scores, query, low, high))
            for low, high in bounds
            if high > low
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _score_range(
        self, scores: npt.NDArray[np.int32], query: npt.NDArray[np.int16], low: int, high: int
    ) -> None:
        for start in range(low, high, SEARCH_BLOCK_ROWS):
            stop = min(start + SEARCH_BLOCK_ROWS, high)
            scores[start:stop] = np.einsum(
                "ij,j->i", self._matrix[start:stop], query, dtype=np.int32
            )

    def _workers(self) -> int:
        if self.rows < SEARCH_PARALLEL_ROWS:
            return 1
        return max(1, min(SEARCH_MAX_THREADS, os.cpu_count() or 1))

    def close(self) -> None:
        self._matrix = np.empty((0, self._dims), dtype=np.int8)
        self._ids = np.empty((0,), dtype=np.int64)


def merge_hits(groups: Iterable[Sequence[VectorHit]], limit: int) -> list[VectorHit]:
    merged = [hit for group in groups for hit in group]
    merged.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return merged[:limit]
