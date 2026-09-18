from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reddex.db import init_db
from reddex.ui import INDEX_HTML, UIState


class UITests(unittest.TestCase):
    def test_ui_contains_sync_search_and_message_link_controls(self) -> None:
        self.assertIn("Sync selected", INDEX_HTML)
        self.assertIn("Search archived messages", INDEX_HTML)
        self.assertIn("Open message", INDEX_HTML)

    def test_state_counts_archived_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reddex.db"
            connection = init_db(path)
            connection.close()

            state = UIState(
                db_path=path,
                endpoint="http://127.0.0.1:9222",
                target_filter="reddit",
                auth_timeout=1,
                page_limit=100,
            )
            self.assertEqual(0, state.message_count())


if __name__ == "__main__":
    unittest.main()
