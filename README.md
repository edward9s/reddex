# reddex

Archive Reddit Chat messages that are visible to your logged-in account and make them searchable locally with SQLite FTS5.

## Status

Early bootstrap. The current goal is deliberately small:

1. Use your normal Android Chrome session for Reddit authentication.
2. Connect to that already logged-in tab through Chrome DevTools Protocol (CDP).
3. Capture the Reddit Chat network traffic needed to learn the current message format.
4. Store parsed messages in SQLite.
5. Search message text with SQLite FTS5.

The Reddit Chat parser is intentionally **not** hard-coded yet. Reddit Chat is not a stable public API, so `reddex probe` comes first: capture real traffic from your own authorized session, inspect the shape, then implement the parser against observed data.

## Requirements

- Android with Chrome and Developer options enabled
- Termux
- Python 3.11+
- Android platform tools (`adb`)
- A Reddit account already logged in with Chrome

Install the Termux prerequisites:

```sh
pkg update
pkg install python android-tools
```

Install reddex from the repository:

```sh
git clone https://github.com/edward9s/reddex.git
cd reddex
python -m pip install -e .
```

## 1. Connect Termux ADB to Android

Enable **Wireless debugging** in Android Developer options, then pair/connect ADB using the address and ports shown by Android:

```sh
adb pair <host>:<pairing-port>
adb connect <host>:<debug-port>
adb devices
```

Open Chrome and make sure Reddit Chat is already logged in.

Expose Chrome's DevTools socket:

```sh
adb forward tcp:9222 localabstract:chrome_devtools_remote
```

Check that Chrome targets are visible:

```sh
curl http://127.0.0.1:9222/json
```

## 2. Probe Reddit Chat traffic

Open a Reddit Chat room in Chrome, then run:

```sh
reddex probe
```

By default the probe:

- connects to `http://127.0.0.1:9222`
- selects a Reddit tab
- enables CDP Network events
- records Reddit-related HTTP response bodies and WebSocket frames
- writes JSONL to `data/probe.jsonl`
- deliberately does not save request headers, cookies, or passwords

While it is running, open rooms and scroll older messages in Chrome. Stop with Ctrl+C.

Useful options:

```sh
reddex probe --list-targets
reddex probe --endpoint http://127.0.0.1:9222
reddex probe --output data/probe.jsonl
reddex probe --filter reddit
```

The `data/` directory is ignored by Git. Probe captures can contain private chat contents and must not be committed.

## 3. Initialize/search the SQLite database

```sh
reddex init
reddex search "some words"
```

The database defaults to `data/reddex.db`. FTS5 is maintained by SQLite triggers, so later inserts/updates/deletes are reflected in full-text search without rebuilding the index.

The initial message schema keeps:

- room ID and optional room name
- event/message ID
- sender
- timestamp
- message body
- per-message web URL
- original parsed JSON for forward compatibility

## Development

Run the built-in tests:

```sh
python -m unittest discover -s tests
```

## Security

reddex is designed around an existing browser login. It should not need your Reddit password or 2FA secret.

Local files under `data/` may contain private Reddit Chat data. Keep them out of source control and backups you do not trust.
