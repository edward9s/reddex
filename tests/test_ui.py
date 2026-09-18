from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddex.ui import INDEX_HTML, UIState


def make_state(path: Path) -> UIState:
    return UIState(
        db_path=path,
        endpoint="http://127.0.0.1:9222",
        target_filter="reddit",
        auth_timeout=1,
        page_limit=100,
    )


class UITests(unittest.TestCase):
    def test_ui_contains_unlock_sync_search_and_message_link_controls(self) -> None:
        self.assertIn("資料庫密碼", INDEX_HTML)
        self.assertIn("同步已選", INDEX_HTML)
        self.assertIn("搜尋已封存留言", INDEX_HTML)
        self.assertIn("開啟留言", INDEX_HTML)
        self.assertIn("變更資料庫密碼", INDEX_HTML)

    def test_state_starts_locked_and_unlocks_with_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = make_state(path)

            locked = state.snapshot()
            self.assertFalse(locked["unlocked"])
            self.assertFalse(locked["database_exists"])
            self.assertEqual(0, locked["message_count"])

            state.unlock("password", "password")
            unlocked = state.snapshot()
            self.assertTrue(unlocked["unlocked"])
            self.assertTrue(unlocked["database_exists"])
            self.assertEqual(0, unlocked["message_count"])
            self.assertTrue(path.is_file())

    def test_first_unlock_requires_matching_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = make_state(path)

            with self.assertRaisesRegex(
                RuntimeError,
                "Database passwords do not match",
            ):
                state.unlock("one", "two")

            self.assertFalse(path.exists())

    def test_change_password_updates_active_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = make_state(path)
            state.unlock("password", "password")

            state.change_password(
                "password",
                "new password",
                "new password",
            )

            self.assertEqual(
                "new password",
                state.require_database_password(),
            )

            other = make_state(path)
            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect database password or invalid database",
            ):
                other.unlock("password")
            other.unlock("new password")
            self.assertTrue(other.snapshot()["unlocked"])

    def test_change_password_rejects_wrong_current_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = make_state(path)
            state.unlock("password", "password")

            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect current database password",
            ):
                state.change_password(
                    "wrong",
                    "new password",
                    "new password",
                )

            self.assertEqual(
                "password",
                state.require_database_password(),
            )

    def test_existing_database_requires_correct_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            first = make_state(path)
            first.unlock("password", "password")

            second = make_state(path)
            with self.assertRaisesRegex(
                RuntimeError,
                "Incorrect database password or invalid database",
            ):
                second.unlock("wrong")

            self.assertFalse(second.snapshot()["unlocked"])
            second.unlock("password")
            self.assertTrue(second.snapshot()["unlocked"])


if __name__ == "__main__":
    unittest.main()
