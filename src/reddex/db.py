from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    sqlite3 = None  # type: ignore[assignment]

DatabaseError = sqlite3.DatabaseError if sqlite3 is not None else RuntimeError

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


def _require_sqlcipher():
    if sqlite3 is None:
        raise RuntimeError(
            "SQLCipher support is required. Install sqlcipher3 before running "
            "reddex. On Termux: python -m pip install setuptools wheel && "
            "python -m pip install --no-build-isolation 'sqlcipher3==0.6.2'."
        )
    return sqlite3


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _apply_password(connection, database_password: str) -> None:
    if not database_password:
        raise RuntimeError("Database password must not be empty.")
    connection.execute(
        f"PRAGMA key = '{_sql_string(database_password)}'"
    )


def connect(path: str | Path, database_password: str):
    driver = _require_sqlcipher()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = driver.connect(str(path))
    connection.row_factory = driver.Row
    _apply_password(connection, database_password)

    try:
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except driver.DatabaseError as exc:
        connection.close()
        raise RuntimeError("Incorrect database password or invalid database.") from exc

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(path: str | Path, database_password: str):
    connection = connect(path, database_password)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def upsert_message(
    connection,
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
    connection,
    query: str,
    limit: int = 20,
) -> list[Any]:
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
    connection,
    event_id: str,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM messages WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    return row is not None


def backfill_complete(
    connection,
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
    connection,
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
