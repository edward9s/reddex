from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .db import (
    DatabaseError,
    change_database_password,
    connect,
    init_db,
)
from .search import smart_search_messages
from .matrix import MatrixAuth, prepare_rooms, sync_prepared


INDEX_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>reddex</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#1f2328;background:#f6f8fa}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:980px;margin:auto;padding:20px}
header{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{margin:0;font-size:1.7rem}.muted{color:#656d76}.card{background:white;border:1px solid #d0d7de;border-radius:12px;padding:16px;margin-top:16px}
button,input{font:inherit}button{border:1px solid #8c959f;border-radius:8px;background:#f6f8fa;padding:8px 12px;cursor:pointer}
button.primary{background:#1f883d;color:white;border-color:#1f883d}button:disabled{opacity:.55;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:220px}
input[type=search],input[type=password]{width:100%;padding:10px;border:1px solid #8c959f;border-radius:8px}
.rooms{display:grid;gap:8px;margin-top:12px}.room{display:flex;gap:10px;align-items:flex-start;padding:10px;border:1px solid #d8dee4;border-radius:8px}
.room-id{font:12px ui-monospace,monospace;color:#656d76;word-break:break-all}
.log{background:#0d1117;color:#c9d1d9;border-radius:8px;padding:12px;min-height:90px;max-height:260px;overflow:auto;white-space:pre-wrap;font:12px ui-monospace,monospace}
.results{display:grid;gap:10px;margin-top:12px}.result{padding:12px;border:1px solid #d8dee4;border-radius:8px}.body{white-space:pre-wrap;word-break:break-word}
.meta{font-size:12px;color:#656d76;margin-bottom:6px}.result a{display:inline-block;margin-top:8px}
.bad{color:#cf222e}.good{color:#1a7f37}
</style>
</head>
<body><div class="wrap">
<header><div><h1>reddex</h1><div class="muted">本機 Reddit Chat 封存</div></div><div id="summary" class="muted"></div></header>

<section id="unlockCard" class="card">
  <form id="unlockForm">
    <strong id="unlockTitle">解鎖資料庫</strong>
    <div class="row" style="margin-top:12px">
      <input id="password" class="grow" type="password" placeholder="資料庫密碼" autocomplete="off">
      <input id="confirmPassword" class="grow" type="password" placeholder="再次輸入密碼" autocomplete="off" hidden>
      <button id="unlockButton" class="primary">解鎖</button>
    </div>
    <div id="unlockHint" class="muted" style="margin-top:10px"></div>
    <div id="unlockError" class="bad"></div>
  </form>
</section>

<div id="app" hidden>
<section class="card">
  <div class="row">
    <button id="load" class="primary">載入聊天室</button>
    <button id="all">全選</button>
    <button id="none">全不選</button>
    <button id="sync" class="primary" disabled>同步已選</button>
    <span id="phase" class="muted"></span>
  </div>
  <div class="muted" style="margin-top:10px">若載入時停在等待 Matrix 授權，切到 Reddit Chat 分頁並切換一次聊天室。</div>
  <div id="rooms" class="rooms"></div>
</section>

<section class="card">
  <strong>進度</strong>
  <div id="error" class="bad"></div>
  <pre id="log" class="log"></pre>
</section>

<section class="card">
  <form id="searchForm" class="row">
    <input id="query" class="grow" type="search" placeholder="搜尋已封存留言" autocomplete="off">
    <button class="primary">搜尋</button>
  </form>
  <div id="results" class="results"></div>
</section>

<section class="card">
  <form id="passwdForm">
    <strong>變更資料庫密碼</strong>
    <div class="row" style="margin-top:12px">
      <input id="currentPassword" class="grow" type="password" placeholder="目前密碼" autocomplete="off">
      <input id="newPassword" class="grow" type="password" placeholder="新密碼" autocomplete="off">
      <input id="confirmNewPassword" class="grow" type="password" placeholder="再次輸入新密碼" autocomplete="off">
      <button class="primary">變更密碼</button>
    </div>
    <div id="passwdStatus" style="margin-top:10px"></div>
  </form>
</section>
</div>
</div>
<script>
const $ = (id) => document.getElementById(id);
let knownRooms = [];
let lastLogSize = -1;
let unlocked = false;

function esc(s){return String(s ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}

function renderRooms(rooms){
  knownRooms = rooms || [];
  $("rooms").innerHTML = knownRooms.map((r,i)=>`
    <label class="room">
      <input type="checkbox" class="roomCheck" value="${esc(r.room_id)}" checked>
      <span><strong>${esc(r.label || "(unnamed)")}</strong>
      <div class="room-id">${esc(r.room_id)}</div></span>
    </label>`).join("");
  $("sync").disabled = knownRooms.length === 0;
}

async function post(path, body={}){
  const r = await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await r.json();
  if(!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

$("unlockForm").onsubmit = async (ev)=>{
  ev.preventDefault();
  $("unlockError").textContent="";
  const password=$("password").value;
  const confirm_password=$("confirmPassword").hidden ? "" : $("confirmPassword").value;
  try{
    await post("/api/unlock",{password,confirm_password});
    $("password").value="";
    $("confirmPassword").value="";
    await pollOnce();
  }catch(e){
    $("unlockError").textContent=e.message;
  }
};

$("passwdForm").onsubmit = async (ev)=>{
  ev.preventDefault();
  const status=$("passwdStatus");
  status.className="";
  status.textContent="";
  try{
    await post("/api/passwd",{
      current_password:$("currentPassword").value,
      new_password:$("newPassword").value,
      confirm_password:$("confirmNewPassword").value
    });
    $("currentPassword").value="";
    $("newPassword").value="";
    $("confirmNewPassword").value="";
    status.className="good";
    status.textContent="資料庫密碼已變更。";
  }catch(e){
    status.className="bad";
    status.textContent=e.message;
  }
};

$("load").onclick = async ()=>{
  $("error").textContent="";
  try{await post("/api/load");}catch(e){$("error").textContent=e.message;}
};
$("all").onclick = ()=>document.querySelectorAll(".roomCheck").forEach(x=>x.checked=true);
$("none").onclick = ()=>document.querySelectorAll(".roomCheck").forEach(x=>x.checked=false);
$("sync").onclick = async ()=>{
  $("error").textContent="";
  const ids=[...document.querySelectorAll(".roomCheck:checked")].map(x=>x.value);
  if(!ids.length){$("error").textContent="至少選擇一個聊天室。";return;}
  try{await post("/api/sync",{room_ids:ids});}catch(e){$("error").textContent=e.message;}
};

$("searchForm").onsubmit = async (ev)=>{
  ev.preventDefault();
  const q=$("query").value.trim();
  if(!q)return;
  const r=await fetch("/api/search?q="+encodeURIComponent(q));
  const data=await r.json();
  if(!r.ok){$("results").innerHTML=`<div class="bad">${esc(data.error)}</div>`;return;}
  $("results").innerHTML=data.results.map(x=>`
    <div class="result">
      <div class="meta">${esc(x.room_name || x.room_id)} · ${esc(x.sender || "-")} · ${new Date(x.created_at_ms).toLocaleString()}</div>
      <div class="body">${esc(x.body)}</div>
      <a href="${esc(x.web_url)}" target="_blank" rel="noopener">開啟留言</a>
    </div>`).join("") || '<div class="muted">沒有搜尋結果。</div>';
};

async function pollOnce(){
  const r=await fetch("/api/state");
  const s=await r.json();
  unlocked=!!s.unlocked;
  $("unlockCard").hidden=unlocked;
  $("app").hidden=!unlocked;
  $("summary").textContent=unlocked ? `${s.message_count} 則已封存留言` : "資料庫已鎖定";

  if(!unlocked){
    const exists=!!s.database_exists;
    $("unlockTitle").textContent=exists ? "解鎖資料庫" : "建立資料庫密碼";
    $("unlockButton").textContent=exists ? "解鎖" : "建立並解鎖";
    $("confirmPassword").hidden=exists;
    $("password").autocomplete="off";
    $("unlockHint").textContent=exists
      ? "輸入密碼以解鎖 SQLCipher 資料庫。"
      : "第一次使用：設定 SQLCipher 資料庫密碼。";
    return;
  }

  $("phase").textContent=s.phase || "";
  $("error").textContent=s.error || "";
  $("load").disabled=!!s.busy;
  $("sync").disabled=!!s.busy || knownRooms.length===0;
  if((s.rooms||[]).length && JSON.stringify(s.rooms)!==JSON.stringify(knownRooms)) renderRooms(s.rooms);
  const logs=s.log||[];
  if(logs.length!==lastLogSize){
    $("log").textContent=logs.join("\n");
    $("log").scrollTop=$("log").scrollHeight;
    lastLogSize=logs.length;
  }
}

async function poll(){
  try{await pollOnce();}catch(e){}
  setTimeout(poll,1000);
}
poll();
</script>
</body>
</html>
"""


class UIState:
    def __init__(
        self,
        db_path: Path,
        endpoint: str,
        target_filter: str,
        auth_timeout: float,
        page_limit: int,
    ) -> None:
        self.db_path = db_path
        self.endpoint = endpoint
        self.target_filter = target_filter
        self.auth_timeout = auth_timeout
        self.page_limit = page_limit
        self.lock = threading.Lock()
        self.database_lock = threading.Lock()
        self.busy = False
        self.phase = "Ready"
        self.error: str | None = None
        self.log: list[str] = []
        self.database_password: str | None = None
        self.auth: MatrixAuth | None = None
        self.rooms = []
        self.room_payloads: dict[str, dict[str, Any]] = {}

    def progress(self, message: str) -> None:
        with self.lock:
            self.log.append(message)
            if len(self.log) > 500:
                self.log = self.log[-500:]
            self.phase = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            database_password = self.database_password
            if database_password is None:
                return {
                    "unlocked": False,
                    "database_exists": self.db_path.is_file(),
                    "busy": False,
                    "phase": "Locked",
                    "error": None,
                    "log": [],
                    "rooms": [],
                    "message_count": 0,
                }

            rooms = [asdict(room) for room in self.rooms]
            data = {
                "unlocked": True,
                "database_exists": True,
                "busy": self.busy,
                "phase": self.phase,
                "error": self.error,
                "log": list(self.log),
                "rooms": rooms,
            }
        data["message_count"] = self.message_count(database_password)
        return data

    def message_count(self, database_password: str | None = None) -> int:
        if database_password is None:
            with self.lock:
                database_password = self.database_password
        if database_password is None:
            raise RuntimeError("Database is locked.")

        with self.database_lock:
            connection = connect(self.db_path, database_password)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM messages"
                ).fetchone()
                return int(row["count"])
            finally:
                connection.close()

    def unlock(self, password: str, confirm_password: str = "") -> None:
        if not password:
            raise RuntimeError("Database password must not be empty.")
        if not self.db_path.exists() and password != confirm_password:
            raise RuntimeError("Database passwords do not match.")

        connection = init_db(self.db_path, password)
        connection.close()
        with self.lock:
            self.database_password = password
            self.phase = "Ready"
            self.error = None

    def change_password(
        self,
        current_password: str,
        new_password: str,
        confirm_password: str,
    ) -> None:
        if new_password != confirm_password:
            raise RuntimeError("New database passwords do not match.")
        if not new_password:
            raise RuntimeError("New database password must not be empty.")

        with self.lock:
            if self.busy:
                raise RuntimeError(
                    "Cannot change the database password while an operation is running."
                )
            if self.database_password is None:
                raise RuntimeError("Database is locked.")
            if current_password != self.database_password:
                raise RuntimeError("Incorrect current database password.")
            self.busy = True
            self.phase = "Changing database password..."

        try:
            with self.database_lock:
                change_database_password(
                    self.db_path,
                    current_password,
                    new_password,
                )
        finally:
            with self.lock:
                self.busy = False

        with self.lock:
            self.database_password = new_password
            self.phase = "Ready"

    def require_database_password(self) -> str:
        with self.lock:
            if self.database_password is None:
                raise RuntimeError("Database is locked.")
            return self.database_password

    def start_load(self) -> None:
        self.require_database_password()
        with self.lock:
            if self.busy:
                raise RuntimeError("Another operation is already running.")
            self.busy = True
            self.error = None
            self.log = []
            self.phase = "Loading rooms..."
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        try:
            auth, rooms, payloads = prepare_rooms(
                endpoint=self.endpoint,
                target_filter=self.target_filter,
                auth_timeout=self.auth_timeout,
                progress=self.progress,
            )
            with self.lock:
                self.auth = auth
                self.rooms = rooms
                self.room_payloads = payloads
                self.phase = f"Loaded {len(rooms)} room(s)."
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.phase = "Load failed"
        finally:
            with self.lock:
                self.busy = False

    def start_sync(self, room_ids: set[str]) -> None:
        database_password = self.require_database_password()
        with self.lock:
            if self.busy:
                raise RuntimeError("Another operation is already running.")
            if self.auth is None or not self.rooms:
                raise RuntimeError("請先載入聊天室。")
            valid = {room.room_id for room in self.rooms}
            selected = room_ids & valid
            if not selected:
                raise RuntimeError("No valid rooms selected.")
            auth = self.auth
            rooms = list(self.rooms)
            payloads = dict(self.room_payloads)
            self.busy = True
            self.error = None
            self.log = []
            self.phase = "Starting sync..."
        threading.Thread(
            target=self._sync_worker,
            args=(database_password, auth, rooms, payloads, selected),
            daemon=True,
        ).start()

    def _sync_worker(
        self,
        database_password: str,
        auth: MatrixAuth,
        rooms,
        payloads: dict[str, dict[str, Any]],
        selected: set[str],
    ) -> None:
        try:
            room_count, message_count = sync_prepared(
                db_path=str(self.db_path),
                database_password=database_password,
                auth=auth,
                available_rooms=rooms,
                room_payloads=payloads,
                selected_room_ids=selected,
                page_limit=self.page_limit,
                progress=self.progress,
            )
            with self.lock:
                self.phase = (
                    f"Done: {room_count} room(s), "
                    f"{message_count} new message(s)."
                )
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.phase = "Sync failed"
        finally:
            with self.lock:
                self.busy = False


def _json(handler: BaseHTTPRequestHandler, status: int, data: Any) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "reddex"

    @property
    def state(self) -> UIState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/state":
            _json(self, HTTPStatus.OK, self.state.snapshot())
            return

        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not query:
                _json(self, HTTPStatus.BAD_REQUEST, {"error": "Missing query."})
                return
            try:
                database_password = self.state.require_database_password()
            except RuntimeError as exc:
                _json(
                    self,
                    HTTPStatus.LOCKED,
                    {"error": str(exc)},
                )
                return
            with self.state.database_lock:
                connection = connect(self.state.db_path, database_password)
                try:
                    rows = smart_search_messages(connection, query, 100)
                    results = [
                        {
                            "event_id": row["event_id"],
                            "room_id": row["room_id"],
                            "room_name": row["room_name"],
                            "sender": row["sender"],
                            "created_at_ms": row["created_at_ms"],
                            "body": row["body"],
                            "web_url": row["web_url"],
                        }
                        for row in rows
                    ]
                except DatabaseError as exc:
                    _json(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"Search error: {exc}"},
                    )
                    return
                finally:
                    connection.close()
            _json(self, HTTPStatus.OK, {"results": results})
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _json(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
            return

        try:
            if parsed.path == "/api/unlock":
                password = payload.get("password", "")
                confirm_password = payload.get("confirm_password", "")
                if not isinstance(password, str) or not isinstance(
                    confirm_password, str
                ):
                    raise RuntimeError("Invalid password.")
                self.state.unlock(password, confirm_password)
                _json(self, HTTPStatus.OK, {"ok": True})
                return

            if parsed.path == "/api/passwd":
                current_password = payload.get("current_password", "")
                new_password = payload.get("new_password", "")
                confirm_password = payload.get("confirm_password", "")
                if not all(
                    isinstance(value, str)
                    for value in (
                        current_password,
                        new_password,
                        confirm_password,
                    )
                ):
                    raise RuntimeError("Invalid password.")
                self.state.change_password(
                    current_password,
                    new_password,
                    confirm_password,
                )
                _json(self, HTTPStatus.OK, {"ok": True})
                return

            if parsed.path == "/api/load":
                self.state.start_load()
                _json(self, HTTPStatus.ACCEPTED, {"ok": True})
                return

            if parsed.path == "/api/sync":
                values = payload.get("room_ids", [])
                if not isinstance(values, list):
                    raise RuntimeError("room_ids must be a list.")
                room_ids = {
                    value for value in values if isinstance(value, str)
                }
                self.state.start_sync(room_ids)
                _json(self, HTTPStatus.ACCEPTED, {"ok": True})
                return
        except RuntimeError as exc:
            _json(self, HTTPStatus.CONFLICT, {"error": str(exc)})
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def open_ui_url(url: str) -> bool:
    termux_open_url = shutil.which("termux-open-url")
    if termux_open_url:
        try:
            subprocess.Popen(
                [termux_open_url, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


class Server(ThreadingHTTPServer):
    def __init__(self, address, state: UIState) -> None:
        super().__init__(address, Handler)
        self.state = state

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(
            exc,
            (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
        ):
            return
        super().handle_error(request, client_address)


def run_ui(
    db_path: str | Path = "data/reddex.db",
    endpoint: str = "http://127.0.0.1:9222",
    target_filter: str = "reddit",
    auth_timeout: float = 30.0,
    page_limit: int = 100,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    state = UIState(
        db_path=Path(db_path),
        endpoint=endpoint,
        target_filter=target_filter,
        auth_timeout=auth_timeout,
        page_limit=page_limit,
    )
    server = Server((host, port), state)
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{port}"
    print(f"reddex UI: {url}")
    if not open_ui_url(url):
        print("Could not open the browser automatically; open the URL above.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
