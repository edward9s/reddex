from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import websocket


def fetch_targets(endpoint: str) -> list[dict[str, Any]]:
    url = endpoint.rstrip("/") + "/json"
    with urlopen(url, timeout=5) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected CDP target response from {url}")
    return data


def print_targets(targets: list[dict[str, Any]]) -> None:
    for index, target in enumerate(targets):
        print(
            f"{index:2}  {target.get('title', '')}\n"
            f"    {target.get('url', '')}"
        )


def select_target(
    targets: list[dict[str, Any]],
    needle: str,
) -> dict[str, Any]:
    needle = needle.lower()
    candidates = [
        target
        for target in targets
        if target.get("type", "page") == "page"
        and needle
        in (
            str(target.get("url", ""))
            + " "
            + str(target.get("title", ""))
        ).lower()
        and target.get("webSocketDebuggerUrl")
    ]
    if not candidates:
        raise RuntimeError(
            f"No CDP page target matching {needle!r}. "
            "Open Reddit Chat in Chrome and try `reddex probe --list-targets`."
        )
    return candidates[0]


def _matches(url: str, needle: str) -> bool:
    if not needle:
        return True
    return needle.lower() in url.lower()


def _textual_mime(mime_type: str) -> bool:
    mime_type = mime_type.lower()
    return (
        mime_type.startswith("text/")
        or "json" in mime_type
        or "javascript" in mime_type
        or "graphql" in mime_type
        or "xml" in mime_type
    )


@dataclass
class PendingCommand:
    kind: str
    metadata: dict[str, Any]


class Probe:
    def __init__(
        self,
        ws_url: str,
        output: Path,
        capture_filter: str,
    ) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=30,
            suppress_origin=True,
        )
        self.output = output
        self.capture_filter = capture_filter
        self.next_id = 1
        self.pending: dict[int, PendingCommand] = {}
        self.responses: dict[str, dict[str, Any]] = {}
        self.websockets: dict[str, str] = {}

    def close(self) -> None:
        self.ws.close()

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        pending: PendingCommand | None = None,
    ) -> int:
        command_id = self.next_id
        self.next_id += 1
        payload = {
            "id": command_id,
            "method": method,
            "params": params or {},
        }
        if pending is not None:
            self.pending[command_id] = pending
        self.ws.send(json.dumps(payload))
        return command_id

    def write(self, handle: Any, record: dict[str, Any]) -> None:
        handle.write(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        handle.flush()

    def run(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.send("Network.enable")

        with self.output.open("a", encoding="utf-8") as handle:
            while True:
                message = json.loads(self.ws.recv())

                if "id" in message:
                    self._handle_command_result(handle, message)
                    continue

                method = message.get("method")
                params = message.get("params", {})

                if method == "Network.responseReceived":
                    response = params.get("response", {})
                    url = str(response.get("url", ""))
                    mime_type = str(response.get("mimeType", ""))
                    if _matches(url, self.capture_filter) and _textual_mime(
                        mime_type
                    ):
                        self.responses[str(params["requestId"])] = {
                            "url": url,
                            "status": response.get("status"),
                            "mime_type": mime_type,
                        }

                elif method == "Network.loadingFinished":
                    request_id = str(params.get("requestId", ""))
                    metadata = self.responses.pop(request_id, None)
                    if metadata is not None:
                        self.send(
                            "Network.getResponseBody",
                            {"requestId": request_id},
                            PendingCommand("http_body", metadata),
                        )

                elif method == "Network.loadingFailed":
                    self.responses.pop(str(params.get("requestId", "")), None)

                elif method == "Network.webSocketCreated":
                    request_id = str(params.get("requestId", ""))
                    url = str(params.get("url", ""))
                    if _matches(url, self.capture_filter):
                        self.websockets[request_id] = url

                elif method in (
                    "Network.webSocketFrameReceived",
                    "Network.webSocketFrameSent",
                ):
                    request_id = str(params.get("requestId", ""))
                    url = self.websockets.get(request_id)
                    if url is None:
                        continue
                    frame = params.get("response", {})
                    self.write(
                        handle,
                        {
                            "type": "websocket",
                            "direction": (
                                "received"
                                if method.endswith("Received")
                                else "sent"
                            ),
                            "url": url,
                            "opcode": frame.get("opcode"),
                            "payload": frame.get("payloadData", ""),
                        },
                    )

                elif method == "Network.webSocketClosed":
                    self.websockets.pop(
                        str(params.get("requestId", "")),
                        None,
                    )

    def _handle_command_result(
        self,
        handle: Any,
        message: dict[str, Any],
    ) -> None:
        pending = self.pending.pop(int(message["id"]), None)
        if pending is None:
            return

        if "error" in message:
            self.write(
                handle,
                {
                    "type": "cdp_error",
                    "operation": pending.kind,
                    "metadata": pending.metadata,
                    "error": message["error"],
                },
            )
            return

        if pending.kind == "http_body":
            result = message.get("result", {})
            self.write(
                handle,
                {
                    "type": "http_response",
                    **pending.metadata,
                    "base64_encoded": bool(result.get("base64Encoded")),
                    "body": result.get("body", ""),
                },
            )


def run_probe(
    endpoint: str,
    output: str | Path,
    target_filter: str = "reddit",
    capture_filter: str = "reddit",
    list_targets: bool = False,
) -> None:
    targets = fetch_targets(endpoint)

    if list_targets:
        print_targets(targets)
        return

    target = select_target(targets, target_filter)
    ws_url = str(target["webSocketDebuggerUrl"])
    print(f"Target: {target.get('title', '')}")
    print(f"URL:    {target.get('url', '')}")
    print(f"Output: {output}")
    print("Capturing Reddit network data. Press Ctrl+C to stop.")

    probe = Probe(ws_url, Path(output), capture_filter)
    try:
        probe.run()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        probe.close()
