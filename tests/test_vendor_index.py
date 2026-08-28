from __future__ import annotations

import io
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca.config import init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EmbeddingClient
from arca.pipeline import ingest_vendor, vendor_texts
from arca.shards import KIND_VENDOR, STATE_SEALED, Shard, inspect_shard, shard_path
from arca.vendor import LockEntry, vendor_sources
from tests.support import EmbeddingServer

DIMS = 8
README = (
    "ARCA test package\n"
    "=================\n"
    "\n"
    "This package explains how the resumable download engine verifies a file "
    "by rereading it completely before replacing the destination.\n"
    "\n"
    "The mirror failover keeps the same partial file and asks the next mirror "
    "for the remaining bytes with a Range request.\n"
)
GUIDE = (
    "Configuration guide\n"
    "\n"
    "Every setting lives in a plain text file that can be edited by hand, and "
    "the library refuses to start without a readable path.\n"
)


def _sdist(path: Path, name: str = "sample-1.0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as bundle:
        for member_name, text in (
            (f"{name}/README.md", README),
            (f"{name}/docs/guide.rst", GUIDE),
            (f"{name}/setup.py", "print('not documentation')"),
            (f"{name}/src/module.py", "value = 1"),
        ):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def server() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=DIMS)
    instance.start()
    yield instance
    instance.stop()


def test_only_documentation_is_read_from_a_source_archive(tmp_path: Path) -> None:
    archive = _sdist(tmp_path / "sample.tar.gz")
    texts = dict(vendor_texts(archive))
    assert set(texts) == {"sample-1.0/README.md", "sample-1.0/docs/guide.rst"}
    assert "resumable download engine" in texts["sample-1.0/README.md"]


def test_a_missing_or_broken_archive_yields_nothing(tmp_path: Path) -> None:
    assert vendor_texts(tmp_path / "absent.tar.gz") == []
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"not a tarball")
    assert vendor_texts(broken) == []


def test_vendor_sources_only_lists_what_is_on_disk(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    present = LockEntry(
        key="sample",
        version="1.0",
        kind="pypi",
        url="https://example.invalid/sample-1.0.tar.gz",
        commit="",
        sha256="",
        license="MIT",
        captured_at="2026-09-02",
        notes="",
    )
    absent = LockEntry(
        key="ghost",
        version="2.0",
        kind="pypi",
        url="https://example.invalid/ghost-2.0.tar.gz",
        commit="",
        sha256="",
        license="MIT",
        captured_at="2026-09-02",
        notes="",
    )
    _sdist(root / "vendor" / "wheelhouse" / "sample-1.0.tar.gz")
    sources = vendor_sources(root, [present, absent])
    assert [source.title for source in sources] == ["sample 1.0"]
    assert sources[0].relative_path.startswith("vendor")
    assert sources[0].doc_id


def test_the_vendor_batch_is_sealed_and_searchable(tmp_path: Path, server: EmbeddingServer) -> None:
    root = tmp_path / "library"
    init_library(root)
    entry = LockEntry(
        key="sample",
        version="1.0",
        kind="pypi",
        url="https://example.invalid/sample-1.0.tar.gz",
        commit="",
        sha256="",
        license="MIT",
        captured_at="2026-09-02",
        notes="",
    )
    _sdist(root / "vendor" / "wheelhouse" / "sample-1.0.tar.gz")
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    try:
        report = ingest_vendor(root, connection, vendor_sources(root, [entry]), client)
    finally:
        connection.close()
    assert report.sealed is True
    assert report.manifest is not None
    assert report.manifest.kind == KIND_VENDOR
    assert report.manifest.doc_count == 1
    assert report.manifest.chunk_count > 0
    directory = shard_path(root, report.shard_id, KIND_VENDOR)
    state = inspect_shard(directory, client.model, DIMS)
    assert state.state == STATE_SEALED
    shard = Shard(state)
    hits = shard.search_text("mirror failover")
    assert hits
    assert hits[0].anchor.startswith("p:")
    document = shard.document(hits[0].doc_id)
    assert document is not None
    assert document.title == "sample 1.0"
    assert document.mime == "application/gzip"
    shard.close()


def test_a_vendor_result_offers_no_pdf_original(tmp_path: Path, server: EmbeddingServer) -> None:
    from arca.search import shard_results

    root = tmp_path / "library"
    init_library(root)
    entry = LockEntry(
        key="sample",
        version="1.0",
        kind="pypi",
        url="https://example.invalid/sample-1.0.tar.gz",
        commit="",
        sha256="",
        license="MIT",
        captured_at="2026-09-02",
        notes="",
    )
    _sdist(root / "vendor" / "wheelhouse" / "sample-1.0.tar.gz")
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    try:
        report = ingest_vendor(root, connection, vendor_sources(root, [entry]), client)
    finally:
        connection.close()
    assert report.manifest is not None
    shard = Shard(inspect_shard(shard_path(root, report.shard_id, KIND_VENDOR), client.model, DIMS))
    results = shard_results(shard, shard.search_text("mirror failover"))
    assert results
    assert results[0].original_url == ""
    assert results[0].url.startswith("/api/doc/")
    shard.close()


def test_a_package_without_documentation_is_reported_not_sealed(
    tmp_path: Path, server: EmbeddingServer
) -> None:
    root = tmp_path / "library"
    init_library(root)
    empty = root / "vendor" / "wheelhouse" / "empty-1.0.tar.gz"
    empty.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(empty, "w:gz") as bundle:
        data = b"value = 1"
        info = tarfile.TarInfo("empty-1.0/module.py")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    entry = LockEntry(
        key="empty",
        version="1.0",
        kind="pypi",
        url="https://example.invalid/empty-1.0.tar.gz",
        commit="",
        sha256="",
        license="MIT",
        captured_at="2026-09-02",
        notes="",
    )
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=server.url, dims=DIMS, timeout=5.0)
    try:
        report = ingest_vendor(root, connection, vendor_sources(root, [entry]), client)
    finally:
        connection.close()
    assert report.sealed is False
    assert report.failed == 1
    assert "no documentation" in report.results[0].error
