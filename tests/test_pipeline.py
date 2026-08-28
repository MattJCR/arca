from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca.config import init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EmbeddingClient, EmbeddingUnavailableError
from arca.pipeline import (
    JOB_DONE,
    JOB_FAILED,
    JOB_SKIPPED,
    ingest_documents,
    provisional_shards,
    seal_batch,
    unfinished_jobs,
)
from arca.shards import KIND_DOCS, STATE_SEALED, Shard, inspect_shard, shard_path
from tests.support import EmbeddingServer, blank_pdf, build_pdf

DIMS = 8
BODY = "The pump must be primed before the valve is opened and the gauge checked. "


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture
def library(tmp_path: Path) -> Iterator[tuple[Path, sqlite3.Connection]]:
    root = tmp_path / "library"
    init_library(root)
    connection = open_library_rw(library_db_path(root))
    yield root, connection
    connection.close()


def _client(server: EmbeddingServer) -> EmbeddingClient:
    return EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)


def _documents(tmp_path: Path, count: int) -> list[Path]:
    inbox = tmp_path / "inbox"
    return [
        build_pdf(
            inbox / f"doc{index}.pdf",
            [[f"Document {index}. " + BODY * 3], [f"Second page of {index}. " + BODY * 3]],
            title=f"Manual {index}",
        )
        for index in range(count)
    ]


def test_a_batch_indexes_documents_and_seals_itself(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    report = ingest_documents(root, connection, _documents(tmp_path, 3), _client(server))
    assert report.sealed is True
    assert report.done == 3
    assert report.failed == 0
    assert report.manifest is not None
    assert report.manifest.doc_count == 3
    assert report.manifest.chunk_count > 0
    state = inspect_shard(shard_path(root, report.shard_id), report.manifest.embed_model, DIMS)
    assert state.state == STATE_SEALED
    assert unfinished_jobs(connection, report.shard_id) == 0
    assert provisional_shards(connection, root) == []


def test_the_sealed_batch_can_be_searched(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    report = ingest_documents(root, connection, _documents(tmp_path, 2), _client(server))
    assert report.manifest is not None
    shard = Shard(inspect_shard(shard_path(root, report.shard_id), "multilingual-e5-base", DIMS))
    hits = shard.search_text("pump")
    assert hits
    assert all(hit.anchor.startswith("p:") for hit in hits)
    documents = shard.documents()
    assert len(documents) == 2
    assert all(document.orig_path.startswith("docs") for document in documents)
    shard.close()


def test_killing_the_import_halfway_only_repeats_the_document_in_flight(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    sources = _documents(tmp_path, 6)
    seen: list[str] = []

    class Failing(EmbeddingClient):
        def embed_passages(self, texts: object) -> list[list[float]]:
            if len(seen) >= 3:
                raise EmbeddingUnavailableError("killed")
            seen.append("one")
            return super().embed_passages(texts)  # type: ignore[arg-type]

    crashed = Failing(base_url=server.url, dims=DIMS, timeout=5.0)
    first = ingest_documents(root, connection, sources, crashed)
    assert first.sealed is False
    assert first.done == 3
    assert first.retry == 3
    assert first.failed == 0
    assert unfinished_jobs(connection, first.shard_id) == 3
    assert len(server.requests) == 3

    before = len(server.requests)
    second = ingest_documents(root, connection, sources, _client(server))
    assert second.shard_id == first.shard_id
    assert second.skipped == 3
    assert second.done == 3
    assert second.sealed is True
    assert len(server.requests) - before == 3


def test_a_relaunch_reuses_the_provisional_batch(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    sources = _documents(tmp_path, 2)
    first = ingest_documents(root, connection, sources[:1], _client(server), seal=False)
    assert first.sealed is False
    assert provisional_shards(connection, root) == [first.shard_id]
    second = ingest_documents(root, connection, sources, _client(server))
    assert second.shard_id == first.shard_id
    assert second.skipped == 1
    assert second.done == 1
    assert second.sealed is True


def test_a_provisional_batch_is_visible_while_it_has_pending_work(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    server.status = 500
    report = ingest_documents(root, connection, _documents(tmp_path, 2), _client(server))
    assert report.sealed is False
    assert report.retry == 2
    directory = shard_path(root, report.shard_id, KIND_DOCS)
    assert inspect_shard(directory, "multilingual-e5-base", DIMS).state == "provisional"
    assert provisional_shards(connection, root) == [report.shard_id]


def test_a_document_without_a_text_layer_is_recorded_but_does_not_stop_the_batch(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    scan = blank_pdf(tmp_path / "inbox" / "scan.pdf", pages=2)
    sources = [*_documents(tmp_path, 1), scan]
    report = ingest_documents(root, connection, sources, _client(server))
    assert report.sealed is True
    assert report.done == 2
    gaps = {result.orig_name: result.unindexable_pages for result in report.results}
    assert gaps["scan.pdf"] == 2
    assert report.manifest is not None
    shard = Shard(inspect_shard(shard_path(root, report.shard_id), "multilingual-e5-base", DIMS))
    scanned = [row for row in shard.documents() if row.orig_name == "scan.pdf"]
    assert scanned
    assert shard.unindexable_pages(scanned[0].doc_id) == [1, 2]
    shard.close()


def test_a_rejected_file_fails_alone(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    broken = tmp_path / "inbox" / "broken.pdf"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"%PDF-1.7 not really")
    report = ingest_documents(root, connection, [*_documents(tmp_path, 1), broken], _client(server))
    statuses = {result.orig_name: result.status for result in report.results}
    assert statuses["broken.pdf"] == JOB_FAILED
    assert statuses["doc0.pdf"] == JOB_DONE
    assert report.sealed is True


def test_reimporting_a_sealed_document_does_not_duplicate_its_chunks(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    sources = _documents(tmp_path, 1)
    first = ingest_documents(root, connection, sources, _client(server))
    assert first.manifest is not None
    chunks = first.manifest.chunk_count
    second = ingest_documents(root, connection, sources, _client(server))
    assert second.shard_id != first.shard_id
    assert second.manifest is not None
    assert second.manifest.chunk_count == chunks


def test_sealing_an_empty_batch_produces_nothing(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection]
) -> None:
    root, connection = library
    assert seal_batch(root, connection, "2026-09-02T1000-aaaaaa") is None


def test_the_sidecar_learns_which_batch_the_document_landed_in(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    from arca.ingest import read_sidecar

    root, connection = library
    report = ingest_documents(root, connection, _documents(tmp_path, 1), _client(server))
    doc_id = report.results[0].doc_id
    record = read_sidecar(root, doc_id)
    assert record is not None
    assert record.shard_id == report.shard_id


def test_skipped_documents_are_reported_as_such(
    tmp_path: Path, library: tuple[Path, sqlite3.Connection], server: EmbeddingServer
) -> None:
    root, connection = library
    sources = _documents(tmp_path, 1)
    ingest_documents(root, connection, sources, _client(server), seal=False)
    report = ingest_documents(root, connection, sources, _client(server), seal=False)
    assert [result.status for result in report.results] == [JOB_SKIPPED]
