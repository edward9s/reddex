from __future__ import annotations

import os
import sqlite3 as plain_sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reddex.db import (
    backfill_complete,
    init_db,
    message_exists,
    search_messages,
    set_backfill_complete,
    upsert_message,
)


class DatabaseTests(unittest.TestCase):
    def test_fts_tracks_insert_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path)
            try:
                message = {
                    "event_id": "$event-1",
                    "room_id": "!room:reddit.com",
                    "room_name": "test room",
                    "sender": "alice",
                    "created_at_ms": 1_700_000_000_000,
                    "body": "DuckDB index discussion",
                    "web_url": "https://chat.reddit.com/room/x/event/y",
                    "raw_json": {"example": True},
                }

                upsert_message(connection, message)
                connection.commit()

                rows = search_messages(connection, "DuckDB")
                self.assertEqual(["$event-1"], [row["event_id"] for row in rows])

                message["body"] = "SQLite full text search"
                upsert_message(connection, message)
                connection.commit()

                self.assertEqual([], search_messages(connection, "DuckDB"))
                rows = search_messages(connection, "SQLite")
                self.assertEqual(["$event-1"], [row["event_id"] for row in rows])

                connection.execute(
                    "DELETE FROM messages WHERE event_id = ?",
                    ("$event-1",),
                )
                connection.commit()
                self.assertEqual([], search_messages(connection, "SQLite"))
            finally:
                connection.close()

    def test_incremental_sync_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path)
            try:
                self.assertFalse(
                    backfill_complete(connection, "!room:reddit.com")
                )
                self.assertFalse(message_exists(connection, "$missing"))

                message = {
                    "event_id": "$existing",
                    "room_id": "!room:reddit.com",
                    "room_name": "test room",
                    "sender": "alice",
                    "created_at_ms": 1_700_000_000_000,
                    "body": "hello",
                    "web_url": (
                        "https://chat.reddit.com/room/"
                        "!room:reddit.com/event/$existing"
                    ),
                    "raw_json": {"example": True},
                }
                upsert_message(connection, message)
                set_backfill_complete(
                    connection, "!room:reddit.com", True
                )
                connection.commit()

                self.assertTrue(message_exists(connection, "$existing"))
                self.assertTrue(
                    backfill_complete(connection, "!room:reddit.com")
                )
            finally:
                connection.close()

    def test_plaintext_database_is_migrated_to_sqlcipher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reddex.db"
            key_file = root / "db_key"

            plain = plain_sqlite3.connect(path)
            try:
                plain.execute("CREATE TABLE marker (value TEXT NOT NULL)")
                plain.execute("INSERT INTO marker VALUES ('secret')")
                plain.commit()
            finally:
                plain.close()

            self.assertEqual(
                b"SQLite format 3\x00",
                path.read_bytes()[:16],
            )

            with patch.dict(
                os.environ,
                {"REDDEX_DB_KEY_FILE": str(key_file)},
            ):
                connection = init_db(path)
                try:
                    row = connection.execute(
                        "SELECT value FROM marker"
                    ).fetchone()
                    self.assertEqual("secret", row["value"])
                finally:
                    connection.close()

            self.assertTrue(key_file.exists())
            self.assertEqual(64, len(key_file.read_text().strip()))
            self.assertNotEqual(
                b"SQLite format 3\x00",
                path.read_bytes()[:16],
            )

            plain = plain_sqlite3.connect(path)
            try:
                with self.assertRaises(plain_sqlite3.DatabaseError):
                    plain.execute(
                        "SELECT value FROM marker"
                    ).fetchone()
            finally:
                plain.close()


if __name__ == "__main__":
    unittest.main()
