"""
crosstalk-mcp (Python edition).

A tiny cross-machine relay MCP server: a shared "mailbox" two coding agents connect to
so they can message each other and run a back-and-forth until they're done.

Transport: streamable HTTP MCP at /mcp; a small REST mirror under /api.
Auth:      optional shared bearer token via env RELAY_TOKEN (enforced only if set).
Storage:   SQLite (env RELAY_DB, default relay.db), durable across restarts.

Tools:      post_message, get_messages, wait_for_message, list_channels.
Endpoints:  GET/POST /api/channels/{channel}/messages, GET .../wait (long-poll),
            GET .../stream (SSE), GET /api/channels.
Run:  RELAY_TOKEN=secret PORT=8765 python crosstalk_mcp.py
"""

import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from mcp.server.fastmcp import FastMCP

RELAY_TOKEN = os.environ.get("RELAY_TOKEN")
DB_PATH = os.environ.get("RELAY_DB", "relay.db")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))

_write_lock = threading.Lock()

# ----- in-process event bus (Phase 0) -----
# The relay stays a transport, not a brain: we don't push message payloads through the bus.
# We just wake anyone waiting, and they re-read the DB by cursor (since_id). This keeps every
# consumer (wait_for_message, SSE) reconnect-safe and never lets a wakeup lose a message.
_notify = threading.Condition()

# ----- in-process session state (Phase 1) -----
_sessions: dict[str, dict[str, Any]] = {}

# ----- in-memory presence / online participants state (Phase 3) -----
_online_participants: dict[str, dict[str, dict[str, Any]]] = {}


def _register_agent_presence(channel: str, sender: str) -> None:
    if sender == "human" or sender.startswith("human"):
        return
    if channel in _online_participants and sender in _online_participants[channel]:
        if _online_participants[channel][sender]["kind"] == "human":
            return
    if channel not in _online_participants:
        _online_participants[channel] = {}
    _online_participants[channel][sender] = {
        "id": sender,
        "display_name": sender,
        "kind": "agent",
        "side": "X" if any(x in sender.lower() for x in ["x", "a", "creator"]) else "Y",
        "last_seen": time.time()
    }


def _prune_old_participants(channel: str) -> None:
    if channel not in _online_participants:
        return
    now = time.time()
    to_remove = []
    for pid, p in list(_online_participants[channel].items()):
        if p.get("kind") == "agent" and now - p.get("last_seen", 0) > 60.0:
            to_remove.append(pid)
    for pid in to_remove:
        _online_participants[channel].pop(pid, None)


def _start_session(channel: str, max_turns: int | None = None) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    _sessions[channel] = {
        "session_id": session_id,
        "max_turns": max_turns,
        "turn_count": 0,
        "done_senders": set(),
    }
    return {"channel": channel, "session_id": session_id, "max_turns": max_turns, "status": "active"}


def _end_session(channel: str) -> dict[str, Any]:
    if channel in _sessions:
        info = _sessions.pop(channel)
        return {"channel": channel, "session_id": info["session_id"], "status": "ended"}
    return {"channel": channel, "status": "no_active_session"}


def _get_session(channel: str) -> dict[str, Any] | None:
    if channel in _sessions:
        sess = _sessions[channel]
        return {
            "session_id": sess["session_id"],
            "max_turns": sess["max_turns"],
            "turn_count": sess["turn_count"],
            "status": "active"
        }
    return None


# ----- additive schema migration (Phase 0) -----
# New columns are always nullable and added here, never by rewriting the base table, so old
# relay.db files and the original three-tool contract keep working untouched. Names are fixed
# constants (no user input) -> safe to interpolate into ALTER TABLE.
_EXTRA_COLUMNS: dict[str, str] = {
    "session_id": "TEXT",  # forward-ready for the opt-in Phase 1 session grouping (nullable)
    "recipient": "TEXT",
    "reply_to": "INTEGER",
    "status": "TEXT",
    "side": "TEXT",
}


def _ensure_columns(c: sqlite3.Connection) -> None:
    """Add any missing nullable columns to `messages` (additive, idempotent)."""
    existing = {row["name"] for row in c.execute("PRAGMA table_info(messages)").fetchall()}
    for name, decl in _EXTRA_COLUMNS.items():
        if name not in existing:
            c.execute(f"ALTER TABLE messages ADD COLUMN {name} {decl}")


def _conn() -> sqlite3.Connection:
    """Open a connection and ensure the schema (cheap IF NOT EXISTS + additive columns) so the
    relay self-heals if the db file is ever deleted/recreated empty while running."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            sender TEXT NOT NULL,
            type TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL)"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_id ON messages(channel, id)")
    _ensure_columns(c)
    return c


def _post(
    channel: str,
    sender: str,
    type_: str,
    body: str,
    session_id: str | None = None,
    recipient: str | None = None,
    reply_to: int | None = None,
    status: str | None = None,
    side: str | None = None,
) -> dict[str, Any]:
    _register_agent_presence(channel, sender)
    # Manage session automatically if active
    if channel in _sessions:
        sess = _sessions[channel]
        if not session_id:
            session_id = sess["session_id"]
        sess["turn_count"] += 1

        if type_.upper() == "DONE":
            sess["done_senders"].add(sender)

        # Check for auto-stop conditions
        auto_stop = False
        if len(sess["done_senders"]) >= 2:
            auto_stop = True
        elif sess["max_turns"] is not None and sess["turn_count"] >= sess["max_turns"]:
            auto_stop = True

        if auto_stop:
            # End the session
            _sessions.pop(channel, None)

    ts = datetime.now(timezone.utc).isoformat()
    # Default status to open for questions
    if type_.upper() == "QUESTION" and not status:
        status = "open"

    # If this is an answer replying to a question, update the question's status to "answered"
    if type_.upper() == "ANSWER" and reply_to:
        with _write_lock, _conn() as c:
            c.execute(
                "UPDATE messages SET status='answered' WHERE id=? AND type='QUESTION'",
                (reply_to,),
            )
            c.commit()

    with _write_lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO messages(channel,sender,type,body,created_at,session_id,recipient,reply_to,status,side) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (channel, sender, type_, body, ts, session_id, recipient, reply_to, status, side),
        )
        result = {
            "id": cur.lastrowid,
            "channel": channel,
            "sender": sender,
            "type": type_,
            "body": body,
            "created_at": ts,
            "session_id": session_id,
            "recipient": recipient,
            "reply_to": reply_to,
            "status": status,
            "side": side,
        }
    # Wake any waiters *after* the row is committed, so their next _get sees it.
    with _notify:
        _notify.notify_all()
    return result


def _get(channel: str, since_id: int = 0) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,channel,sender,type,body,created_at,session_id,recipient,reply_to,status,side "
            "FROM messages WHERE channel=? AND id>? ORDER BY id",
            (channel, since_id),
        ).fetchall()
        return [dict(r) for r in rows]


def _wait(channel: str, since_id: int = 0, timeout_s: float = 30.0) -> list[dict[str, Any]]:
    """Block until a message with id > since_id exists, or timeout. Returns the new
    messages (oldest first) or [] on timeout. Correct against lost wakeups: the poster's
    notify can only fire once a waiter is actually waiting on the condition."""
    deadline = time.monotonic() + timeout_s
    with _notify:
        while True:
            msgs = _get(channel, since_id)
            if msgs:
                return msgs
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            _notify.wait(remaining)


def _channels() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT channel, COUNT(*) AS count, MAX(id) AS last_id, MAX(created_at) AS last_at "
            "FROM messages GROUP BY channel ORDER BY last_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


mcp = FastMCP("crosstalk", host=HOST, port=PORT)


# ----- MCP tools -----
@mcp.tool()
def post_message(
    channel: str,
    sender: str,
    type: str,
    body: str,
    session_id: str | None = None,
    recipient: str | None = None,
    reply_to: int | None = None,
    status: str | None = None,
    side: str | None = None,
) -> dict:
    """Append a message to a channel mailbox and return its details.

    Treat the channel as a shared, possibly internet-reachable bus - do not post secrets.
    channel: e.g. "my-project"; sender: e.g. "agent-a"; type: free-text label
    (NOTE/QUESTION/ANSWER/DONE...); body: the content.
    """
    return _post(channel, sender, type, body, session_id, recipient, reply_to, status, side)


@mcp.tool()
def start_session(channel: str, max_turns: int | None = None) -> dict:
    """Start an opt-in session grouping messages on a channel.

    A session enables turn counting and automatic ending of the session when max_turns
    is exceeded, or when both sides (at least 2 distinct senders) post a 'DONE' message.
    """
    return _start_session(channel, max_turns)


@mcp.tool()
def end_session(channel: str) -> dict:
    """Explicitly end the active session on a channel."""
    return _end_session(channel)


@mcp.tool()
def get_session(channel: str) -> dict:
    """Get the active session details for a channel, if any."""
    return _get_session(channel) or {"status": "no_active_session"}


@mcp.tool()
def get_messages(channel: str, since_id: int = 0) -> list:
    """Return messages in a channel with id greater than since_id (0 = all).

    Poll for new messages by passing the highest id you have already seen.
    """
    return _get(channel, since_id)


@mcp.tool()
def wait_for_message(channel: str, since_id: int = 0, timeout_s: float = 30.0) -> list:
    """Block until a message with id greater than since_id appears, then return it.

    This is the "continuous talk" primitive: post your reply, then call
    wait_for_message(channel, <your last id>) to block until the peer answers, and loop -
    no busy-polling. Returns the new messages (oldest first), or [] on timeout so you can
    loop again or stop. timeout_s is capped at 300 seconds.
    """
    timeout_s = max(0.0, min(float(timeout_s), 300.0))
    return _wait(channel, since_id, timeout_s)


@mcp.tool()
def list_channels() -> list:
    """List channels with message count, last id, and last activity timestamp."""
    return _channels()


# ----- REST mirror (for humans/tools without an MCP client) -----
@mcp.custom_route("/api/channels", methods=["GET"])
async def rest_channels(_request: Request) -> JSONResponse:
    return JSONResponse(_channels())


@mcp.custom_route("/api/channels/{channel}/messages", methods=["GET"])
async def rest_get(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    since_id = int(request.query_params.get("since_id", "0"))
    return JSONResponse(_get(channel, since_id))


@mcp.custom_route("/api/channels/{channel}/messages", methods=["POST"])
async def rest_post(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    data = await request.json()
    return JSONResponse(
        _post(
            channel,
            data["sender"],
            data["type"],
            data["body"],
            session_id=data.get("session_id"),
            recipient=data.get("recipient"),
            reply_to=data.get("reply_to"),
            status=data.get("status"),
            side=data.get("side"),
        )
    )


@mcp.custom_route("/api/channels/{channel}/session", methods=["POST"])
async def rest_start_session(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    try:
        data = await request.json()
    except Exception:
        data = {}
    max_turns = data.get("max_turns")
    if max_turns is not None:
        max_turns = int(max_turns)
    return JSONResponse(_start_session(channel, max_turns))


@mcp.custom_route("/api/channels/{channel}/session", methods=["DELETE"])
async def rest_end_session(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    return JSONResponse(_end_session(channel))


@mcp.custom_route("/api/channels/{channel}/session", methods=["GET"])
async def rest_get_session(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    info = _get_session(channel)
    if info:
        return JSONResponse(info)
    return JSONResponse({"status": "no_active_session"}, status_code=404)


@mcp.custom_route("/api/channels/{channel}/wait", methods=["GET"])
async def rest_wait(request: Request) -> JSONResponse:
    """Long-poll mirror of wait_for_message. Blocks (in a worker thread) until a new
    message arrives or timeout_s elapses; returns the new messages or []."""
    channel = request.path_params["channel"]
    since_id = int(request.query_params.get("since_id", "0"))
    timeout_s = max(0.0, min(float(request.query_params.get("timeout_s", "30")), 300.0))
    msgs = await asyncio.to_thread(_wait, channel, since_id, timeout_s)
    return JSONResponse(msgs)


@mcp.custom_route("/api/channels/{channel}/stream", methods=["GET"])
async def rest_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of a channel. Emits each new message as a `data:` event
    (JSON), plus periodic keep-alive comments. Cursor-based via ?since_id= so reconnects
    never miss messages. This is what the live UI consumes."""
    channel = request.path_params["channel"]
    since_id = int(request.query_params.get("since_id", "0"))

    # Track presence from query parameters
    pid = request.query_params.get("participant_id")
    display_name = request.query_params.get("display_name", pid)
    kind = request.query_params.get("kind", "human")
    side = request.query_params.get("side", "")

    if pid:
        if channel not in _online_participants:
            _online_participants[channel] = {}
        _online_participants[channel][pid] = {
            "id": pid,
            "display_name": display_name,
            "kind": kind,
            "side": side,
            "last_seen": time.time()
        }

    async def gen():
        cursor = since_id
        # Announce the current cursor so clients can resync on reconnect.
        yield f"event: ready\ndata: {json.dumps({'channel': channel, 'since_id': cursor})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msgs = await asyncio.to_thread(_get, channel, cursor)
                except Exception:
                    break
                if msgs:
                    for m in msgs:
                        cursor = m["id"]
                        yield f"data: {json.dumps(m)}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)
        finally:
            # Clean up presence when connection is closed
            if pid and channel in _online_participants and pid in _online_participants[channel]:
                _online_participants[channel].pop(pid, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


@mcp.custom_route("/api/channels/{channel}/presence", methods=["GET"])
async def rest_presence(request: Request) -> JSONResponse:
    channel = request.path_params["channel"]
    _prune_old_participants(channel)
    participants = list(_online_participants.get(channel, {}).values())
    return JSONResponse(participants)


@mcp.custom_route("/ui", methods=["GET"])
async def rest_ui(_request: Request) -> Response:
    """Serve the static Discord-style dashboard assets."""
    ui_path = os.path.join(os.path.dirname(__file__), "ui.html")
    try:
        with open(ui_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        html_content = f"<h1>Error loading UI</h1><p>{str(e)}</p>"
    return Response(content=html_content, media_type="text/html")


@mcp.custom_route("/", methods=["GET"])
async def rest_index(_request: Request) -> Response:
    """Redirect home route to /ui."""
    return Response(status_code=307, headers={"Location": "/ui"})


class _BearerTokenMiddleware:
    """Pure-ASGI bearer-token gate (kept out of BaseHTTPMiddleware to not buffer SSE).
    Lifespan and non-HTTP scopes pass straight through."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            if auth != f"Bearer {self.token}":
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main() -> None:
    if not RELAY_TOKEN:
        print(
            "WARNING: RELAY_TOKEN is not set - the relay is OPEN to anyone who can reach it. "
            "Set RELAY_TOKEN to require an Authorization: Bearer <token> header."
        )
    app = mcp.streamable_http_app()  # Starlette app serving MCP at /mcp + the /api routes above
    if RELAY_TOKEN:
        app = _BearerTokenMiddleware(app, RELAY_TOKEN)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
