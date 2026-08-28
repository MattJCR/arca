from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

LIBRARY_SCHEMA = """
CREATE TABLE zim_files (
    uuid          TEXT PRIMARY KEY,
    book_name     TEXT NOT NULL,
    filename      TEXT NOT NULL,
    title         TEXT,
    description   TEXT,
    language      TEXT,
    flavour       TEXT,
    tags          TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    article_count INTEGER,
    has_fulltext  INTEGER,
    added_at      TEXT,
    status        TEXT
);

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

CREATE TABLE shards (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    path          TEXT NOT NULL,
    doc_count     INTEGER,
    chunk_count   INTEGER,
    embed_model   TEXT,
    embed_dims    INTEGER,
    sealed_at     TEXT,
    status        TEXT,
    note          TEXT
);

CREATE TABLE ingest_jobs (
    id            INTEGER PRIMARY KEY,
    shard_id      TEXT,
    source_path   TEXT NOT NULL,
    sha256        TEXT,
    pages         INTEGER,
    status        TEXT,
    last_error    TEXT,
    updated_at    TEXT
);

CREATE TABLE deep_index_scope (
    source_kind   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    article_list  TEXT,
    PRIMARY KEY (source_kind, source_ref)
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE llama_profiles (
    name       TEXT PRIMARY KEY,
    args       TEXT NOT NULL,
    raw_cmd    TEXT,
    mode       TEXT NOT NULL,
    is_default INTEGER DEFAULT 0,
    last_good  INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY, ts TEXT, level TEXT, area TEXT, message TEXT, detail TEXT
);
"""


class LibraryDatabaseMissingError(FileNotFoundError):
    pass


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_library_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    _migrate(connection)
    return connection


def open_library_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise LibraryDatabaseMissingError(str(path))
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def record_event(
    connection: sqlite3.Connection, level: str, area: str, message: str, detail: str = ""
) -> None:
    connection.execute(
        "INSERT INTO events (ts, level, area, message, detail) VALUES (?, ?, ?, ?, ?)",
        (utc_now_iso(), level, area, message, detail),
    )
    connection.commit()


def recent_events(connection: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    cursor = connection.execute(
        "SELECT id, ts, level, area, message, detail FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return list(cursor.fetchall())


def _migrate(connection: sqlite3.Connection) -> None:
    if schema_version(connection) >= SCHEMA_VERSION:
        return
    connection.executescript(LIBRARY_SCHEMA)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
