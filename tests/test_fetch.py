import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca import fetch
from arca.db import open_library_rw
from arca.fetch import (
    ProgressUpdate,
    claim_next,
    enqueue,
    file_sha256,
    read_sha256_sidecar,
    request_cancel,
    request_delete,
    request_pause,
    request_resume,
    request_retry,
    run_download,
    run_queue,
    verify_library,
    write_sha256_sidecar,
)
from tests.support import Behavior, DownloadServer

PAYLOAD = bytes(range(256)) * 512


@pytest.fixture
def server() -> Iterator[DownloadServer]:
    instance = DownloadServer()
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_library_rw(tmp_path / "library.db")
    yield conn
    conn.close()


def _enqueue(
    connection: sqlite3.Connection,
    tmp_path: Path,
    mirrors: tuple[str, ...],
    sha256: str | None = None,
    size: int | None = len(PAYLOAD),
) -> tuple[int, Path]:
    dest = tmp_path / "zim" / "sample.zim"
    expected = hashlib.sha256(PAYLOAD).hexdigest() if sha256 is None else sha256
    download_id = enqueue(
        connection,
        kind="zim",
        ref="uuid-sample",
        filename="sample.zim",
        dest_path=dest,
        size_bytes=size,
        sha256=expected,
        mirrors=mirrors,
    )
    return download_id, dest


def _claim_and_run(connection: sqlite3.Connection, download_id: int, **kwargs: object) -> str:
    assert claim_next(connection) == download_id
    return run_download(connection, download_id, sleep=lambda _: None, **kwargs)  # type: ignore[arg-type]


def test_fresh_download_completes_and_writes_the_sidecar(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_name(dest.name + ".part").exists()
    assert read_sha256_sidecar(dest) == hashlib.sha256(PAYLOAD).hexdigest()
    row = connection.execute("SELECT * FROM downloads").fetchone()
    assert row["status"] == "done"
    assert row["bytes_done"] == len(PAYLOAD)


def test_resume_downloads_only_the_missing_tail(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    behavior = server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    half = len(PAYLOAD) // 2
    part.write_bytes(PAYLOAD[:half])
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD
    assert behavior.requests == [f"bytes={half}-"]
    assert behavior.bytes_served == len(PAYLOAD) - half


def test_server_ignoring_range_truncates_and_restarts(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    behavior = server.add("/sample.zim", Behavior(payload=PAYLOAD, mode="no_range"))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"stale partial content")
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD
    assert behavior.bytes_served == len(PAYLOAD)


def test_mirror_failover_continues_on_the_same_part_file(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    cut_at = 4096
    broken = server.add(
        "/broken.zim", Behavior(payload=PAYLOAD, mode="fail_after", fail_after=cut_at)
    )
    healthy = server.add("/healthy.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(
        connection, tmp_path, (server.url("/broken.zim"), server.url("/healthy.zim"))
    )
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD
    assert len(broken.requests) == 1
    assert healthy.requests == [f"bytes={cut_at}-"]
    row = connection.execute("SELECT attempts, mirror_index FROM downloads").fetchone()
    assert row["attempts"] == 1
    assert row["mirror_index"] == 1


def test_hash_mismatch_fails_and_preserves_the_part_file(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(
        connection, tmp_path, (server.url("/sample.zim"),), sha256="0" * 64
    )
    assert _claim_and_run(connection, download_id) == "failed"
    assert not dest.exists()
    assert dest.with_name(dest.name + ".part").read_bytes() == PAYLOAD
    row = connection.execute("SELECT status, last_error FROM downloads").fetchone()
    assert row["status"] == "failed"
    assert row["last_error"] == "hash mismatch"
    event = connection.execute("SELECT * FROM events WHERE level = 'error'").fetchone()
    assert "hash mismatch" in event["message"]


def test_missing_upstream_hash_stores_the_computed_hash(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(
        connection, tmp_path, (server.url("/sample.zim"),), sha256="", size=None
    )
    assert _claim_and_run(connection, download_id) == "done"
    expected = hashlib.sha256(PAYLOAD).hexdigest()
    row = connection.execute("SELECT sha256 FROM downloads").fetchone()
    assert row["sha256"] == expected
    assert read_sha256_sidecar(dest) == expected
    warning = connection.execute("SELECT * FROM events WHERE level = 'warning'").fetchone()
    assert "no upstream hash" in warning["message"]


def test_pause_interrupts_and_resume_completes(
    server: DownloadServer,
    connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "PROGRESS_FLUSH_BYTES", 1024)
    monkeypatch.setattr(fetch, "STREAM_CHUNK_BYTES", 1024)
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    paused_once = False

    def pause_on_first_update(update: ProgressUpdate) -> None:
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            request_pause(connection, update.download_id)

    assert _claim_and_run(connection, download_id, progress=pause_on_first_update) == "paused"
    part = dest.with_name(dest.name + ".part")
    assert part.exists()
    assert 0 < part.stat().st_size < len(PAYLOAD)
    assert request_resume(connection, download_id) is True
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD


def test_cancel_interrupts_and_marks_failed(
    server: DownloadServer,
    connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "PROGRESS_FLUSH_BYTES", 1024)
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))

    def cancel_on_update(update: ProgressUpdate) -> None:
        request_cancel(connection, update.download_id)

    assert _claim_and_run(connection, download_id, progress=cancel_on_update) == "failed"
    row = connection.execute("SELECT status, last_error FROM downloads").fetchone()
    assert row["status"] == "failed"
    assert row["last_error"] == "cancelled"
    assert dest.with_name(dest.name + ".part").exists()


def test_exhausted_attempts_mark_the_download_failed(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD, mode="fail_after", fail_after=128))
    download_id, _ = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    assert _claim_and_run(connection, download_id, max_attempts=2) == "failed"
    row = connection.execute("SELECT status, attempts FROM downloads").fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == 2


def test_enqueue_deduplicates_active_downloads(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    first, _ = _enqueue(connection, tmp_path, ("https://example.org/a",))
    second, _ = _enqueue(connection, tmp_path, ("https://example.org/a",))
    assert first == second
    request_cancel(connection, first)
    third, _ = _enqueue(connection, tmp_path, ("https://example.org/a",))
    assert third != first


def test_retry_requeues_only_failed_downloads(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    download_id, _ = _enqueue(connection, tmp_path, ("https://example.org/a",))
    assert request_retry(connection, download_id) is False
    request_cancel(connection, download_id)
    assert request_retry(connection, download_id) is True
    row = connection.execute("SELECT status, attempts, last_error FROM downloads").fetchone()
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["last_error"] is None


def test_claim_next_returns_none_on_an_empty_queue(connection: sqlite3.Connection) -> None:
    assert claim_next(connection) is None


def test_delete_removes_the_row_and_the_partial_file(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    download_id, dest = _enqueue(connection, tmp_path, ("https://example.org/a",))
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"partial")
    assert request_delete(connection, download_id) is True
    assert connection.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 0
    assert not part.exists()
    event = connection.execute(
        "SELECT message FROM events WHERE message LIKE 'download removed%'"
    ).fetchone()
    assert event is not None
    assert request_delete(connection, download_id) is False


def test_delete_never_touches_the_completed_file(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    assert _claim_and_run(connection, download_id) == "done"
    assert request_delete(connection, download_id) is True
    assert dest.read_bytes() == PAYLOAD
    assert dest.with_name(dest.name + ".sha256").exists()


def test_run_queue_downloads_concurrently_and_updates_zim_files(
    server: DownloadServer, tmp_path: Path
) -> None:
    db_path = tmp_path / "library.db"
    connection = open_library_rw(db_path)
    try:
        for index in (1, 2):
            payload = PAYLOAD + bytes([index])
            server.add(f"/file{index}.zim", Behavior(payload=payload))
            connection.execute(
                """
                INSERT INTO zim_files (uuid, book_name, filename, status)
                VALUES (?, ?, ?, 'missing')
                """,
                (f"uuid-{index}", f"book_{index}", f"file{index}.zim"),
            )
            connection.commit()
            enqueue(
                connection,
                kind="zim",
                ref=f"uuid-{index}",
                filename=f"file{index}.zim",
                dest_path=tmp_path / "zim" / f"file{index}.zim",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                mirrors=(server.url(f"/file{index}.zim"),),
            )
        results = run_queue(db_path, concurrency=2, sleep=lambda _: None)
        assert sorted(result.status for result in results) == ["done", "done"]
        rows = connection.execute("SELECT status, sha256 FROM zim_files").fetchall()
        assert all(row["status"] == "present" and row["sha256"] for row in rows)
    finally:
        connection.close()


def test_run_queue_recovers_downloads_orphaned_by_a_crash(
    server: DownloadServer, tmp_path: Path
) -> None:
    db_path = tmp_path / "library.db"
    connection = open_library_rw(db_path)
    try:
        behavior = server.add("/sample.zim", Behavior(payload=PAYLOAD))
        download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
        half = len(PAYLOAD) // 2
        part = dest.with_name(dest.name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(PAYLOAD[:half])
        connection.execute(
            "UPDATE downloads SET status = 'running', bytes_done = 1000 WHERE id = ?",
            (download_id,),
        )
        connection.commit()
        results = run_queue(db_path, concurrency=1, sleep=lambda _: None)
        assert [result.status for result in results] == ["done"]
        assert dest.read_bytes() == PAYLOAD
        assert behavior.requests == [f"bytes={half}-"]
    finally:
        connection.close()


def test_file_scheme_mirrors_are_rejected(connection: sqlite3.Connection, tmp_path: Path) -> None:
    local_file = tmp_path / "secret.txt"
    local_file.write_bytes(b"local secret content")
    download_id, dest = _enqueue(connection, tmp_path, (local_file.as_uri(),))
    assert _claim_and_run(connection, download_id, max_attempts=1) == "failed"
    row = connection.execute("SELECT last_error FROM downloads").fetchone()
    assert "unsupported mirror scheme" in row["last_error"]
    assert not dest.exists()


def test_queue_zim_download_rejects_unsafe_filenames(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    from arca.catalog import CatalogEntry, Resolution
    from arca.fetch import queue_zim_download

    entry = CatalogEntry(
        uuid="u1",
        book_name="evil",
        filename="../escape.zim",
        title="Evil",
        description="",
        language="spa",
        flavour="",
        tags="",
        article_count=None,
        size_bytes=None,
        has_fulltext=False,
        meta4_url="https://x.org/evil.zim.meta4",
    )
    resolution = Resolution(
        size_bytes=None, sha256="", mirrors=("https://x.org/evil.zim",), hash_missing=True
    )
    with pytest.raises(ValueError):
        queue_zim_download(connection, tmp_path, entry, resolution)


def test_verify_library_detects_corruption_and_absence(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    zim_dir = tmp_path / "zim"
    zim_dir.mkdir()
    good = zim_dir / "good.zim"
    good.write_bytes(PAYLOAD)
    write_sha256_sidecar(good, file_sha256(good))
    bad = zim_dir / "bad.zim"
    bad.write_bytes(PAYLOAD)
    write_sha256_sidecar(bad, file_sha256(bad))
    corrupted = bytearray(PAYLOAD)
    corrupted[0] ^= 0xFF
    bad.write_bytes(bytes(corrupted))
    for uuid, filename in (("u-good", "good.zim"), ("u-bad", "bad.zim"), ("u-gone", "gone.zim")):
        connection.execute(
            "INSERT INTO zim_files (uuid, book_name, filename, status) VALUES (?, ?, ?, 'present')",
            (uuid, filename.removesuffix(".zim"), filename),
        )
    connection.commit()
    outcomes = {item.filename: item.result for item in verify_library(connection, tmp_path)}
    assert outcomes == {"good.zim": "ok", "bad.zim": "corrupt", "gone.zim": "missing"}
    statuses = {
        row["uuid"]: row["status"]
        for row in connection.execute("SELECT uuid, status FROM zim_files").fetchall()
    }
    assert statuses == {"u-good": "present", "u-bad": "corrupt", "u-gone": "missing"}


def test_download_releases_an_open_zim_before_replacing_it(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    build_zim(
        tmp_path / "zim" / "sample.zim",
        [article("page.html", "Page", "<p>old</p>")],
        title="Old",
        main_path="page.html",
    )
    library = ZimLibrary(tmp_path)
    assert [info.title for info in library.refresh()] == ["Old"]
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    released: list[str] = []

    def before_replace(path: Path) -> None:
        released.append(path.name)
        library.release(path.name)

    status = _claim_and_run(connection, download_id, before_replace=before_replace)
    assert status == "done"
    assert released == ["sample.zim"]
    assert dest.read_bytes() == PAYLOAD
    library.close()


def test_downloads_still_work_without_a_release_hook(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    server.add("/sample.zim", Behavior(payload=PAYLOAD))
    download_id, dest = _enqueue(connection, tmp_path, (server.url("/sample.zim"),))
    assert _claim_and_run(connection, download_id) == "done"
    assert dest.read_bytes() == PAYLOAD


def test_a_book_lands_in_its_own_directory_and_carries_what_to_do_next(
    server: DownloadServer, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    from arca.catalog import Resolution
    from arca.fetch import AFTER_INDEX, queue_book_download

    server.add("/handbook.epub", Behavior(payload=PAYLOAD))
    resolution = Resolution(
        size_bytes=len(PAYLOAD),
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        mirrors=(server.url("/handbook.epub"),),
        hash_missing=False,
    )
    download_id = queue_book_download(
        connection,
        tmp_path,
        ref="books:1",
        filename="handbook.epub",
        resolution=resolution,
        after_done=AFTER_INDEX,
    )
    row = connection.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
    assert row["kind"] == "book"
    assert row["after_done"] == AFTER_INDEX
    assert Path(row["dest_path"]) == tmp_path / "books" / "handbook.epub"
    assert _claim_and_run(connection, download_id) == "done"
    assert (tmp_path / "books" / "handbook.epub").read_bytes() == PAYLOAD


def test_a_book_with_an_unusable_name_is_never_queued(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    from arca.catalog import Resolution
    from arca.fetch import queue_book_download

    resolution = Resolution(size_bytes=None, sha256="", mirrors=("https://x/a",), hash_missing=True)
    for name in ("../escape.epub", "dir/inner.epub", ""):
        with pytest.raises(ValueError):
            queue_book_download(connection, tmp_path, ref="r", filename=name, resolution=resolution)
    assert connection.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 0


def test_a_step_that_explodes_after_a_download_never_stops_the_queue(
    server: DownloadServer, tmp_path: Path
) -> None:
    db_path = tmp_path / "library.db"
    connection = open_library_rw(db_path)
    seen: list[str] = []
    try:
        for index in (1, 2):
            payload = PAYLOAD + bytes([index])
            server.add(f"/file{index}.zim", Behavior(payload=payload))
            enqueue(
                connection,
                kind="zim",
                ref=f"uuid-{index}",
                filename=f"file{index}.zim",
                dest_path=tmp_path / "zim" / f"file{index}.zim",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                mirrors=(server.url(f"/file{index}.zim"),),
            )

        def exploding(result: fetch.QueueResult) -> None:
            seen.append(result.filename)
            raise RuntimeError("the indexing step blew up")

        results = run_queue(db_path, concurrency=1, sleep=lambda _: None, on_done=exploding)
        assert sorted(result.status for result in results) == ["done", "done"]
        assert sorted(seen) == ["file1.zim", "file2.zim"]
        events = connection.execute(
            "SELECT detail FROM events WHERE message LIKE 'post-download step failed%'"
        ).fetchall()
        assert len(events) == 2
        assert "the indexing step blew up" in str(events[0]["detail"])
    finally:
        connection.close()
