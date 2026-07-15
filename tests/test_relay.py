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


def test_session_turn_counting_and_limits(db_isolation):
    """Test start_session, turn counting, max_turns limit and end_session."""
    # Initially no session
    assert crosstalk_mcp._get_session("test_sess_ch") is None

    # Start session with max_turns = 2
    sess_info = crosstalk_mcp._start_session("test_sess_ch", max_turns=2)
    assert sess_info["status"] == "active"
    assert sess_info["max_turns"] == 2

    active = crosstalk_mcp._get_session("test_sess_ch")
    assert active is not None
    assert active["turn_count"] == 0

    # Post message 1 -> turn count = 1
    crosstalk_mcp._post("test_sess_ch", "agent-a", "NOTE", "msg1")
    active = crosstalk_mcp._get_session("test_sess_ch")
    assert active is not None
    assert active["turn_count"] == 1

    # Post message 2 -> turn count = 2 -> auto-stop triggers since max_turns = 2
    crosstalk_mcp._post("test_sess_ch", "agent-b", "NOTE", "msg2")
    assert crosstalk_mcp._get_session("test_sess_ch") is None


def test_session_done_auto_stop(db_isolation):
    """Test session auto-stops when both sides post 'DONE'."""
    crosstalk_mcp._start_session("done_ch")

    # Post DONE from side 1
    crosstalk_mcp._post("done_ch", "agent-a", "DONE", "finished")
    assert crosstalk_mcp._get_session("done_ch") is not None

    # Post DONE from side 2 -> auto-stop triggers because 2 distinct senders posted DONE
    crosstalk_mcp._post("done_ch", "agent-b", "DONE", "finished")
    assert crosstalk_mcp._get_session("done_ch") is None


def test_directed_qa_status_updates(db_isolation):
    """Test directed questions start with status='open' and change to 'answered' when replied with ANSWER."""
    # Post a QUESTION -> should default status to 'open'
    q = crosstalk_mcp._post("qa_ch", "agent-a", "QUESTION", "What is 1+1?", recipient="humanX")
    assert q["status"] == "open"
    qid = q["id"]

    # Retrieve and verify from db
    msgs = crosstalk_mcp._get("qa_ch")
    assert msgs[0]["status"] == "open"
    assert msgs[0]["recipient"] == "humanX"

    # Post an ANSWER replying to the question ID
    ans = crosstalk_mcp._post("qa_ch", "humanX", "ANSWER", "It is 2", reply_to=qid)
    assert ans["reply_to"] == qid

    # Retrieve original question and verify status is now 'answered'
    msgs = crosstalk_mcp._get("qa_ch")
    question_msg = next(m for m in msgs if m["id"] == qid)
    assert question_msg["status"] == "answered"


def test_presence_sse_registration_and_pruning(db_isolation):
    """Test registering presence via SSE parameters and pruning inactive agents."""
    # Initially presence is empty
    participants = list(crosstalk_mcp._online_participants.get("pres_ch", {}).values())
    assert len(participants) == 0

    # Simulate agent polling/posting to register presence
    crosstalk_mcp._register_agent_presence("pres_ch", "agent-x")
    participants = list(crosstalk_mcp._online_participants.get("pres_ch", {}).values())
    assert len(participants) == 1
    assert participants[0]["id"] == "agent-x"
    assert participants[0]["kind"] == "agent"

    # Fast forward time to test agent pruning (agent is inactive after 60s)
    crosstalk_mcp._online_participants["pres_ch"]["agent-x"]["last_seen"] = time.time() - 70.0
    crosstalk_mcp._prune_old_participants("pres_ch")
    participants = list(crosstalk_mcp._online_participants.get("pres_ch", {}).values())
    assert len(participants) == 0


def test_side_classification_heuristic(db_isolation):
    """Verify default name-heuristic side classification for agents."""
    # Reset online participants to avoid side interference from previous tests
    crosstalk_mcp._online_participants["heuristic_ch"] = {}

    test_cases = {
        "agentY": "Y",
        "agent-b": "Y",
        "agentX": "X",
        "agent-a": "X",
        "claude": "X",
    }
    for agent_name, expected_side in test_cases.items():
        crosstalk_mcp._register_agent_presence("heuristic_ch", agent_name)
        participant = crosstalk_mcp._online_participants["heuristic_ch"][agent_name]
        assert participant["side"] == expected_side


def test_side_classification_explicit_override(db_isolation):
    """Verify that an explicit side overrides any name-heuristic defaults."""
    crosstalk_mcp._online_participants["explicit_ch"] = {}

    # agent-a would heuristically be "X", but we explicitly pass "Y"
    crosstalk_mcp._post("explicit_ch", "agent-a", "NOTE", "hello", side="Y")
    participant = crosstalk_mcp._online_participants["explicit_ch"]["agent-a"]
    assert participant["side"] == "Y"

    # claude would heuristically be "X", but we explicitly pass "Y" via registration directly
    crosstalk_mcp._register_agent_presence("explicit_ch", "claude", side="Y")
    participant = crosstalk_mcp._online_participants["explicit_ch"]["claude"]
    assert participant["side"] == "Y"


def test_get_directives_filtering(db_isolation):
    """Test get_directives filters only INTERRUPT, DIRECTIVE, and open QUESTIONs, and honors recipient/broadcasts."""
    ch = "directives_ch"
    # 1. Open QUESTION addressed to humanX
    crosstalk_mcp._post(ch, "agent-a", "QUESTION", "Q1?", recipient="humanX")
    # 2. Answered QUESTION addressed to humanX
    q2 = crosstalk_mcp._post(ch, "agent-a", "QUESTION", "Q2?", recipient="humanX")
    crosstalk_mcp._post(ch, "humanX", "ANSWER", "A2", reply_to=q2["id"])
    # 3. INTERRUPT addressed to humanX
    crosstalk_mcp._post(ch, "agent-a", "INTERRUPT", "Stop!", recipient="humanX")
    # 4. DIRECTIVE with no recipient (broadcast)
    crosstalk_mcp._post(ch, "agent-a", "DIRECTIVE", "Broadcast action")
    # 5. NOTE message (should be ignored)
    crosstalk_mcp._post(ch, "agent-a", "NOTE", "regular note", recipient="humanX")
    # 6. QUESTION addressed to humanY (should be ignored when queried for humanX)
    crosstalk_mcp._post(ch, "agent-a", "QUESTION", "Q3?", recipient="humanY")

    # Retrieve directives for humanX
    directives = crosstalk_mcp._get_directives(ch, "humanX")
    assert len(directives) == 3

    # Check bodies/types of the matched ones:
    # Match 1: Q1? (QUESTION, open, recipient="humanX")
    # Match 2: Stop! (INTERRUPT, recipient="humanX")
    # Match 3: Broadcast action (DIRECTIVE, recipient is null/empty)
    matched_bodies = {d["body"] for d in directives}
    assert "Q1?" in matched_bodies
    assert "Stop!" in matched_bodies
    assert "Broadcast action" in matched_bodies
    assert "Q2?" not in matched_bodies
    assert "regular note" not in matched_bodies
    assert "Q3?" not in matched_bodies
