from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import websocket

from .db import init_db, upsert_message
from .probe import fetch_targets, select_target

MATRIX_HOST = "matrix.redditspace.com"
MATRIX_BASE = "https://matrix.redditspace.com"
CHAT_BASE = "https://chat.reddit.com"


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


class MatrixClient:
    def __init__(self, auth: MatrixAuth, timeout: float = 30.0) -> None:
        self.auth = auth
        self.timeout = timeout

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.auth.base_url.rstrip("/") + path
        if params:
            url += "?" + urlencode(params)

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
            raise RuntimeError(
                f"Matrix request failed: HTTP {exc.code} {path}: {body[:500]}"
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Matrix response for {path}")
        return data


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


def sync_archive(
    db_path: str,
    endpoint: str = "http://127.0.0.1:9222",
    target_filter: str = "reddit",
    auth_timeout: float = 30.0,
    page_limit: int = 100,
) -> tuple[int, int]:
    print("Waiting for an authenticated Reddit Matrix request from Chrome...")
    auth = discover_matrix_auth(
        endpoint=endpoint,
        target_filter=target_filter,
        timeout=auth_timeout,
    )
    print("Matrix authorization found. Token remains in memory only.")

    client = MatrixClient(auth)
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

    connection = init_db(db_path)
    room_count = 0
    message_count = 0

    try:
        for room_id, room in joined.items():
            if not isinstance(room_id, str) or not isinstance(room, dict):
                continue

            room_count += 1
            room_name = _room_name(room)
            label = room_name or room_id
            print(f"[{room_count}/{len(joined)}] {label}")

            timeline = room.get("timeline", {})
            if not isinstance(timeline, dict):
                timeline = {}

            for event in timeline.get("events", []):
                if not isinstance(event, dict):
                    continue
                message = message_from_event(room_id, room_name, event)
                if message is not None:
                    upsert_message(connection, message)
                    message_count += 1

            token = timeline.get("prev_batch")
            seen_tokens: set[str] = set()

            while isinstance(token, str) and token and token not in seen_tokens:
                seen_tokens.add(token)
                page = client.get(
                    f"/_matrix/client/v3/rooms/{room_id}/messages",
                    {
                        "from": token,
                        "dir": "b",
                        "limit": page_limit,
                    },
                )
                chunk = page.get("chunk", [])
                if not isinstance(chunk, list):
                    break

                for event in chunk:
                    if not isinstance(event, dict):
                        continue
                    message = message_from_event(room_id, room_name, event)
                    if message is not None:
                        upsert_message(connection, message)
                        message_count += 1

                connection.commit()
                print(f"    archived {message_count} message event(s)", end="\r")

                next_token = page.get("end")
                if (
                    not chunk
                    or not isinstance(next_token, str)
                    or not next_token
                    or next_token == token
                ):
                    break
                token = next_token

            connection.commit()
            print(f"    archived {message_count} message event(s)")
    finally:
        connection.close()

    return room_count, message_count
