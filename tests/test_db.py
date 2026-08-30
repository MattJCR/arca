import sqlite3
from pathlib import Path

import pytest

from arca.db import (
    SCHEMA_VERSION,
    LibraryDatabaseMissingError,
    open_library_ro,
    open_library_rw,
    recent_events,
    record_event,
    schema_version,
    utc_now_iso,
)

EXPECTED_TABLES = {
    "zim_files",
    "downloads",
    "shards",
    "ingest_jobs",
    "deep_index_scope",
    "settings",
    "llama_profiles",
    "events",
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows if not row["name"].startswith("sqlite_")}


def test_open_rw_creates_the_full_schema(tmp_path: Path) -> None:
    connection = open_library_rw(tmp_path / "library.db")
    try:
        assert _table_names(connection) == EXPECTED_TABLES
        assert schema_version(connection) == SCHEMA_VERSION
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        connection.close()


def test_open_rw_is_idempotent_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    open_library_rw(path).close()
    connection = open_library_rw(path)
    try:
        assert _table_names(connection) == EXPECTED_TABLES
    finally:
        connection.close()


def test_open_ro_refuses_to_write(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    writer = open_library_rw(path)
    record_event(writer, "info", "test", "seeded")
    writer.close()
    reader = open_library_ro(path)
    try:
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
    finally:
        reader.close()


def test_open_ro_requires_an_existing_database(tmp_path: Path) -> None:
    with pytest.raises(LibraryDatabaseMissingError):
        open_library_ro(tmp_path / "missing.db")


def test_open_ro_handles_paths_with_uri_reserved_characters(tmp_path: Path) -> None:
    tricky = tmp_path / "weird%20lib" / "sub#dir"
    tricky.mkdir(parents=True)
    path = tricky / "library.db"
    open_library_rw(path).close()
    reader = open_library_ro(path)
    try:
        assert reader.execute("SELECT COUNT(*) FROM zim_files").fetchone()[0] == 0
    finally:
        reader.close()


def test_open_ro_leaves_the_database_bytes_untouched(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    open_library_rw(path).close()
    before_bytes = path.read_bytes()
    reader = open_library_ro(path)
    reader.execute("SELECT COUNT(*) FROM zim_files").fetchone()
    reader.close()
    assert path.read_bytes() == before_bytes
    allowed = {"library.db", "library.db-wal", "library.db-shm"}
    assert {entry.name for entry in tmp_path.iterdir()} <= allowed


def test_events_roundtrip_in_reverse_chronological_order(tmp_path: Path) -> None:
    connection = open_library_rw(tmp_path / "library.db")
    try:
        for index in range(25):
            record_event(connection, "info", "test", f"event {index}", detail=str(index))
        events = recent_events(connection, limit=20)
        assert len(events) == 20
        assert events[0]["message"] == "event 24"
        assert events[-1]["message"] == "event 5"
        assert events[0]["ts"]
    finally:
        connection.close()


def test_utc_timestamps_are_iso_and_zulu() -> None:
    stamp = utc_now_iso()
    assert stamp.endswith("Z")
    assert len(stamp) == 20


SCHEMA_VERSION_ONE = """
CREATE TABLE downloads (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    ref           TEXT NOT NULL,
    filename      TEXT NOT NULL,
    dest_path     TEXT NOT NULL,
    size_bytes    INTEGER,
    sha256        TEXT,
    mirrors       TEXT,
    mirror_index  INTEGER DEFAULT 0,
    bytes_done    INTEGER DEFAULT 0,
    status        TEXT NOT NULL,
    attempts      INTEGER DEFAULT 0,
    last_error    TEXT,
    started_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY, ts TEXT, level TEXT, area TEXT, message TEXT, detail TEXT
);
"""


def _library_at_version_one(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_VERSION_ONE)
        connection.execute(
            "INSERT INTO downloads (kind, ref, filename, dest_path, status) "
            "VALUES ('zim', 'uuid-1', 'old.zim', 'C:/old.zim', 'done')"
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()


def test_an_older_library_is_migrated_in_place_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    _library_at_version_one(path)
    connection = open_library_rw(path)
    try:
        assert schema_version(connection) == SCHEMA_VERSION
        row = connection.execute("SELECT * FROM downloads").fetchone()
        assert row["ref"] == "uuid-1"
        assert row["after_done"] == ""
        connection.execute("UPDATE downloads SET after_done = 'index' WHERE ref = 'uuid-1'")
        connection.commit()
    finally:
        connection.close()
    again = open_library_rw(path)
    try:
        assert schema_version(again) == SCHEMA_VERSION
        assert again.execute("SELECT after_done FROM downloads").fetchone()[0] == "index"
    finally:
        again.close()


def test_migrating_twice_does_not_try_to_add_the_column_again(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    _library_at_version_one(path)
    first = open_library_rw(path)
    first.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()
    second = open_library_rw(path)
    try:
        assert schema_version(second) == SCHEMA_VERSION
        assert second.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 1
    finally:
        second.close()
