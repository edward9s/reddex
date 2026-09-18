from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddex.db import init_db, search_messages, upsert_message


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


if __name__ == "__main__":
    unittest.main()
