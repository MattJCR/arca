from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from arca.cli import main
from arca.config import (
    config_path,
    init_library,
    library_db_path,
    load_config,
    save_sources,
)
from arca.db import open_library_ro, open_library_rw
from arca.fetch import PART_SUFFIX, file_sha256, queue_book_download, read_sha256_sidecar, run_queue
from arca.formats import EPUB, PDF, detect_format, open_document
from arca.sources import (
    RemoteItem,
    Source,
    SourceError,
    acquire,
    default_sources,
    filename_from_url,
    probe,
    resolve,
    search,
)
from tests.support import EmbeddingServer

LIVE = os.environ.get("ARCA_LIVE", "") not in ("", "0")
DIMS = 8
RESUME_BYTES = 262144
USER_AGENT = "arca/live-test"

live = pytest.mark.skipif(
    not LIVE,
    reason="reaches the real sources over the network; set ARCA_LIVE=1 to run it",
)


def _source(source_id: str) -> Source:
    for candidate in default_sources():
        if candidate.source_id == source_id:
            return candidate
    raise AssertionError(source_id)


@pytest.fixture
def library(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "library"
    init_library(root)
    yield root


@pytest.fixture
def embeddings() -> Iterator[EmbeddingServer]:
    server = EmbeddingServer(dims=DIMS, mode="hashed")
    server.start()
    yield server
    server.stop()


def _point_at_embeddings(root: Path, server: EmbeddingServer) -> None:
    document = json.loads(config_path(root).read_text("utf-8"))
    document["embed_url"] = server.url
    document["embed_dims"] = DIMS
    config_path(root).write_text(json.dumps(document, indent=2), "utf-8")


def _enable(root: Path, source_id: str) -> Source:
    declared = tuple(
        replace(source, enabled=True) if source.source_id == source_id else source
        for source in default_sources()
    )
    save_sources(root, declared)
    reloaded = load_config(root)
    chosen = next(source for source in reloaded.sources if source.source_id == source_id)
    assert chosen.enabled is True
    return chosen


def _smallest(items: list[RemoteItem]) -> RemoteItem:
    sized = [item for item in items if item.size_bytes]
    return min(sized, key=lambda item: item.size_bytes or 0) if sized else items[0]


@live
def test_standard_ebooks_downloads_a_real_book_and_leaves_it_searchable(
    library: Path, embeddings: EmbeddingServer, capsys: pytest.CaptureFixture[str]
) -> None:
    root = library
    _point_at_embeddings(root, embeddings)
    source = _enable(root, "standard-ebooks")
    probe(source)
    query = "meditations"
    items = search(source, query=query)
    assert items, "the feed answered with no book"
    chosen = _smallest(items)
    capsys.readouterr()

    assert (
        main(
            [
                "--root",
                str(root),
                "add",
                "--source",
                source.source_id,
                chosen.item_id,
                "--query",
                query,
                "--index",
            ]
        )
        == 0
    )
    assert main(["--root", str(root), "run"]) == 0
    output = capsys.readouterr().out

    downloaded = root / "books" / chosen.filename
    assert downloaded.is_file(), output
    assert downloaded.stat().st_size == chosen.size_bytes
    assert read_sha256_sidecar(downloaded) == file_sha256(downloaded)
    assert detect_format(downloaded) is EPUB
    assert not downloaded.with_name(downloaded.name + PART_SUFFIX).exists()

    sealed = list((root / "docs" / "shards").glob("*/text.sqlite"))
    assert sealed, output
    connection = open_library_ro(library_db_path(root))
    try:
        row = connection.execute(
            "SELECT status, after_done FROM downloads WHERE kind = 'book'"
        ).fetchone()
        assert row["status"] == "done"
        assert row["after_done"] == "index"
    finally:
        connection.close()
    shard = sqlite3.connect(sealed[0])
    try:
        shard.row_factory = sqlite3.Row
        chunks = shard.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunks > 0
        anchor = shard.execute("SELECT anchor FROM chunks LIMIT 1").fetchone()[0]
        assert str(anchor).startswith("p:")
    finally:
        shard.close()


@live
def test_gutenberg_follows_its_second_feed_and_lands_an_ingestible_name(library: Path) -> None:
    root = library
    source = _source("gutenberg")
    probe(source)
    items = search(source, query="plato")
    assert items, "the search feed answered with no book"
    assert all(item.filename == "" for item in items[:5])
    assert all("/ebooks/" in item.url for item in items[:5])
    assert not any(item.title in ("Authors", "Subjects") for item in items)

    chosen = items[0]
    ready = acquire(source, chosen)
    assert ready.url != chosen.url
    assert not filename_from_url(ready.url).endswith(".epub")
    assert ready.filename.endswith(".epub")

    connection = open_library_rw(library_db_path(root))
    try:
        queue_book_download(
            connection,
            root,
            ref=ready.ref,
            filename=ready.filename,
            resolution=resolve(source, ready),
        )
    finally:
        connection.close()
    results = run_queue(library_db_path(root), concurrency=1)
    assert [result.status for result in results] == ["done"]

    downloaded = root / "books" / ready.filename
    assert downloaded.is_file()
    assert downloaded.read_bytes()[:2] == b"PK"
    assert detect_format(downloaded) is EPUB
    assert read_sha256_sidecar(downloaded) == file_sha256(downloaded)
    document = open_document(downloaded)
    try:
        assert document.page_count > 0
    finally:
        document.close()


@live
def test_openstax_resumes_a_real_pdf_from_a_half_written_part(library: Path) -> None:
    root = library
    source = _source("openstax")
    probe(source)
    items = search(source, query="additive manufacturing")
    assert items, "the api answered with no book"
    ready = acquire(source, items[0])
    resolution = resolve(source, ready)
    assert resolution.hash_missing is True

    request = urllib.request.Request(
        resolution.mirrors[0],
        headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{RESUME_BYTES - 1}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        assert response.status == 206, "the server does not honour Range"
        head = response.read()
    assert len(head) == RESUME_BYTES

    destination = root / "books" / ready.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + PART_SUFFIX)
    part.write_bytes(head)

    connection = open_library_rw(library_db_path(root))
    try:
        queue_book_download(
            connection, root, ref=ready.ref, filename=ready.filename, resolution=resolution
        )
    finally:
        connection.close()
    results = run_queue(library_db_path(root), concurrency=1)
    assert [result.status for result in results] == ["done"]

    assert destination.is_file()
    assert not part.exists()
    assert destination.stat().st_size > RESUME_BYTES
    assert destination.read_bytes()[:RESUME_BYTES] == head
    assert detect_format(destination) is PDF
    assert read_sha256_sidecar(destination) == file_sha256(destination)
    document = open_document(destination)
    try:
        assert document.page_count > 50
    finally:
        document.close()


@live
def test_arxiv_downloads_a_paper_whose_url_says_nothing_about_its_format(library: Path) -> None:
    root = library
    source = _source("arxiv")
    probe(source)
    items = search(source, query="water purification")
    assert items, "the api answered with no paper"
    chosen = items[0]
    assert chosen.size_bytes is None
    assert not filename_from_url(chosen.url).endswith(".pdf")
    assert chosen.filename.endswith(".pdf")
    assert filename_from_url(chosen.url) in chosen.filename

    connection = open_library_rw(library_db_path(root))
    try:
        download_id = queue_book_download(
            connection,
            root,
            ref=chosen.ref,
            filename=chosen.filename,
            resolution=resolve(source, chosen),
        )
    finally:
        connection.close()
    results = run_queue(library_db_path(root), concurrency=1)
    assert [result.status for result in results] == ["done"]

    downloaded = root / "books" / chosen.filename
    assert downloaded.is_file()
    assert detect_format(downloaded) is PDF
    assert read_sha256_sidecar(downloaded) == file_sha256(downloaded)
    connection = open_library_ro(library_db_path(root))
    try:
        row = connection.execute(
            "SELECT size_bytes, bytes_done FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        assert row["size_bytes"] == downloaded.stat().st_size
        assert row["bytes_done"] == downloaded.stat().st_size
    finally:
        connection.close()
    document = open_document(downloaded)
    try:
        assert document.page_count > 0
    finally:
        document.close()


@live
def test_every_shipped_source_answers_and_declares_whether_it_verifies() -> None:
    for source in default_sources():
        if not source.searchable:
            continue
        try:
            probe(source)
        except SourceError as error:
            raise AssertionError(f"{source.source_id} did not answer: {error}") from error
        assert source.verifies_hash is source.is_kiwix
