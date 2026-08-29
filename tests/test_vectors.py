from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from arca.vectors import (
    IDS_FILENAME,
    VECTORS_FILENAME,
    VectorError,
    VectorHit,
    VectorIndex,
    hit_order,
    ids_path,
    normalize_rows,
    quantize,
    quantize_query,
    vectors_path,
    write_index,
)

DIMS = 32
TOLERANCE = 0.02


def _vectors(count: int, dims: int = DIMS, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal((count, dims)).astype(np.float32)


def _index(tmp_path: Path, matrix: np.ndarray, ids: list[int] | None = None) -> VectorIndex:
    write_index(tmp_path, matrix, ids or list(range(len(matrix))), matrix.shape[1])
    return VectorIndex(tmp_path, matrix.shape[1])


def test_normalisation_puts_every_row_on_the_unit_sphere() -> None:
    normalized = normalize_rows(_vectors(20))
    assert np.allclose(np.linalg.norm(normalized, axis=1), 1.0, atol=1e-5)


def test_normalisation_survives_a_zero_vector() -> None:
    normalized = normalize_rows(np.zeros((1, DIMS), dtype=np.float32))
    assert np.all(np.isfinite(normalized))


def test_quantisation_stays_inside_the_int8_range() -> None:
    quantized = quantize(normalize_rows(_vectors(50)))
    assert quantized.dtype == np.int8
    assert quantized.max() <= 127
    assert quantized.min() >= -127


def test_the_query_is_quantised_with_more_resolution_than_the_rows() -> None:
    vector = normalize_rows(_vectors(1))[0]
    quantized = quantize_query(vector)
    assert quantized.dtype == np.int16
    assert abs(int(quantized.max())) > 127


def test_writing_produces_the_two_files_of_the_specification(tmp_path: Path) -> None:
    matrix = _vectors(10)
    written = write_index(tmp_path, matrix, list(range(10)), DIMS)
    assert written == 10
    assert vectors_path(tmp_path).name == VECTORS_FILENAME
    assert ids_path(tmp_path).name == IDS_FILENAME
    assert vectors_path(tmp_path).stat().st_size == 10 * DIMS
    assert ids_path(tmp_path).stat().st_size == 10 * 8


def test_the_index_reports_what_it_holds(tmp_path: Path) -> None:
    index = _index(tmp_path, _vectors(12))
    assert index.rows == 12
    assert index.dims == DIMS
    index.close()


def test_searching_finds_the_vector_that_was_asked_for(tmp_path: Path) -> None:
    matrix = _vectors(200)
    index = _index(tmp_path, matrix, [1000 + position for position in range(200)])
    hits = index.search(matrix[7], limit=3)
    assert hits[0].chunk_id == 1007
    assert hits[0].score == pytest.approx(1.0, abs=TOLERANCE)
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
    index.close()


def test_the_score_tracks_the_real_cosine(tmp_path: Path) -> None:
    matrix = _vectors(300)
    index = _index(tmp_path, matrix)
    normalized = normalize_rows(matrix)
    query = matrix[11]
    exact = normalized @ normalize_rows(query.reshape(1, -1))[0]
    for hit in index.search(query, limit=10):
        assert hit.score == pytest.approx(float(exact[hit.chunk_id]), abs=TOLERANCE)
    index.close()


def test_quantisation_keeps_the_answers_among_the_best(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    centres = rng.standard_normal((8, DIMS)).astype(np.float32)
    labels = rng.integers(0, 8, 2000)
    matrix = (centres[labels] + 0.3 * rng.standard_normal((2000, DIMS))).astype(np.float32)
    index = _index(tmp_path, matrix)
    normalized = normalize_rows(matrix)
    inside = 0
    losses = []
    for seed in range(10):
        local = np.random.default_rng(100 + seed)
        query = (centres[local.integers(0, 8)] + 0.3 * local.standard_normal(DIMS)).astype(
            np.float32
        )
        exact = normalized @ normalize_rows(query.reshape(1, -1))[0]
        window = set(int(position) for position in np.argsort(-exact)[:50])
        returned = [hit.chunk_id for hit in index.search(query, limit=10)]
        inside += len(window & set(returned))
        best = float(np.sort(exact)[-10:].mean())
        losses.append(best - float(exact[returned].mean()))
    assert inside >= 80
    assert max(losses) < 0.02
    index.close()


def test_an_empty_index_answers_nothing(tmp_path: Path) -> None:
    write_index(tmp_path, [], [], DIMS)
    index = VectorIndex(tmp_path, DIMS)
    assert index.rows == 0
    assert index.search([0.0] * DIMS, limit=5) == []
    index.close()


def test_asking_for_more_than_there_is_returns_everything(tmp_path: Path) -> None:
    index = _index(tmp_path, _vectors(4))
    assert len(index.search(_vectors(1)[0], limit=99)) == 4
    assert index.search(_vectors(1)[0], limit=0) == []
    index.close()


def test_mismatched_lengths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(VectorError):
        write_index(tmp_path, _vectors(3), [1, 2], DIMS)


def test_vectors_of_the_wrong_size_are_refused(tmp_path: Path) -> None:
    with pytest.raises(VectorError):
        write_index(tmp_path, _vectors(3, dims=DIMS + 1), [1, 2, 3], DIMS)


def test_a_missing_or_broken_index_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VectorError):
        VectorIndex(tmp_path, DIMS)
    write_index(tmp_path, _vectors(4), [1, 2, 3, 4], DIMS)
    ids_path(tmp_path).write_bytes(b"short")
    with pytest.raises(VectorError):
        VectorIndex(tmp_path, DIMS)


def test_a_truncated_vector_file_is_refused(tmp_path: Path) -> None:
    write_index(tmp_path, _vectors(4), [1, 2, 3, 4], DIMS)
    vectors_path(tmp_path).write_bytes(b"x" * (DIMS * 4 - 1))
    with pytest.raises(VectorError):
        VectorIndex(tmp_path, DIMS)


def test_hits_order_by_score_and_break_ties_by_chunk() -> None:
    hits = [VectorHit(4, 0.1), VectorHit(1, 0.9), VectorHit(3, 0.7), VectorHit(2, 0.9)]
    assert [hit.chunk_id for hit in sorted(hits, key=hit_order)] == [1, 2, 3, 4]


def test_a_big_index_is_searched_in_parallel(tmp_path: Path) -> None:
    matrix = _vectors(60_000, dims=16, seed=9)
    write_index(tmp_path, matrix, list(range(60_000)), 16)
    index = VectorIndex(tmp_path, 16)
    hits = index.search(matrix[123], limit=1)
    assert hits[0].chunk_id == 123
    index.close()
