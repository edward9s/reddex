from __future__ import annotations

import sqlite3 as plain_sqlite3
import tempfile
import unittest
from pathlib import Path

from reddex.db import (
    backfill_complete,
    create_key_vault,
    init_db,
    key_vault_path,
    message_exists,
    search_messages,
    set_backfill_complete,
    unlock_db_key,
    upsert_message,
)

DB_KEY = "11" * 32


class DatabaseTests(unittest.TestCase):
    def test_fts_tracks_insert_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, DB_KEY)
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
            connection = init_db(path, DB_KEY)
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

    def test_key_vault_encrypts_random_database_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"

            db_key = create_key_vault(path, "correct horse battery staple")
            self.assertEqual(64, len(db_key))
            self.assertEqual(
                db_key,
                unlock_db_key(path, "correct horse battery staple"),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect database password",
            ):
                unlock_db_key(path, "wrong password")

            vault = key_vault_path(path)
            self.assertTrue(vault.is_file())
            self.assertNotEqual(
                b"SQLite format 3\x00",
                vault.read_bytes()[:16],
            )

            plain = plain_sqlite3.connect(vault)
            try:
                with self.assertRaises(plain_sqlite3.DatabaseError):
                    plain.execute(
                        "SELECT db_key FROM key_vault"
                    ).fetchone()
            finally:
                plain.close()

    def test_database_is_encrypted_with_vault_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            db_key = create_key_vault(path, "password")
            connection = init_db(path, db_key)
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

    def test_existing_database_without_vault_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            plain = plain_sqlite3.connect(path)
            plain.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "Database already exists but key vault does not",
            ):
                create_key_vault(path, "password")


if __name__ == "__main__":
    unittest.main()
