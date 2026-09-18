from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import websocket

from .browser_rooms import VisibleRoom, discover_visible_rooms
from .db import (
    backfill_complete,
    init_db,
    message_exists,
    set_backfill_complete,
    upsert_message,
)
from .probe import fetch_targets, select_target

MATRIX_HOST = "matrix.redditspace.com"
MATRIX_BASE = "https://matrix.redditspace.com"
CHAT_BASE = "https://chat.reddit.com"
ProgressCallback = Callable[[str], None]


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is None:
        print(message)
    else:
        progress(message)


def _header(headers: dict[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


@dataclass(frozen=True)
class MatrixAuth:
    base_url: str
    authorization: str


def discover_matrix_auth(
    endpoint: str,
    target_filter: str = "reddit",
    timeout: float = 30.0,
) -> MatrixAuth:
    targets = fetch_targets(endpoint)
    target = select_target(targets, target_filter)
    ws = websocket.create_connection(
        str(target["webSocketDebuggerUrl"]),
        timeout=2,
        suppress_origin=True,
    )

    request_urls: dict[str, str] = {}
    extra_headers: dict[str, dict[str, Any]] = {}
    command_id = 1
    ws.send(
        json.dumps(
            {
                "id": command_id,
                "method": "Network.enable",
                "params": {},
            }
        )
    )

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                message = json.loads(ws.recv())
            except (socket.timeout, websocket.WebSocketTimeoutException):
                continue

            method = message.get("method")
            params = message.get("params", {})
            request_id = str(params.get("requestId", ""))

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                url = str(request.get("url", ""))
                request_urls[request_id] = url

                auth = _auth_from(url, request.get("headers", {}))
                if auth is not None:
                    return auth

                pending_headers = extra_headers.pop(request_id, None)
                if pending_headers is not None:
                    auth = _auth_from(url, pending_headers)
                    if auth is not None:
                        return auth

            elif method == "Network.requestWillBeSentExtraInfo":
                headers = params.get("headers", {})
                url = request_urls.get(request_id)
                if url is None:
                    extra_headers[request_id] = headers
                    continue
                auth = _auth_from(url, headers)
                if auth is not None:
                    return auth
    finally:
        ws.close()

    raise RuntimeError(
        "No authenticated Matrix request was observed. Keep Reddit Chat open "
        "in Chrome, switch rooms once, and run reddex sync again."
    )


def _auth_from(url: str, headers: dict[str, Any]) -> MatrixAuth | None:
    parsed = urlsplit(url)
    if parsed.hostname != MATRIX_HOST:
        return None

    authorization = _header(headers, "authorization")
    if not authorization:
        return None

    return MatrixAuth(
        base_url=f"{parsed.scheme}://{parsed.netloc}",
        authorization=authorization,
    )


class MatrixRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class MatrixClient:
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        auth: MatrixAuth,
        timeout: float = 30.0,
        retries: int = 3,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.auth = auth
        self.timeout = timeout
        self.retries = retries
        self.progress = progress

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.auth.base_url.rstrip("/") + path
        if params:
            url += "?" + urlencode(params)

        for attempt in range(self.retries + 1):
            request = Request(
                url,
                headers={
                    "Authorization": self.auth.authorization,
                    "Accept": "application/json",
                    "User-Agent": "reddex/0.1",
                },
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                retryable = exc.code in self.RETRYABLE_STATUS
                if retryable and attempt < self.retries:
                    delay = min(2**attempt, 8)
                    _emit(
                        self.progress,
                        f"HTTP {exc.code}; retrying in {delay}s "
                        f"({attempt + 1}/{self.retries})",
                    )
                    time.sleep(delay)
                    continue
                raise MatrixRequestError(
                    f"Matrix request failed: HTTP {exc.code} {path}: "
                    f"{body[:500]}",
                    status=exc.code,
                    retryable=retryable,
                ) from exc
            except (URLError, TimeoutError, socket.timeout) as exc:
                if attempt < self.retries:
                    delay = min(2**attempt, 8)
                    _emit(
                        self.progress,
                        f"network error; retrying in {delay}s "
                        f"({attempt + 1}/{self.retries})",
                    )
                    time.sleep(delay)
                    continue
                raise MatrixRequestError(
                    f"Matrix request failed: {path}: {exc}",
                    retryable=True,
                ) from exc

            if not isinstance(data, dict):
                raise MatrixRequestError(
                    f"Unexpected Matrix response for {path}"
                )
            return data

        raise AssertionError("unreachable")


def message_url(room_id: str, event_id: str) -> str:
    return f"{CHAT_BASE}/room/{room_id}/event/{event_id}"


def message_from_event(
    room_id: str,
    room_name: str | None,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    if event.get("type") != "m.room.message":
        return None

    event_id = event.get("event_id")
    content = event.get("content")
    if not isinstance(event_id, str) or not isinstance(content, dict):
        return None

    body = content.get("body")
    if not isinstance(body, str):
        return None

    created_at_ms = event.get("origin_server_ts")
    if not isinstance(created_at_ms, int):
        return None

    return {
        "event_id": event_id,
        "room_id": room_id,
        "room_name": room_name,
        "sender": event.get("sender"),
        "created_at_ms": created_at_ms,
        "body": body,
        "web_url": message_url(room_id, event_id),
        "raw_json": event,
    }


def _room_name(room: dict[str, Any]) -> str | None:
    state = room.get("state", {})
    events = state.get("events", []) if isinstance(state, dict) else []
    for event in events:
        if (
            isinstance(event, dict)
            and event.get("type") == "m.room.name"
            and isinstance(event.get("content"), dict)
        ):
            name = event["content"].get("name")
            if isinstance(name, str) and name:
                return name
    return None


def prepare_rooms(
    endpoint: str = "http://127.0.0.1:9222",
    target_filter: str = "reddit",
    auth_timeout: float = 30.0,
    progress: ProgressCallback | None = None,
) -> tuple[
    MatrixAuth,
    list[VisibleRoom],
    dict[str, dict[str, Any]],
]:
    _emit(progress, "Reading the visible Reddit Chat room list from Chrome...")
    browser_rooms = discover_visible_rooms(
        endpoint=endpoint,
        target_filter=target_filter,
    )

    _emit(progress, "Waiting for an authenticated Reddit Matrix request from Chrome...")
    auth = discover_matrix_auth(
        endpoint=endpoint,
        target_filter=target_filter,
        timeout=auth_timeout,
    )
    _emit(progress, "Matrix authorization found. Token remains in memory only.")

    client = MatrixClient(auth, progress=progress)
    sync_filter = json.dumps(
        {
            "room": {
                "timeline": {
                    "limit": 1,
                    "lazy_load_members": True,
                },
                "state": {
                    "lazy_load_members": True,
                },
            }
        },
        separators=(",", ":"),
    )
    initial = client.get(
        "/_matrix/client/v3/sync",
        {
            "timeout": 0,
            "filter": sync_filter,
        },
    )

    rooms_obj = initial.get("rooms", {})
    joined = rooms_obj.get("join", {}) if isinstance(rooms_obj, dict) else {}
    if not isinstance(joined, dict):
        joined = {}

    available: list[VisibleRoom] = []
    room_payloads: dict[str, dict[str, Any]] = {}
    for visible in browser_rooms:
        room = joined.get(visible.room_id)
        if not isinstance(room, dict):
            continue
        room_payloads[visible.room_id] = room
        available.append(
            VisibleRoom(
                visible.room_id,
                _room_name(room) or visible.label,
            )
        )

    if not available:
        raise RuntimeError(
            "None of the rooms visible in Reddit Chat were present in the "
            "authenticated Matrix /sync response. Refusing to fall back to "
            "all joined Matrix rooms."
        )

    _emit(
        progress,
        f"Found {len(available)} visible Reddit Chat room(s); "
        f"ignoring {max(len(joined) - len(available), 0)} other "
        "joined Matrix room(s).",
    )
    return auth, available, room_payloads


def sync_prepared(
    db_path: str,
    auth: MatrixAuth,
    available_rooms: list[VisibleRoom],
    room_payloads: dict[str, dict[str, Any]],
    selected_room_ids: set[str] | None = None,
    page_limit: int = 100,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    if selected_room_ids is None:
        selected_rooms = available_rooms
    else:
        selected_rooms = [
            room
            for room in available_rooms
            if room.room_id in selected_room_ids
        ]
    if not selected_rooms:
        raise RuntimeError("No Reddit Chat rooms were selected.")

    client = MatrixClient(auth, progress=progress)
    connection = init_db(db_path)
    room_count = 0
    new_message_count = 0

    try:
        for visible in selected_rooms:
            room_id = visible.room_id
            room = room_payloads.get(room_id)
            if not isinstance(room, dict):
                _emit(progress, f"Skipping unavailable room: {room_id}")
                continue

            room_count += 1
            room_name = visible.label
            label = room_name or room_id
            _emit(
                progress,
                f"[{room_count}/{len(selected_rooms)}] {label}",
            )

            timeline = room.get("timeline", {})
            if not isinstance(timeline, dict):
                timeline = {}

            history_complete = backfill_complete(connection, room_id)
            overlap_found = False
            room_new = 0

            timeline_events = timeline.get("events", [])
            if not isinstance(timeline_events, list):
                timeline_events = []

            for event in timeline_events:
                if not isinstance(event, dict):
                    continue
                message = message_from_event(room_id, room_name, event)
                if message is None:
                    continue

                if message_exists(connection, message["event_id"]):
                    overlap_found = True
                    continue

                upsert_message(connection, message)
                room_new += 1
                new_message_count += 1

            if history_complete and overlap_found:
                connection.commit()
                _emit(progress, f"{label}: +{room_new} new message(s)")
                continue

            token = timeline.get("prev_batch")
            seen_tokens: set[str] = set()
            reached_history_end = False

            while isinstance(token, str) and token and token not in seen_tokens:
                seen_tokens.add(token)
                try:
                    page = client.get(
                        f"/_matrix/client/v3/rooms/{room_id}/messages",
                        {
                            "from": token,
                            "dir": "b",
                            "limit": page_limit,
                        },
                    )
                except MatrixRequestError as exc:
                    connection.commit()
                    _emit(
                        progress,
                        f"{label}: skipped remaining history: {exc}",
                    )
                    break

                chunk = page.get("chunk", [])
                if not isinstance(chunk, list):
                    break

                stop_on_overlap = False
                for event in chunk:
                    if not isinstance(event, dict):
                        continue
                    message = message_from_event(room_id, room_name, event)
                    if message is None:
                        continue

                    if message_exists(connection, message["event_id"]):
                        if history_complete:
                            stop_on_overlap = True
                            break
                        continue

                    upsert_message(connection, message)
                    room_new += 1
                    new_message_count += 1

                connection.commit()
                _emit(
                    progress,
                    f"{label}: {room_new} new message(s) so far",
                )

                if stop_on_overlap:
                    break

                next_token = page.get("end")
                if (
                    not chunk
                    or not isinstance(next_token, str)
                    or not next_token
                    or next_token == token
                ):
                    reached_history_end = True
                    break
                token = next_token

            if not history_complete and reached_history_end:
                set_backfill_complete(connection, room_id, True)
                connection.commit()

            _emit(progress, f"{label}: +{room_new} new message(s)")
    finally:
        connection.close()

    return room_count, new_message_count


def sync_archive(
    db_path: str,
    endpoint: str = "http://127.0.0.1:9222",
    target_filter: str = "reddit",
    auth_timeout: float = 30.0,
    page_limit: int = 100,
    room_selector: Callable[
        [list[VisibleRoom]], list[VisibleRoom]
    ] | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    auth, available, room_payloads = prepare_rooms(
        endpoint=endpoint,
        target_filter=target_filter,
        auth_timeout=auth_timeout,
        progress=progress,
    )

    selected = room_selector(available) if room_selector else available
    selected_ids = {room.room_id for room in selected}

    return sync_prepared(
        db_path=db_path,
        auth=auth,
        available_rooms=available,
        room_payloads=room_payloads,
        selected_room_ids=selected_ids,
        page_limit=page_limit,
        progress=progress,
    )

