from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddex.db import init_db, upsert_message
from reddex.search import (
    fuzzy_score,
    match_rank,
    smart_search_messages,
)

PASSWORD = "test password"


class SearchTests(unittest.TestCase):
    def test_case_insensitive_matching(self) -> None:
        self.assertEqual(0, match_rank("ChatGPT", "chatgpt"))
        self.assertEqual(0, match_rank("OPUS", "opus"))

    def test_smm_style_match_order(self) -> None:
        self.assertEqual(0, match_rank("attack", "attack"))
        self.assertEqual(1, match_rank("attacker", "attack"))
        self.assertEqual(2, match_rank("counterattack", "attack"))
        self.assertEqual(3, match_rank("Golem", "goem"))
        self.assertEqual(3, match_rank("PotionOfHealing", "potheal"))

    def test_fuzzy_matching_rejects_short_or_scattered_queries(self) -> None:
        self.assertEqual(-1, fuzzy_score("attack", "ak"))
        self.assertEqual(-1, fuzzy_score("abcdefghijklmnop", "afkp"))

    def test_search_ranks_case_insensitive_exact_word_before_fuzzy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            try:
                messages = [
                    {
                        "event_id": "$exact",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "alice",
                        "created_at_ms": 100,
                        "body": "I use ChatGPT every day",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$exact",
                        "raw_json": {},
                    },
                    {
                        "event_id": "$fuzzy",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "bob",
                        "created_at_ms": 200,
                        "body": "I wrote about ChariotGPT",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$fuzzy",
                        "raw_json": {},
                    },
                ]
                for message in messages:
                    upsert_message(connection, message)
                connection.commit()

                rows = smart_search_messages(connection, "CHATGPT", 10)
                self.assertEqual("$exact", rows[0]["event_id"])
            finally:
                connection.close()

    def test_partial_english_and_chinese_text_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            try:
                messages = [
                    {
                        "event_id": "$english",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "alice",
                        "created_at_ms": 100,
                        "body": "Using ChatGPT for coding",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$english",
                        "raw_json": {},
                    },
                    {
                        "event_id": "$chinese",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "bob",
                        "created_at_ms": 200,
                        "body": "這是一篇關於資料庫索引的留言",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$chinese",
                        "raw_json": {},
                    },
                ]
                for message in messages:
                    upsert_message(connection, message)
                connection.commit()

                self.assertEqual(
                    ["$english"],
                    [
                        row["event_id"]
                        for row in smart_search_messages(
                            connection, "chat", 10
                        )
                    ],
                )
                self.assertEqual(
                    ["$chinese"],
                    [
                        row["event_id"]
                        for row in smart_search_messages(
                            connection, "資料庫", 10
                        )
                    ],
                )
            finally:
                connection.close()

    def test_search_supports_ordered_fuzzy_word_matching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            try:
                upsert_message(
                    connection,
                    {
                        "event_id": "$golem",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "alice",
                        "created_at_ms": 100,
                        "body": "The Golem appeared.",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$golem",
                        "raw_json": {},
                    },
                )
                connection.commit()

                rows = smart_search_messages(connection, "goem", 10)
                self.assertEqual(["$golem"], [row["event_id"] for row in rows])
            finally:
                connection.close()

    def test_reddit_urls_do_not_participate_in_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path, PASSWORD)
            try:
                messages = [
                    {
                        "event_id": "$reddit",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "alice",
                        "created_at_ms": 100,
                        "body": (
                            "look https://www.reddit.com/r/test/comments/"
                            "abc123/tmdXYZ/"
                        ),
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$reddit",
                        "raw_json": {},
                    },
                    {
                        "event_id": "$generic",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "bob",
                        "created_at_ms": 200,
                        "body": "look https://example.com/tmdXYZ/",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$generic",
                        "raw_json": {},
                    },
                    {
                        "event_id": "$real",
                        "room_id": "!r:reddit.com",
                        "room_name": "Room",
                        "sender": "carol",
                        "created_at_ms": 300,
                        "body": "TMD is written here.",
                        "web_url": "https://chat.reddit.com/room/!r:reddit.com/event/$real",
                        "raw_json": {},
                    },
                ]
                for message in messages:
                    upsert_message(connection, message)
                connection.commit()

                rows = smart_search_messages(connection, "tmd", 10)
                ids = [row["event_id"] for row in rows]
                self.assertNotIn("$reddit", ids)
                self.assertIn("$generic", ids)
                self.assertIn("$real", ids)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
