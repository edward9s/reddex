from __future__ import annotations

import argparse
from pathlib import Path

from .db import init_db, search_messages
from .matrix import sync_archive
from .probe import run_probe

DEFAULT_DB = Path("data/reddex.db")
DEFAULT_PROBE = Path("data/probe.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reddex",
        description="Archive and search Reddit Chat locally.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="initialize the SQLite database",
    )
    init_parser.add_argument("--db", type=Path, default=DEFAULT_DB)

    search_parser = subparsers.add_parser(
        "search",
        help="full-text search archived messages",
    )
    search_parser.add_argument("query")
    search_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    search_parser.add_argument("--limit", type=int, default=20)

    sync_parser = subparsers.add_parser(
        "sync",
        help="archive Reddit Chat history through the logged-in Chrome session",
    )
    sync_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sync_parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:9222",
        help="CDP HTTP endpoint",
    )
    sync_parser.add_argument(
        "--target-filter",
        default="reddit",
        help="substring used to choose a Chrome tab",
    )
    sync_parser.add_argument(
        "--auth-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for a Matrix request from Chrome",
    )
    sync_parser.add_argument(
        "--page-limit",
        type=int,
        default=100,
        help="Matrix history events requested per page",
    )

    probe_parser = subparsers.add_parser(
        "probe",
        help="capture Reddit-related CDP network traffic for debugging",
    )
    probe_parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:9222",
        help="CDP HTTP endpoint",
    )
    probe_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PROBE,
    )
    probe_parser.add_argument(
        "--target-filter",
        default="reddit",
        help="substring used to choose a Chrome tab",
    )
    probe_parser.add_argument(
        "--filter",
        default="reddit",
        dest="capture_filter",
        help="substring required in captured network URLs",
    )
    probe_parser.add_argument(
        "--list-targets",
        action="store_true",
        help="list available Chrome tabs and exit",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "init":
        connection = init_db(args.db)
        connection.close()
        print(args.db)
        return

    if args.command == "search":
        connection = init_db(args.db)
        try:
            rows = search_messages(connection, args.query, args.limit)
        finally:
            connection.close()

        for row in rows:
            print(
                f"{row['event_id']}  "
                f"{row['sender'] or '-'}  "
                f"{row['room_name'] or row['room_id']}"
            )
            print(row["snippet"])
            print(row["web_url"])
            print()
        return

    if args.command == "sync":
        rooms, messages = sync_archive(
            db_path=str(args.db),
            endpoint=args.endpoint,
            target_filter=args.target_filter,
            auth_timeout=args.auth_timeout,
            page_limit=args.page_limit,
        )
        print(f"Done: {rooms} room(s), {messages} message event(s) processed.")
        return

    if args.command == "probe":
        run_probe(
            endpoint=args.endpoint,
            output=args.output,
            target_filter=args.target_filter,
            capture_filter=args.capture_filter,
            list_targets=args.list_targets,
        )
        return

    raise AssertionError(f"Unhandled command: {args.command}")
