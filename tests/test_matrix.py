from __future__ import annotations

import unittest

from reddex.matrix import message_from_event, message_url


class MatrixTests(unittest.TestCase):
    def test_message_url_uses_room_and_event_ids(self) -> None:
        self.assertEqual(
            "https://chat.reddit.com/room/!room:reddit.com/event/$event",
            message_url("!room:reddit.com", "$event"),
        )

    def test_message_from_event(self) -> None:
        event = {
            "type": "m.room.message",
            "event_id": "$event",
            "sender": "@user:reddit.com",
            "origin_server_ts": 123,
            "content": {
                "msgtype": "m.text",
                "body": "hello",
            },
        }
        message = message_from_event(
            "!room:reddit.com",
            "room",
            event,
        )
        assert message is not None
        self.assertEqual("hello", message["body"])
        self.assertEqual("$event", message["event_id"])
        self.assertEqual(
            "https://chat.reddit.com/room/!room:reddit.com/event/$event",
            message["web_url"],
        )

    def test_non_message_event_is_ignored(self) -> None:
        self.assertIsNone(
            message_from_event(
                "!room:reddit.com",
                None,
                {
                    "type": "m.reaction",
                    "event_id": "$event",
                    "origin_server_ts": 123,
                    "content": {},
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
