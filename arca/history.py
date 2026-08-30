from __future__ import annotations

import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from arca.config import ArcaConfig, session_dir
from arca.db import utc_now_iso

TALK_SUBDIR = "talk"
TALK_DB_FILENAME = "talk.sqlite"
TALK_SCHEMA_VERSION = 1
MAX_TITLE_CHARS = 120
MAX_CONTENT_CHARS = 20000
MAX_IMAGE_BYTES = 8_000_000
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLES = (ROLE_USER, ROLE_ASSISTANT)

TALK_SCHEMA = """
CREATE TABLE conversations (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE attachments (
    id              INTEGER PRIMARY KEY,
    message_id      INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    mimetype        TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE talk_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX messages_by_conversation ON messages (conversation_id, id);
CREATE INDEX attachments_by_message ON attachments (message_id);
"""

IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True)
class Attachment:
    id: int
    message_id: int
    conversation_id: int
    mimetype: str
    size_bytes: int
    created_at: str = ""


@dataclass(frozen=True)
class Message:
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class Conversation:
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


def talk_dir(root: Path, config: ArcaConfig) -> Path:
    return session_dir(root, config) / TALK_SUBDIR


def image_mimetype(data: bytes) -> str:
    for signature, mimetype in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mimetype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


class TalkStore:
    def __init__(self, directory: Path) -> None:
        self._lock = threading.RLock()
        self._media: Path | None = None
        self._detail = ""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                directory / TALK_DB_FILENAME, check_same_thread=False, timeout=5.0
            )
            self._media = directory
        except (OSError, sqlite3.Error) as error:
            self._detail = str(error)
            connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        self._connection = connection
        self._migrate()

    @property
    def persistent(self) -> bool:
        return self._media is not None

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def path(self) -> Path | None:
        return None if self._media is None else self._media / TALK_DB_FILENAME

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def conversations(self) -> list[Conversation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
                "FROM conversations c ORDER BY c.updated_at DESC, c.id DESC"
            ).fetchall()
        return [_conversation(row) for row in rows]

    def conversation(self, conversation_id: int) -> Conversation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
                "FROM conversations c WHERE c.id = ?",
                (conversation_id,),
            ).fetchone()
        return None if row is None else _conversation(row)

    def create(self, title: str) -> Conversation:
        stamp = utc_now_iso()
        clean = _clean_title(title)
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (clean, stamp, stamp),
            )
            self._connection.commit()
            created = int(cursor.lastrowid or 0)
        return Conversation(created, clean, stamp, stamp, 0)

    def rename(self, conversation_id: int, title: str) -> Conversation | None:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (_clean_title(title), conversation_id),
            )
            self._connection.commit()
            changed = cursor.rowcount
        return self.conversation(conversation_id) if changed else None

    def delete(self, conversation_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            self._connection.commit()
            removed = bool(cursor.rowcount)
        self._drop_media(conversation_id)
        return removed

    def clear(self) -> int:
        known = [item.id for item in self.conversations()]
        with self._lock:
            self._connection.execute("DELETE FROM conversations")
            self._connection.commit()
        for conversation_id in known:
            self._drop_media(conversation_id)
        return len(known)

    def messages(self, conversation_id: int) -> list[Message]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, conversation_id, role, content, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            attached = self._connection.execute(
                "SELECT id, message_id, conversation_id, mimetype, size_bytes, created_at "
                "FROM attachments WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        by_message: dict[int, list[Attachment]] = {}
        for row in attached:
            by_message.setdefault(int(row["message_id"] or 0), []).append(_attachment(row))
        return [
            Message(
                id=int(row["id"]),
                conversation_id=int(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                created_at=str(row["created_at"]),
                attachments=tuple(by_message.get(int(row["id"]), ())),
            )
            for row in rows
        ]

    def add_message(self, conversation_id: int, role: str, content: str) -> Message | None:
        if role not in ROLES or self.conversation(conversation_id) is None:
            return None
        stamp = utc_now_iso()
        text = content[:MAX_CONTENT_CHARS]
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, role, text, stamp),
            )
            self._connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (stamp, conversation_id)
            )
            self._connection.commit()
            created = int(cursor.lastrowid or 0)
        return Message(created, conversation_id, role, text, stamp)

    def attach(self, conversation_id: int, data: bytes) -> Attachment | None:
        mimetype = image_mimetype(data)
        if not mimetype or self._media is None or self.conversation(conversation_id) is None:
            return None
        stamp = utc_now_iso()
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO attachments "
                "(message_id, conversation_id, mimetype, size_bytes, created_at) "
                "VALUES (NULL, ?, ?, ?, ?)",
                (conversation_id, mimetype, len(data), stamp),
            )
            self._connection.commit()
            created = int(cursor.lastrowid or 0)
        attachment = Attachment(created, 0, conversation_id, mimetype, len(data), stamp)
        target = self.attachment_path(attachment)
        if target is None:
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError:
            self.drop_attachment(created)
            return None
        return attachment

    def attachment(self, attachment_id: int) -> Attachment | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, message_id, conversation_id, mimetype, size_bytes, created_at "
                "FROM attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        return None if row is None else _attachment(row)

    def bind_attachments(self, message_id: int, attachment_ids: list[int]) -> list[Attachment]:
        bound: list[Attachment] = []
        for attachment_id in attachment_ids:
            with self._lock:
                self._connection.execute(
                    "UPDATE attachments SET message_id = ? WHERE id = ? AND message_id IS NULL",
                    (message_id, attachment_id),
                )
                self._connection.commit()
            found = self.attachment(attachment_id)
            if found is not None and found.message_id == message_id:
                bound.append(found)
        return bound

    def drop_attachment(self, attachment_id: int) -> bool:
        found = self.attachment(attachment_id)
        if found is None:
            return False
        with self._lock:
            self._connection.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            self._connection.commit()
        target = self.attachment_path(found)
        if target is not None:
            target.unlink(missing_ok=True)
        return True

    def attachment_path(self, attachment: Attachment) -> Path | None:
        if self._media is None:
            return None
        extension = IMAGE_EXTENSIONS.get(attachment.mimetype, "bin")
        return self._media / str(attachment.conversation_id) / f"{attachment.id}.{extension}"

    def attachment_bytes(self, attachment: Attachment) -> bytes:
        target = self.attachment_path(attachment)
        if target is None:
            return b""
        try:
            return target.read_bytes()
        except OSError:
            return b""

    def media_dir(self, conversation_id: int) -> Path | None:
        return None if self._media is None else self._media / str(conversation_id)

    def setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM talk_settings WHERE key = ?", (key,)
            ).fetchone()
        return default if row is None else str(row["value"])

    def write_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO talk_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._connection.commit()

    def _drop_media(self, conversation_id: int) -> None:
        directory = self.media_dir(conversation_id)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def _migrate(self) -> None:
        with self._lock:
            row = self._connection.execute("PRAGMA user_version").fetchone()
            if int(row[0]) >= TALK_SCHEMA_VERSION:
                return
            self._connection.executescript(TALK_SCHEMA)
            self._connection.execute(f"PRAGMA user_version = {TALK_SCHEMA_VERSION}")
            self._connection.commit()


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=int(row["id"]),
        title=str(row["title"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        message_count=int(row["message_count"] or 0),
    )


def _attachment(row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=int(row["id"]),
        message_id=int(row["message_id"] or 0),
        conversation_id=int(row["conversation_id"]),
        mimetype=str(row["mimetype"]),
        size_bytes=int(row["size_bytes"] or 0),
        created_at=str(row["created_at"]),
    )


def _clean_title(title: str) -> str:
    return " ".join(title.split())[:MAX_TITLE_CHARS]
