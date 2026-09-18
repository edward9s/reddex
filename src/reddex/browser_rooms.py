from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import websocket

from .probe import fetch_targets, select_target


@dataclass(frozen=True)
class VisibleRoom:
    room_id: str
    label: str | None = None


def room_id_from_url(url: str) -> str | None:
    try:
        path = urlsplit(url).path
    except ValueError:
        return None

    parts = [unquote(part) for part in path.split("/") if part]
    try:
        index = parts.index("room")
    except ValueError:
        return None

    if index + 1 >= len(parts):
        return None

    room_id = parts[index + 1]
    if not room_id.startswith("!") or ":reddit.com" not in room_id:
        return None
    return room_id


ROOM_DISCOVERY_JS = r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function roots(root, out = []) {
    out.push(root);
    const elements = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const element of elements) {
      if (element.shadowRoot) roots(element.shadowRoot, out);
    }
    return out;
  }

  function anchors() {
    const out = [];
    for (const root of roots(document)) {
      if (!root.querySelectorAll) continue;
      for (const anchor of root.querySelectorAll('a[href*="/room/"]')) {
        out.push(anchor);
      }
    }
    return out;
  }

  function nearestScroller(element) {
    let node = element;
    while (node) {
      if (node instanceof Element) {
        const style = getComputedStyle(node);
        const overflowY = style.overflowY;
        if (
          node.scrollHeight > node.clientHeight + 8 &&
          (overflowY === "auto" || overflowY === "scroll")
        ) {
          return node;
        }
      }
      node = node.parentNode || (node.host ?? null);
    }
    return null;
  }

  const seen = new Map();

  function collect(list) {
    for (const anchor of list) {
      const href = anchor.href;
      if (!href || !href.includes("/room/")) continue;
      const label = (
        anchor.getAttribute("aria-label") ||
        anchor.getAttribute("title") ||
        anchor.textContent ||
        ""
      ).trim();
      if (!seen.has(href) || (!seen.get(href) && label)) {
        seen.set(href, label || null);
      }
    }
  }

  let currentAnchors = anchors();
  const groups = new Map();
  for (const anchor of currentAnchors) {
    const scroller = nearestScroller(anchor);
    if (!scroller) continue;
    if (!groups.has(scroller)) groups.set(scroller, []);
    groups.get(scroller).push(anchor);
  }

  // The chat sidebar is normally the scroll container containing the largest
  // number of /room/ links. This avoids treating room links inside message
  // bodies as conversations.
  const ranked = [...groups.entries()].sort(
    (a, b) => b[1].length - a[1].length
  );
  const sidebar = ranked.length ? ranked[0][0] : null;

  if (sidebar) {
    const oldTop = sidebar.scrollTop;
    sidebar.scrollTop = 0;
    await sleep(100);

    for (let i = 0; i < 100; i++) {
      currentAnchors = anchors().filter(
        (anchor) => nearestScroller(anchor) === sidebar
      );
      collect(currentAnchors);

      const before = sidebar.scrollTop;
      const step = Math.max(Math.floor(sidebar.clientHeight * 0.8), 200);
      sidebar.scrollTop = Math.min(
        sidebar.scrollTop + step,
        sidebar.scrollHeight
      );
      await sleep(80);

      if (
        sidebar.scrollTop === before ||
        sidebar.scrollTop + sidebar.clientHeight >= sidebar.scrollHeight - 2
      ) {
        collect(
          anchors().filter((anchor) => nearestScroller(anchor) === sidebar)
        );
        break;
      }
    }

    sidebar.scrollTop = oldTop;
  } else {
    collect(currentAnchors);
  }

  // The selected room can occasionally be rendered as a non-link item.
  // location.href is therefore included explicitly.
  if (location.href.includes("/room/") && !seen.has(location.href)) {
    seen.set(location.href, document.title || null);
  }

  return [...seen.entries()].map(([href, label]) => ({ href, label }));
})()
"""


def _evaluate(
    ws: websocket.WebSocket,
    expression: str,
    timeout: float,
) -> object:
    command_id = 1
    ws.send(
        json.dumps(
            {
                "id": command_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            }
        )
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = json.loads(ws.recv())
        except (socket.timeout, websocket.WebSocketTimeoutException):
            continue

        if message.get("id") != command_id:
            continue
        if "error" in message:
            raise RuntimeError(
                f"CDP Runtime.evaluate failed: {message['error']}"
            )

        result = message.get("result", {}).get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(
                f"Chat room discovery JavaScript failed: "
                f"{result.get('description', result.get('value'))}"
            )
        return result.get("value")

    raise RuntimeError("Timed out while reading the Reddit Chat sidebar")


def discover_visible_rooms(
    endpoint: str,
    target_filter: str = "reddit",
    timeout: float = 15.0,
) -> list[VisibleRoom]:
    targets = fetch_targets(endpoint)
    target = select_target(targets, target_filter)
    ws = websocket.create_connection(
        str(target["webSocketDebuggerUrl"]),
        timeout=2,
        suppress_origin=True,
    )
    try:
        raw = _evaluate(ws, ROOM_DISCOVERY_JS, timeout)
    finally:
        ws.close()

    if not isinstance(raw, list):
        raise RuntimeError("Reddit Chat room discovery returned invalid data")

    rooms: dict[str, VisibleRoom] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        href = item.get("href")
        if not isinstance(href, str):
            continue
        room_id = room_id_from_url(href)
        if room_id is None:
            continue

        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            label = None
        rooms[room_id] = VisibleRoom(room_id, label)

    if not rooms:
        raise RuntimeError(
            "No visible Reddit Chat rooms were found in the browser UI. "
            "Open chat.reddit.com with the chat sidebar visible and try again."
        )

    return list(rooms.values())
