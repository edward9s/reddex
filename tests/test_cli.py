from __future__ import annotations

import unittest

from reddex.cli import parse_room_selection


class RoomSelectionTests(unittest.TestCase):
    def test_single_room(self) -> None:
        self.assertEqual([2], parse_room_selection("3", 5))

    def test_multiple_rooms(self) -> None:
        self.assertEqual([0, 2, 4], parse_room_selection("1,3,5", 5))

    def test_range(self) -> None:
        self.assertEqual([1, 2, 3], parse_room_selection("2-4", 5))

    def test_spaces(self) -> None:
        self.assertEqual([0, 2], parse_room_selection("1 3", 5))

    def test_all(self) -> None:
        self.assertEqual([0, 1, 2], parse_room_selection("a", 3))

    def test_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_room_selection("6", 5)


if __name__ == "__main__":
    unittest.main()
