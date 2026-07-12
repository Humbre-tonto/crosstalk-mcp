import asyncio
import json
import sqlite3
import threading
import time

import pytest
from starlette.testclient import TestClient

import crosstalk_mcp


@pytest.fixture
def db_isolation(tmp_path, monkeypatch):
    """Isolate DB per test by patching DB_PATH to a unique file."""
    db_path = str(tmp_path / "relay.db")
    monkeypatch.setattr(crosstalk_mcp, "DB_PATH", db_path)
    return db_path


def test_migration(db_isolation):
    """messages table contains session_id column."""
    c = crosstalk_mcp._conn()
    info = c.execute("PRAGMA table_info(messages)").fetchall()
    column_names = [row["name"] for row in info]
    assert "session_id" in column_names
    c.close()


def test_post_then_get(db_isolation):
    """Post message, get it back with correct body and incrementing ids."""
    result1 = crosstalk_mcp._post("test_ch", "alice", "NOTE", "hello")
    assert "id" in result1
    assert result1["channel"] == "test_ch"

    msgs = crosstalk_mcp._get("test_ch")
    assert len(msgs) == 1
    assert msgs[0]["body"] == "hello"
    assert msgs[0]["sender"] == "alice"
    assert msgs[0]["id"] == result1["id"]

    result2 = crosstalk_mcp._post("test_ch", "bob", "REPLY", "world")
    assert result2["id"] > result1["id"]

    msgs = crosstalk_mcp._get("test_ch")
    assert len(msgs) == 2
    assert msgs[0]["id"] < msgs[1]["id"]


def test_get_since_id(db_isolation):
    """Get with since_id only returns id > since_id."""
    r1 = crosstalk_mcp._post("ch1", "a", "T", "msg1")
    r2 = crosstalk_mcp._post("ch1", "b", "T", "msg2")
    r3 = crosstalk_mcp._post("ch1", "c", "T", "msg3")

    msgs = crosstalk_mcp._get("ch1", since_id=r1["id"])
    assert len(msgs) == 2
    assert msgs[0]["id"] == r2["id"]
    assert msgs[1]["id"] == r3["id"]

    msgs = crosstalk_mcp._get("ch1", since_id=r2["id"])
    assert len(msgs) == 1
    assert msgs[0]["id"] == r3["id"]


def test_wait_wakes_on_post(db_isolation):
    """_wait wakes when another thread posts, returns message in <1s."""
    start = time.time()

    def post_after_delay():
        time.sleep(0.2)
        crosstalk_mcp._post("ch2", "sender", "TYPE", "delayed_msg")

    thread = threading.Thread(target=post_after_delay)
    thread.start()

    msgs = crosstalk_mcp._wait("ch2", since_id=0, timeout_s=10.0)
    elapsed = time.time() - start

    thread.join()

    assert len(msgs) == 1
    assert msgs[0]["body"] == "delayed_msg"
    assert elapsed < 1.0


def test_wait_timeout(db_isolation):
    """_wait returns [] after roughly timeout_s when nothing arrives."""
    timeout = 0.5
    start = time.time()
    msgs = crosstalk_mcp._wait("ch3", since_id=0, timeout_s=timeout)
    elapsed = time.time() - start

    assert msgs == []
    assert 0.4 < elapsed < 1.0


def test_channels(db_isolation):
    """_channels() returns counts and last_id per channel."""
    crosstalk_mcp._post("ch_a", "x", "T", "msg1")
    crosstalk_mcp._post("ch_a", "y", "T", "msg2")
    crosstalk_mcp._post("ch_b", "z", "T", "msg3")

    channels = crosstalk_mcp._channels()
    assert len(channels) == 2

    ch_a = next(c for c in channels if c["channel"] == "ch_a")
    ch_b = next(c for c in channels if c["channel"] == "ch_b")

    assert ch_a["count"] == 2
    assert ch_a["last_id"] == 2
    assert ch_b["count"] == 1
    assert ch_b["last_id"] == 3


def test_rest_endpoints(db_isolation):
    """REST: POST returns 200 + id; GET returns it; wait returns [] on timeout."""
    app = crosstalk_mcp.mcp.streamable_http_app()

    with TestClient(app) as client:
        resp = client.post("/api/channels/rest_ch/messages", json={
            "sender": "alice",
            "type": "MSG",
            "body": "test"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data

        resp = client.get("/api/channels/rest_ch/messages")
        assert resp.status_code == 200
        msgs = resp.json()
        assert len(msgs) == 1
        assert msgs[0]["body"] == "test"

        resp = client.get("/api/channels/rest_ch/wait?since_id=999&timeout_s=0.1")
        assert resp.status_code == 200
        msgs = resp.json()
        assert msgs == []


def test_sse_stream(db_isolation):
    """SSE: first chunk 'event: ready'; after post, data chunk contains body."""

    async def _test():
        class FakeRequest:
            def __init__(self, channel, since_id):
                self.path_params = {"channel": channel}
                self.query_params = {"since_id": str(since_id)}

            async def is_disconnected(self):
                return False

        request = FakeRequest("sse_ch", 0)
        response = await crosstalk_mcp.rest_stream(request)

        first_chunk = await asyncio.wait_for(
            response.body_iterator.__anext__(), timeout=3
        )
        assert "event: ready" in first_chunk
        assert "sse_ch" in first_chunk

        crosstalk_mcp._post("sse_ch", "bob", "UPDATE", "hello_sse")

        found = False
        for _ in range(20):
            try:
                chunk = await asyncio.wait_for(
                    response.body_iterator.__anext__(), timeout=2
                )
                if "hello_sse" in chunk:
                    found = True
                    break
            except asyncio.TimeoutError:
                break

        assert found, "Message body not found in SSE stream"

    asyncio.run(_test())
