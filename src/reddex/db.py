from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    sqlite3 = None  # type: ignore[assignment]

DatabaseError = sqlite3.DatabaseError if sqlite3 is not None else RuntimeError

DEFAULT_KEY_PATH = Path.home() / ".reddex" / "db_key"
SQLITE_HEADER = b"SQLite format 3\x00"

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
            "reddex. On Termux: python -m pip install --no-build-isolation "
            "'sqlcipher3==0.6.2'."
        )
    return sqlite3


def key_path() -> Path:
    override = os.environ.get("REDDEX_DB_KEY_FILE")
    return Path(override).expanduser() if override else DEFAULT_KEY_PATH


def load_db_key() -> str:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, 0o700)

    if path.exists():
        value = path.read_text(encoding="ascii").strip().lower()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise RuntimeError(
                f"Invalid reddex database key file: {path}. "
                "Expected exactly 64 hexadecimal characters."
            )
        _chmod(path, 0o600)
        return value

    value = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_db_key()

    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")
    _chmod(path, 0o600)
    return value


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        # Windows permissions are primarily ACL-based; chmod is best effort.
        pass


def _is_plaintext_sqlite(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < len(SQLITE_HEADER):
        return False
    with path.open("rb") as handle:
        return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def _quoted(value: str) -> str:
    return value.replace("'", "''")


def _migrate_plaintext_database(path: Path, key: str) -> None:
    driver = _require_sqlcipher()
    temporary = path.with_name(path.name + ".encrypted.tmp")
    temporary.unlink(missing_ok=True)

    connection = driver.connect(str(path))
    try:
        # Fold any outstanding plaintext WAL content back into the main file
        # before exporting it.
        try:
            connection.execute("PRAGMA wal_checkpoint(FULL)")
        except driver.Error:
            pass

        temp_sql = _quoted(str(temporary))
        key_sql = _quoted(key)
        connection.execute(
            f"ATTACH DATABASE '{temp_sql}' AS encrypted KEY '{key_sql}'"
        )
        try:
            connection.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        finally:
            connection.execute("DETACH DATABASE encrypted")
    finally:
        connection.close()

    if not temporary.exists() or temporary.stat().st_size == 0:
        raise RuntimeError("Failed to migrate the plaintext database to SQLCipher.")

    # Verify the new file can be opened before replacing the original.
    verification = driver.connect(str(temporary))
    try:
        verification.execute(f"PRAGMA key = '{_quoted(key)}'")
        verification.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        verification.close()

    os.replace(temporary, path)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    _restrict_database_files(path)


def _restrict_database_files(path: Path) -> None:
    _chmod(path, 0o600)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            _chmod(sidecar, 0o600)


def connect(path: str | Path):
    driver = _require_sqlcipher()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    key = load_db_key()
    if _is_plaintext_sqlite(path):
        _migrate_plaintext_database(path, key)

    connection = driver.connect(str(path))
    connection.row_factory = driver.Row
    connection.execute(f"PRAGMA key = '{_quoted(key)}'")

    # Force SQLCipher to validate the key immediately rather than waiting until
    # the first real query.
    try:
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except driver.DatabaseError as exc:
        connection.close()
        raise RuntimeError(
            "Could not decrypt the reddex database. The db_key may be wrong "
            f"or missing. Expected key file: {key_path()}"
        ) from exc

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    _restrict_database_files(path)
    return connection


def init_db(path: str | Path):
    connection = connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    _restrict_database_files(Path(path))
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
