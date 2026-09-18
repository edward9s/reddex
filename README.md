# reddex

Archive Reddit Chat messages visible to your logged-in account and search them locally.

## What it does

- Reuses an already logged-in Reddit Chat browser session
- Discovers the Reddit Chat rooms visible in that browser
- Archives message history through Reddit's Matrix backend
- Stores messages in SQLite
- Keeps a per-message Reddit Chat URL
- Supports incremental sync after the first complete backfill
- Provides a local web UI
- Supports case-insensitive partial and SMM-style fuzzy search

reddex does not automate Reddit login and does not store your Reddit password or 2FA secret. The Matrix Authorization value is kept in process memory only.

## Requirements

- Python 3.11+
- Chrome
- A Reddit account already logged in to the Chrome instance exposed through Chrome DevTools Protocol (CDP)
- Android: Termux + Android platform tools (`adb`)
- PC: Windows, macOS, or Linux with Chrome remote debugging enabled

Install reddex:

```sh
git clone https://github.com/edward9s/reddex.git
cd reddex
python -m pip install -e .
```

## Start reddex

Run:

```sh
reddex
```

reddex starts the local web server and automatically opens:

```text
http://127.0.0.1:8787
```

On Termux it prefers `termux-open-url`; on desktop platforms it uses Python's normal browser launcher. If automatic opening fails, the URL is still printed in the terminal.

The web server binds to `127.0.0.1` by default, so it is only reachable from the same device.

The UI provides:

- Load the current Reddit Chat room list
- Select one or more rooms
- Incremental sync with live progress
- Archived-message count
- Search
- An **Open message** link for every result

## Android / Termux setup

Install prerequisites:

```sh
pkg update
pkg install python python-pip android-tools
```

Enable **Wireless debugging** in Android Developer options. Pair/connect using the addresses shown by Android:

```sh
adb pair <host>:<pairing-port>
adb connect <host>:<debug-port>
adb devices
```

Open Chrome, log in to Reddit, and open Reddit Chat.

Expose Chrome's DevTools socket:

```sh
adb forward tcp:9222 localabstract:chrome_devtools_remote
```

Confirm Chrome is reachable:

```sh
curl http://127.0.0.1:9222/json
```

Then run:

```sh
reddex
```

If **Load rooms** waits for an authenticated Matrix request, switch to the Reddit Chat tab in Chrome and change rooms once.

## PC setup

reddex uses the same CDP endpoint on PC:

```text
http://127.0.0.1:9222
```

Chrome 136 and newer do not honor `--remote-debugging-port` against the normal default Chrome profile. Start a separate Chrome profile for reddex with both `--remote-debugging-port=9222` and a non-default `--user-data-dir`, then log in to Reddit in that profile.

### Windows

From Command Prompt:

```bat
start chrome --remote-debugging-port=9222 --user-data-dir="%TEMP%\reddex-chrome"
```

### macOS

```sh
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.reddex-chrome"
```

### Linux

```sh
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.reddex-chrome"
```

In that Chrome instance:

1. Log in to Reddit.
2. Open Reddit Chat.
3. Run `reddex`.
4. Use **Load rooms** in the automatically opened reddex page.

You can verify the CDP endpoint before starting reddex:

```sh
curl http://127.0.0.1:9222/json
```

Chrome's current remote-debugging behavior is documented by Chrome for Developers:

- https://developer.chrome.com/blog/remote-debugging-port
- https://developer.chrome.com/docs/devtools/agents/get-started/configuration

## Sync behavior

The first successful sync of a room performs a historical backfill.

After reddex reaches the end of that room's history, it records the room as fully backfilled. Later syncs are incremental:

```text
newest messages
    ↓
archive unseen event_id values
    ↓
reach an event already stored in SQLite
    ↓
stop walking backward
```

This avoids scanning the full room history every time.

If an earlier backfill stopped because of a network/server error, reddex does not mark that room complete. A later run continues doing a full backfill until the historical end is reached, which avoids silently leaving a gap.

## Search

The web UI and CLI use the same search engine.

CLI example:

```sh
reddex search "DuckDB"
```

Search is case-insensitive and supports partial text. For example:

```text
ChatGPT  ← search: chat
HelloWorld ← search: world
這是一篇關於資料庫索引的留言 ← search: 資料庫
```

Literal partial text is matched directly anywhere in the message. After that, reddex uses the same matching tiers as SMM's Debug Console:

1. exact word
2. prefix
3. substring
4. compact ordered fuzzy subsequence

Examples of ordered fuzzy matching:

```text
goem     → Golem
potheal  → PotionOfHealing
atk      → attack
```

For fuzzy matches:

- query letters must remain in order;
- subsequence fuzzy matching requires at least 3 query characters;
- gaps and total matched span are limited;
- smaller gaps rank higher;
- earlier starts and word/CamelCase boundaries rank higher;
- overly scattered matches are rejected.

Search ranking prioritizes match quality first. Equally good matches are ordered with newer messages first.

SQLite FTS5 is still maintained incrementally by triggers, but the current smart-search ranking reads archived messages and applies the SMM-style matcher itself; FTS5 is not what provides the fuzzy ranking.

## Stored message fields

Each archived message stores:

- room ID
- room name when available
- Matrix event/message ID
- sender
- timestamp
- body
- per-message web URL
- original event JSON

The per-message URL is built from the Matrix room and event IDs:

```text
https://chat.reddit.com/room/{room_id}/event/{event_id}
```

## CLI commands

The web UI is the default:

```sh
reddex
```

The lower-level commands remain available for debugging or scripting:

```sh
reddex sync
reddex sync --all
reddex rooms
reddex search "query"
reddex init
reddex probe
reddex probe --list-targets
```

## Database

The default database is:

```text
data/reddex.db
```

Local data under `data/` is ignored by Git.

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

## Development

```sh
python -m unittest discover -s tests
```

## Security

`data/` can contain private Reddit Chat content. Do not commit or upload it.

reddex reuses an already authenticated browser session; it does not need your Reddit password or 2FA secret.

Reddit Chat is a private implementation built on Matrix and can change. `reddex probe` remains available for debugging when that happens.
