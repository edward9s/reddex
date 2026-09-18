from __future__ import annotations

import argparse
import re
from pathlib import Path

from .browser_rooms import discover_visible_rooms
from .db import init_db, search_messages
from .matrix import sync_archive
from .probe import run_probe

DEFAULT_DB = Path("data/reddex.db")
DEFAULT_PROBE = Path("data/probe.jsonl")


def parse_room_selection(value: str, count: int) -> list[int]:
    value = value.strip().lower()
    if value in {"a", "all", "*"}:
        return list(range(count))
    if not value:
        raise ValueError("No room selected.")

    selected: set[int] = set()
    for part in re.split(r"[,\s]+", value):
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"Invalid selection: {part}")
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            numbers = range(start, end + 1)
        else:
            if not part.isdigit():
                raise ValueError(f"Invalid selection: {part}")
            numbers = [int(part)]

        for number in numbers:
            if number < 1 or number > count:
                raise ValueError(
                    f"Room number {number} is outside 1-{count}."
                )
            selected.add(number - 1)

    if not selected:
        raise ValueError("No room selected.")
    return sorted(selected)


def choose_rooms(rooms):
    print()
    for index, room in enumerate(rooms, 1):
        label = room.label or "(unnamed)"
        print(f"[{index}] {label}")
        print(f"    {room.room_id}")
    print()

    while True:
        try:
            value = input(
                "Select room(s) (e.g. 3, 1,3,5, 2-4; a = all): "
            )
            indexes = parse_room_selection(value, len(rooms))
            return [rooms[index] for index in indexes]
        except ValueError as exc:
            print(exc)


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

    rooms_parser = subparsers.add_parser(
        "rooms",
        help="list Reddit Chat rooms currently visible in the browser UI",
    )
    rooms_parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:9222",
        help="CDP HTTP endpoint",
    )
    rooms_parser.add_argument(
        "--target-filter",
        default="reddit",
        help="substring used to choose a Chrome tab",
    )

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
    sync_parser.add_argument(
        "--all",
        action="store_true",
        dest="all_rooms",
        help="sync every visible Reddit Chat room without prompting",
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

    if args.command == "rooms":
        rooms = discover_visible_rooms(
            endpoint=args.endpoint,
            target_filter=args.target_filter,
        )
        for room in rooms:
            print(f"{room.label or '-'}\t{room.room_id}")
        print(f"{len(rooms)} visible room(s)")
        return

    if args.command == "sync":
        rooms, messages = sync_archive(
            db_path=str(args.db),
            endpoint=args.endpoint,
            target_filter=args.target_filter,
            auth_timeout=args.auth_timeout,
            page_limit=args.page_limit,
            room_selector=None if args.all_rooms else choose_rooms,
        )
        print(f"Done: {rooms} room(s), {messages} new message(s) archived.")
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
