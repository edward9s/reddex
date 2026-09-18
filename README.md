# reddex

Archive Reddit Chat messages visible to your logged-in account and search them locally with SQLite FTS5.

## How it works

reddex does not automate Reddit login and does not store your Reddit password or 2FA secret.

1. Log in to Reddit normally in Android Chrome.
2. Expose that Chrome tab to Termux through ADB + Chrome DevTools Protocol (CDP).
3. `reddex sync` observes an authenticated request to Reddit's Matrix chat server and keeps the Authorization value in memory only.
4. It performs an initial Matrix sync to discover joined rooms.
5. It paginates each room's history with the Matrix `/rooms/{roomId}/messages` endpoint.
6. Text message events are written to SQLite and indexed with FTS5.
7. Each archived message gets a deep link built from its Matrix `room_id` and `event_id`.

Reddit Chat is a private implementation built on Matrix and can change. `reddex probe` remains available for debugging when that happens.

## Requirements

- Android with Chrome and Developer options enabled
- Termux
- Python 3.11+
- Android platform tools (`adb`)
- A Reddit account already logged in with Chrome

Install Termux prerequisites:

```sh
pkg update
pkg install python python-pip android-tools
```

Install reddex:

```sh
git clone https://github.com/edward9s/reddex.git
cd reddex
python -m pip install -e .
```

## 1. Connect Termux ADB to Android

Enable **Wireless debugging** in Android Developer options. Pair/connect using the addresses shown by Android:

```sh
adb pair <host>:<pairing-port>
adb connect <host>:<debug-port>
adb devices
```

Open Chrome, make sure Reddit is logged in, and open Reddit Chat.

Expose Chrome's DevTools socket:

```sh
adb forward tcp:9222 localabstract:chrome_devtools_remote
```

Confirm Chrome is reachable:

```sh
curl http://127.0.0.1:9222/json
```

## 2. Archive chat history

```sh
reddex sync
```

reddex waits for Chrome to issue an authenticated request to `matrix.redditspace.com`. If it says no authenticated Matrix request was observed, leave the command running and switch to another Reddit Chat room in Chrome, then retry if necessary.

The Matrix Authorization value is not printed or persisted by reddex.

The database defaults to:

```text
data/reddex.db
```

Local data under `data/` is ignored by Git.

## 3. Full-text search

```sh
reddex search "DuckDB"
```

Search output includes the matching text and a per-message Reddit Chat deep link.

SQLite FTS5 is maintained by triggers, so inserts, edits, and deletes update the search index without rebuilding it.

## Debug probe

For diagnosing Reddit Chat protocol changes:

```sh
reddex probe --list-targets
reddex probe
```

The probe records selected Reddit-related response bodies and WebSocket frames to:

```text
data/probe.jsonl
```

It intentionally does not persist request headers, cookies, or authorization tokens.

## Stored message fields

- room ID
- room name when available
- Matrix event/message ID
- sender
- timestamp
- body
- per-message web URL
- original event JSON

## Development

```sh
python -m unittest discover -s tests
```

## Security

`data/` can contain private Reddit Chat content. Do not commit or upload it.

reddex reuses an already authenticated browser session; it does not need your Reddit password or 2FA secret.
