from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    room_id       TEXT NOT NULL,
    room_name     TEXT,
    sender        TEXT,
    created_at_ms INTEGER NOT NULL,
    body          TEXT NOT NULL,
    web_url       TEXT NOT NULL,
    raw_json      TEXT NOT NULL,
    archived_at   INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS messages_room_created_idx
    ON messages(room_id, created_at_ms);

CREATE INDEX IF NOT EXISTS messages_sender_idx
    ON messages(sender);

CREATE TABLE IF NOT EXISTS room_sync_state (
    room_id            TEXT PRIMARY KEY,
    backfill_complete  INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    body,
    sender,
    room_name,
    content='messages',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_ai
AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, body, sender, room_name)
    VALUES (new.id, new.body, new.sender, new.room_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad
AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, sender, room_name)
    VALUES ('delete', old.id, old.body, old.sender, old.room_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_au
AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, sender, room_name)
    VALUES ('delete', old.id, old.body, old.sender, old.room_name);
    INSERT INTO messages_fts(rowid, body, sender, room_name)
    VALUES (new.id, new.body, new.sender, new.room_name);
END;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(path: str | Path) -> sqlite3.Connection:
    connection = connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def upsert_message(
    connection: sqlite3.Connection,
    message: Mapping[str, Any],
) -> None:
    raw_json = message.get("raw_json")
    if not isinstance(raw_json, str):
        raw_json = json.dumps(
            raw_json if raw_json is not None else dict(message),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    connection.execute(
        """
        INSERT INTO messages (
            event_id,
            room_id,
            room_name,
            sender,
            created_at_ms,
            body,
            web_url,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            room_id = excluded.room_id,
            room_name = excluded.room_name,
            sender = excluded.sender,
            created_at_ms = excluded.created_at_ms,
            body = excluded.body,
            web_url = excluded.web_url,
            raw_json = excluded.raw_json
        """,
        (
            message["event_id"],
            message["room_id"],
            message.get("room_name"),
            message.get("sender"),
            int(message["created_at_ms"]),
            message["body"],
            message["web_url"],
            raw_json,
        ),
    )


def search_messages(
    connection: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                m.*,
                snippet(messages_fts, 0, '[', ']', ' … ', 16) AS snippet,
                bm25(messages_fts) AS rank
            FROM messages_fts
            JOIN messages AS m ON m.id = messages_fts.rowid
            WHERE messages_fts MATCH ?
            ORDER BY rank, m.created_at_ms DESC
            LIMIT ?
            """,
            (query, limit),
        )
    )


def message_exists(
    connection: sqlite3.Connection,
    event_id: str,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM messages WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    return row is not None


def backfill_complete(
    connection: sqlite3.Connection,
    room_id: str,
) -> bool:
    row = connection.execute(
        """
        SELECT backfill_complete
        FROM room_sync_state
        WHERE room_id = ?
        """,
        (room_id,),
    ).fetchone()
    return bool(row["backfill_complete"]) if row is not None else False


def set_backfill_complete(
    connection: sqlite3.Connection,
    room_id: str,
    complete: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO room_sync_state (room_id, backfill_complete)
        VALUES (?, ?)
        ON CONFLICT(room_id) DO UPDATE SET
            backfill_complete = excluded.backfill_complete
        """,
        (room_id, int(complete)),
    )
