"""
crosstalk-mcp (Python edition).

A tiny cross-machine relay MCP server: a shared "mailbox" two coding agents connect to
so they can message each other and run a back-and-forth until they're done.

Transport: streamable HTTP MCP at /mcp; a small REST mirror under /api.
Auth:      optional shared bearer token via env RELAY_TOKEN (enforced only if set).
Storage:   SQLite (env RELAY_DB, default relay.db), durable across restarts.

Tools / endpoints: post_message, get_messages, list_channels.
Run:  RELAY_TOKEN=secret PORT=8765 python crosstalk_mcp.py
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

RELAY_TOKEN = os.environ.get("RELAY_TOKEN")
DB_PATH = os.environ.get("RELAY_DB", "relay.db")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))

_write_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    """Open a connection and ensure the schema (cheap IF NOT EXISTS) so the relay
    self-heals if the db file is ever deleted/recreated empty while running."""
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
    return c


def _post(channel: str, sender: str, type_: str, body: str) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    with _write_lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO messages(channel,sender,type,body,created_at) VALUES(?,?,?,?,?)",
            (channel, sender, type_, body, ts),
        )
        return {"id": cur.lastrowid, "channel": channel, "created_at": ts}


def _get(channel: str, since_id: int = 0) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,channel,sender,type,body,created_at FROM messages "
            "WHERE channel=? AND id>? ORDER BY id",
            (channel, since_id),
        ).fetchall()
        return [dict(r) for r in rows]


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
def post_message(channel: str, sender: str, type: str, body: str) -> dict:
    """Append a message to a channel mailbox and return its id.

    Treat the channel as a shared, possibly internet-reachable bus - do not post secrets.
    channel: e.g. "my-project"; sender: e.g. "agent-a"; type: free-text label
    (NOTE/QUESTION/ANSWER/DONE...); body: the content.
    """
    return _post(channel, sender, type, body)


@mcp.tool()
def get_messages(channel: str, since_id: int = 0) -> list:
    """Return messages in a channel with id greater than since_id (0 = all).

    Poll for new messages by passing the highest id you have already seen.
    """
    return _get(channel, since_id)


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
    return JSONResponse(_post(channel, data["sender"], data["type"], data["body"]))


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
