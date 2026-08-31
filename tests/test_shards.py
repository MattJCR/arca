from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from arca.embed import EMBED_DIMS, EMBED_MODEL, PASSAGE_PREFIX, QUERY_PREFIX
from arca.formats import Block
from arca.ingest import Chunk, OriginalRecord, anchor
from arca.search import search_shards_by_vector
from arca.shards import (
    MANIFEST_FILENAME,
    STATE_CORRUPT,
    STATE_INCOMPATIBLE,
    STATE_PROVISIONAL,
    STATE_SEALED,
    TEXT_FILENAME,
    Shard,
    ShardError,
    ShardWriter,
    inspect_shard,
    list_shard_paths,
    new_shard_id,
    read_manifest,
    shard_path,
)

DIMS = 16


def _record(doc_id: str = "a" * 64, title: str = "Pump manual") -> OriginalRecord:
    return OriginalRecord(
        doc_id=doc_id,
        path=Path("docs/originals") / f"{doc_id}.pdf",
        orig_name="pump.pdf",
        mime="application/pdf",
        size_bytes=1234,
        pages=2,
        title=title,
        imported_at="2026-09-02T10:00:00Z",
        shard_id="",
        meta={"author": "Ana"},
    )


def _blocks() -> list[Block]:
    return [
        Block(1, 1, "The pump must be primed before the valve opens."),
        Block(1, 2, "Photosynthesis is unrelated to pumps."),
        Block(2, 1, "Check the pressure gauge every morning."),
    ]


def _chunks() -> list[Chunk]:
    blocks = _blocks()
    return [
        Chunk(anchor(block.page, block.ord), block.page, position, block.text, 12)
        for position, block in enumerate(blocks)
    ]


def _seal(
    directory: Path,
    embed_model: str = EMBED_MODEL,
    embed_dims: int = DIMS,
    record: OriginalRecord | None = None,
) -> list[int]:
    writer = ShardWriter(directory)
    document = record or _record()
    writer.add_document(document, f"docs/originals/{document.doc_id}.pdf")
    blocks = _blocks()
    writer.add_blocks(document.doc_id, blocks, [anchor(b.page, b.ord) for b in blocks])
    chunk_ids = writer.add_chunks(document.doc_id, _chunks())
    writer.mark_unindexable(document.doc_id, [2], "no text layer")
    rng = np.random.default_rng(4)
    vectors = rng.standard_normal((len(chunk_ids), embed_dims)).astype(np.float32)
    writer.seal(vectors, chunk_ids, embed_model=embed_model, embed_dims=embed_dims)
    return chunk_ids


def test_shard_ids_carry_the_date_and_a_random_suffix() -> None:
    stamp = datetime(2026, 8, 27, 10, 43, tzinfo=UTC)
    first = new_shard_id(stamp)
    second = new_shard_id(stamp)
    assert first.startswith("2026-08-27T1043-")
    assert first != second


def test_a_directory_without_a_manifest_is_provisional(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    writer = ShardWriter(directory)
    writer.add_document(_record(), "docs/originals/x.pdf")
    writer.close()
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    assert state.state == STATE_PROVISIONAL
    assert state.searchable_by_text is False
    assert state.searchable_by_vector is False


def test_sealing_writes_the_manifest_of_the_specification(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    manifest = read_manifest(directory)
    assert manifest.shard_id == "shard"
    assert manifest.embed_model == EMBED_MODEL
    assert manifest.embed_dims == DIMS
    assert manifest.embed_prefix_query == QUERY_PREFIX
    assert manifest.embed_prefix_passage == PASSAGE_PREFIX
    assert manifest.doc_count == 1
    assert manifest.chunk_count == 3
    assert set(manifest.files) == {"text.sqlite", "vectors.i8", "vectors.ids"}
    assert all(value.startswith("sha256:") for value in manifest.files.values())
    assert manifest.sealed_at.endswith("Z")


def test_a_sealed_shard_is_read_only_and_keeps_no_journal(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    assert not (directory / "text.sqlite-wal").exists()
    assert not (directory / "text.sqlite-shm").exists()
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    shard = Shard(state)
    with pytest.raises(sqlite3.OperationalError):
        shard._connection.execute("DELETE FROM chunks")
    shard.close()
    assert not (directory / "text.sqlite-wal").exists()


def test_a_sealed_shard_answers_keyword_and_vector_searches(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    chunk_ids = _seal(directory)
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    assert state.state == STATE_SEALED
    shard = Shard(state)
    hits = shard.search_text("pump")
    assert hits
    assert hits[0].doc_id == "a" * 64
    assert hits[0].anchor.startswith("p:")
    assert [hit.rank for hit in hits] == list(range(len(hits)))
    rng = np.random.default_rng(4)
    query = rng.standard_normal((len(chunk_ids), DIMS)).astype(np.float32)[0]
    vector_hits = shard.search_vectors(query, limit=2)
    assert vector_hits
    assert vector_hits[0].chunk_id in chunk_ids
    shard.close()


def test_keyword_search_ignores_accents_and_punctuation(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    shard = Shard(inspect_shard(directory, EMBED_MODEL, DIMS))
    assert shard.search_text("PUMP;")
    assert shard.search_text("   ") == []
    assert shard.search_text("nothingmatchesthis") == []
    shard.close()


def test_a_shard_exposes_its_documents_blocks_and_gaps(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    shard = Shard(inspect_shard(directory, EMBED_MODEL, DIMS))
    document = shard.document("a" * 64)
    assert document is not None
    assert document.title == "Pump manual"
    assert document.orig_path.endswith(".pdf")
    assert [row.doc_id for row in shard.documents()] == ["a" * 64]
    blocks = shard.blocks("a" * 64)
    assert [row[2] for row in blocks] == ["p:0001:01", "p:0001:02", "p:0002:01"]
    assert shard.unindexable_pages("a" * 64) == [2]
    assert shard.document("missing") is None
    shard.close()


def test_a_shard_sealed_with_another_model_is_marked_incompatible(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory, embed_model="some-other-model")
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    assert state.state == STATE_INCOMPATIBLE
    assert "some-other-model" in state.reason
    assert state.searchable_by_text is True
    assert state.searchable_by_vector is False


def test_an_incompatible_shard_still_serves_keyword_search(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory, embed_model="some-other-model")
    shard = Shard(inspect_shard(directory, EMBED_MODEL, DIMS))
    assert shard.search_text("pump")
    assert shard.search_vectors([0.0] * DIMS, limit=5) == []
    shard.close()


def test_different_dimensions_also_make_a_shard_incompatible(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory, embed_dims=DIMS)
    state = inspect_shard(directory, EMBED_MODEL, EMBED_DIMS)
    assert state.state == STATE_INCOMPATIBLE
    assert str(EMBED_DIMS) in state.reason


def test_an_altered_manifest_marks_the_shard_corrupt(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    manifest = json.loads((directory / MANIFEST_FILENAME).read_text("utf-8"))
    manifest["files"][TEXT_FILENAME] = "sha256:" + "0" * 64
    (directory / MANIFEST_FILENAME).write_text(json.dumps(manifest), "utf-8")
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    assert state.state == STATE_CORRUPT
    assert TEXT_FILENAME in state.reason
    assert state.searchable_by_text is False


def test_an_altered_vector_file_marks_the_shard_corrupt(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    target = directory / "vectors.i8"
    data = bytearray(target.read_bytes())
    data[0] = (data[0] + 1) % 256
    target.write_bytes(bytes(data))
    assert inspect_shard(directory, EMBED_MODEL, DIMS).state == STATE_CORRUPT


def test_a_missing_file_marks_the_shard_corrupt(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    (directory / "vectors.ids").unlink()
    state = inspect_shard(directory, EMBED_MODEL, DIMS)
    assert state.state == STATE_CORRUPT
    assert "missing" in state.reason


def test_an_unreadable_manifest_marks_the_shard_corrupt(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    _seal(directory)
    (directory / MANIFEST_FILENAME).write_text("{not json", "utf-8")
    assert inspect_shard(directory, EMBED_MODEL, DIMS).state == STATE_CORRUPT


def test_opening_a_shard_without_a_manifest_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    ShardWriter(directory).close()
    with pytest.raises(ShardError):
        Shard(inspect_shard(directory, EMBED_MODEL, DIMS))


def test_shards_are_listed_from_both_document_and_vendor_folders(tmp_path: Path) -> None:
    docs = shard_path(tmp_path, "2026-09-02T1000-aaaaaa")
    vendor = shard_path(tmp_path, "2026-09-02T1001-bbbbbb", kind="vendor")
    _seal(docs)
    _seal(vendor)
    found = [path.name for path in list_shard_paths(tmp_path)]
    assert found == ["2026-09-02T1000-aaaaaa", "2026-09-02T1001-bbbbbb"]


def test_a_semantic_result_below_the_floor_is_not_offered(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    chunk_ids = _seal(directory)
    shard = Shard(inspect_shard(directory, EMBED_MODEL, DIMS))
    rng = np.random.default_rng(4)
    query = rng.standard_normal((len(chunk_ids), DIMS)).astype(np.float32)[0].tolist()
    open_floor = search_shards_by_vector([shard], query, 10, floor=0)
    assert open_floor, "the index answered nothing at all"
    best = max(result.score for result in open_floor)
    above = int(best * 100) + 1
    assert search_shards_by_vector([shard], query, 10, floor=above) == []
    kept = search_shards_by_vector([shard], query, 10, floor=int(best * 100))
    assert kept
    assert all(result.score >= int(best * 100) / 100 for result in kept)
    shard.close()


def test_a_floor_of_zero_keeps_every_semantic_result(tmp_path: Path) -> None:
    directory = tmp_path / "shard"
    chunk_ids = _seal(directory)
    shard = Shard(inspect_shard(directory, EMBED_MODEL, DIMS))
    rng = np.random.default_rng(4)
    query = rng.standard_normal((len(chunk_ids), DIMS)).astype(np.float32)[0].tolist()
    hits = shard.search_vectors(query, 10)
    assert len(search_shards_by_vector([shard], query, 10, floor=0)) == len(hits)
    shard.close()


def test_the_floor_applies_across_every_batch(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    chunk_ids = _seal(first)
    _seal(second, record=_record(doc_id="b" * 64))
    shards = [
        Shard(inspect_shard(first, EMBED_MODEL, DIMS)),
        Shard(inspect_shard(second, EMBED_MODEL, DIMS)),
    ]
    rng = np.random.default_rng(4)
    query = rng.standard_normal((len(chunk_ids), DIMS)).astype(np.float32)[0].tolist()
    everything = search_shards_by_vector(shards, query, 20, floor=0)
    assert len({result.source_id for result in everything}) == 2
    best = max(result.score for result in everything)
    demanding = search_shards_by_vector(shards, query, 20, floor=int(best * 100))
    assert demanding
    assert len(demanding) < len(everything)
    assert all(result.score >= int(best * 100) / 100 for result in demanding)
    for shard in shards:
        shard.close()
