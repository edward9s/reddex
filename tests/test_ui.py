from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddex.ui import INDEX_HTML, UIState


class UITests(unittest.TestCase):
    def test_ui_contains_unlock_sync_search_and_message_link_controls(self) -> None:
        self.assertIn("資料庫密碼", INDEX_HTML)
        self.assertIn("同步已選", INDEX_HTML)
        self.assertIn("搜尋已封存留言", INDEX_HTML)
        self.assertIn("開啟留言", INDEX_HTML)

    def test_state_starts_locked_and_unlocks_with_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = UIState(
                db_path=path,
                endpoint="http://127.0.0.1:9222",
                target_filter="reddit",
                auth_timeout=1,
                page_limit=100,
            )

            locked = state.snapshot()
            self.assertFalse(locked["unlocked"])
            self.assertFalse(locked["key_vault_exists"])
            self.assertEqual(0, locked["message_count"])

            state.unlock("password", "password")
            unlocked = state.snapshot()
            self.assertTrue(unlocked["unlocked"])
            self.assertTrue(unlocked["key_vault_exists"])
            self.assertEqual(0, unlocked["message_count"])

    def test_first_unlock_requires_matching_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            state = UIState(
                db_path=path,
                endpoint="http://127.0.0.1:9222",
                target_filter="reddit",
                auth_timeout=1,
                page_limit=100,
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "Database passwords do not match",
            ):
                state.unlock("one", "two")

            self.assertFalse(state.snapshot()["key_vault_exists"])


if __name__ == "__main__":
    unittest.main()
