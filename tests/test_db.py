from __future__ import annotations

import sqlite3 as plain_sqlite3
import tempfile
import unittest
from pathlib import Path

from reddex.db import (
    backfill_complete,
    connect,
    init_db,
    message_exists,
    search_messages,
    set_backfill_complete,
    upsert_message,
)

PASSWORD = "correct horse battery staple"


class DatabaseTests(unittest.TestCase):
    def test_fts_tracks_insert_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
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
            connection = init_db(path, PASSWORD)
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

    def test_database_is_encrypted_with_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            connection.close()

            self.assertNotEqual(
                b"SQLite format 3\x00",
                path.read_bytes()[:16],
            )

            plain = plain_sqlite3.connect(path)
            try:
                with self.assertRaises(plain_sqlite3.DatabaseError):
                    plain.execute(
                        "SELECT COUNT(*) FROM messages"
                    ).fetchone()
            finally:
                plain.close()

    def test_wrong_password_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            connection.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect database password or invalid database",
            ):
                connect(path, "wrong password")

    def test_plaintext_database_is_not_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            plain = plain_sqlite3.connect(path)
            try:
                plain.execute("CREATE TABLE marker (value TEXT NOT NULL)")
                plain.commit()
            finally:
                plain.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect database password or invalid database",
            ):
                connect(path, PASSWORD)

            self.assertEqual(
                b"SQLite format 3\x00",
                path.read_bytes()[:16],
            )

    def test_empty_password_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            with self.assertRaisesRegex(
                RuntimeError,
                "Database password must not be empty",
            ):
                init_db(path, "")


if __name__ == "__main__":
    unittest.main()
