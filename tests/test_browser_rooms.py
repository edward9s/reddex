from __future__ import annotations

import unittest

from reddex.browser_rooms import room_id_from_url


class BrowserRoomTests(unittest.TestCase):
    def test_extracts_plain_room_id(self) -> None:
        self.assertEqual(
            "!abc:reddit.com",
            room_id_from_url(
                "https://chat.reddit.com/room/!abc:reddit.com"
            ),
        )

    def test_extracts_percent_encoded_room_id(self) -> None:
        self.assertEqual(
            "!abc:reddit.com",
            room_id_from_url(
                "https://chat.reddit.com/room/!abc%3Areddit.com"
            ),
        )

    def test_ignores_non_room_links(self) -> None:
        self.assertIsNone(
            room_id_from_url("https://chat.reddit.com/discover")
        )


if __name__ == "__main__":
    unittest.main()
