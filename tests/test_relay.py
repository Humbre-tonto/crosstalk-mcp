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


def test_side_is_optional_not_guessed(db_isolation):
    """With no explicit side, presence side is None (no more X/Y name-guessing)."""
    crosstalk_mcp._online_participants["opt_ch"] = {}
    for name in ["agentY", "agent-b", "claude", "gpt-worker"]:
        crosstalk_mcp._register_agent_presence("opt_ch", name)
        assert crosstalk_mcp._online_participants["opt_ch"][name]["side"] is None


def test_side_explicit_free_form(db_isolation):
    """An explicit side/role is stored verbatim (any string), via post or direct registration."""
    crosstalk_mcp._online_participants["role_ch"] = {}
    crosstalk_mcp._post("role_ch", "agent-a", "NOTE", "hello", side="backend")
    assert crosstalk_mcp._online_participants["role_ch"]["agent-a"]["side"] == "backend"
    crosstalk_mcp._register_agent_presence("role_ch", "claude", side="Y")
    assert crosstalk_mcp._online_participants["role_ch"]["claude"]["side"] == "Y"


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


# ----- per-participant auth -----
def test_parse_participant_tokens():
    """RELAY_PARTICIPANTS parsing: token->id map, skipping blanks/malformed."""
    parsed = crosstalk_mcp._parse_participant_tokens("humanX:tokX, humanY:tokY ,,bad,agentA:tokA")
    assert parsed == {"tokX": "humanX", "tokY": "humanY", "tokA": "agentA"}
    assert crosstalk_mcp._parse_participant_tokens("") == {}


def _auth_client(participant_tokens, shared_token=None):
    """TestClient over the REST app wrapped in the bearer middleware. Used WITHOUT a lifespan
    context (`with`) on purpose: these tests exercise only the /api routes + middleware, and the
    FastMCP streamable-HTTP session manager may only be run once per process (claimed by an
    earlier lifespan test), so entering another lifespan here would raise."""
    app = crosstalk_mcp.mcp.streamable_http_app()
    app = crosstalk_mcp._BearerTokenMiddleware(
        app, shared_token=shared_token, participant_tokens=participant_tokens
    )
    return TestClient(app)


def test_per_participant_bound_token_allows_matching_sender(db_isolation):
    """A bound participant token may post as its own identity (200)."""
    client = _auth_client({"tokX": "humanX", "tokY": "humanY"})
    resp = client.post(
        "/api/channels/authch/messages",
        json={"sender": "humanX", "type": "NOTE", "body": "hi"},
        headers={"Authorization": "Bearer tokX"},
    )
    assert resp.status_code == 200
    assert resp.json()["sender"] == "humanX"


def test_per_participant_bound_token_rejects_impersonation(db_isolation):
    """humanX's token cannot post as humanY (403) - the core anti-impersonation guarantee."""
    client = _auth_client({"tokX": "humanX", "tokY": "humanY"})
    resp = client.post(
        "/api/channels/authch/messages",
        json={"sender": "humanY", "type": "NOTE", "body": "spoof"},
        headers={"Authorization": "Bearer tokX"},
    )
    assert resp.status_code == 403


def test_per_participant_unknown_token_unauthorized(db_isolation):
    """An unrecognized token is rejected before reaching a handler (401)."""
    client = _auth_client({"tokX": "humanX"})
    resp = client.post(
        "/api/channels/authch/messages",
        json={"sender": "humanX", "type": "NOTE", "body": "hi"},
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


def test_per_participant_token_via_query_param(db_isolation):
    """Query-param token (the SSE path, since EventSource can't set headers) also authorizes."""
    client = _auth_client({"tokX": "humanX"})
    assert client.get("/api/channels?token=tokX").status_code == 200
    assert client.get("/api/channels?token=bad").status_code == 401


def test_shared_token_is_unbound_privileged(db_isolation):
    """With both configured, the shared token authenticates but is NOT bound to an identity,
    so it may post as any sender (backward compatible for agents/services)."""
    client = _auth_client({"tokX": "humanX"}, shared_token="admintok")
    resp = client.post(
        "/api/channels/authch/messages",
        json={"sender": "anyone-at-all", "type": "NOTE", "body": "hi"},
        headers={"Authorization": "Bearer admintok"},
    )
    assert resp.status_code == 200


# ----- C1: N participants per channel -----
def test_n_participants_register(db_isolation, monkeypatch):
    """A channel holds many participants (not just two sides)."""
    monkeypatch.setattr(crosstalk_mcp, "RELAY_MAX_PARTICIPANTS", 0)  # unlimited
    crosstalk_mcp._online_participants["big"] = {}
    for a in ["agent-1", "agent-2", "agent-3", "agent-4", "agent-5"]:
        crosstalk_mcp._register_agent_presence("big", a)
    assert len(crosstalk_mcp._online_participants["big"]) == 5


def test_participant_cap_enforced(db_isolation, monkeypatch):
    """_join_denied blocks a NEW participant once the cap is reached; re-join is allowed."""
    monkeypatch.setattr(crosstalk_mcp, "RELAY_MAX_PARTICIPANTS", 3)
    crosstalk_mcp._online_participants["capped"] = {}
    for a in ["agent-1", "agent-2", "agent-3"]:
        crosstalk_mcp._register_agent_presence("capped", a)
    # a 4th distinct participant is denied
    denied = crosstalk_mcp._join_denied("capped", "agent-4")
    assert denied is not None and denied["error"] == "channel_full" and denied["limit"] == 3
    # an already-present participant is never denied (heartbeat / re-join)
    assert crosstalk_mcp._join_denied("capped", "agent-1") is None


def test_participant_cap_unlimited_when_zero(db_isolation, monkeypatch):
    monkeypatch.setattr(crosstalk_mcp, "RELAY_MAX_PARTICIPANTS", 0)
    crosstalk_mcp._online_participants["free"] = {"a": {"last_seen": time.time()}, "b": {"last_seen": time.time()}}
    assert crosstalk_mcp._join_denied("free", "z") is None


def test_group_addressing_any_agent_and_side(db_isolation, monkeypatch):
    """Directives resolve group tokens: any-agent, side:<role>, all — for N participants."""
    monkeypatch.setattr(crosstalk_mcp, "RELAY_MAX_PARTICIPANTS", 0)
    ch = "groups"
    crosstalk_mcp._online_participants[ch] = {}
    # a human asks all agents; another message targets a side/role; one is a broadcast
    crosstalk_mcp._post(ch, "humanX", "DIRECTIVE", "all agents pause", recipient="any-agent")
    crosstalk_mcp._post(ch, "humanX", "DIRECTIVE", "backend only", recipient="side:backend")
    crosstalk_mcp._post(ch, "humanX", "DIRECTIVE", "everyone", recipient="all")
    crosstalk_mcp._post(ch, "humanX", "INTERRUPT", "humanY only", recipient="humanY")

    # agent-7 on the "backend" side: gets any-agent + side:backend + all, but not the humanY one
    crosstalk_mcp._register_agent_presence(ch, "agent-7", side="backend")
    got = {m["body"] for m in crosstalk_mcp._get_directives(ch, "agent-7")}
    assert got == {"all agents pause", "backend only", "everyone"}

    # agent-9 on the "frontend" side: any-agent + all, but NOT the backend-only one
    crosstalk_mcp._register_agent_presence(ch, "agent-9", side="frontend")
    got9 = {m["body"] for m in crosstalk_mcp._get_directives(ch, "agent-9")}
    assert got9 == {"all agents pause", "everyone"}


def test_session_done_quorum_three_speakers(db_isolation):
    """With 3 speakers, the session auto-stops only when all three have posted DONE."""
    ch = "trio"
    crosstalk_mcp._start_session(ch)
    crosstalk_mcp._post(ch, "agent-a", "DONE", "done")
    crosstalk_mcp._post(ch, "agent-b", "NOTE", "still working")
    crosstalk_mcp._post(ch, "agent-c", "DONE", "done")
    assert crosstalk_mcp._get_session(ch) is not None       # agent-b hasn't finished
    crosstalk_mcp._post(ch, "agent-b", "DONE", "done now")
    assert crosstalk_mcp._get_session(ch) is None            # all three DONE -> stopped


def test_session_min_done_override(db_isolation):
    """min_done sets an explicit DONE quorum regardless of speaker count."""
    ch = "quorum2"
    crosstalk_mcp._start_session(ch, min_done=2)
    crosstalk_mcp._post(ch, "agent-a", "NOTE", "hi")
    crosstalk_mcp._post(ch, "agent-b", "DONE", "d")
    assert crosstalk_mcp._get_session(ch) is not None        # only 1 DONE
    crosstalk_mcp._post(ch, "agent-c", "DONE", "d")
    assert crosstalk_mcp._get_session(ch) is None            # 2 DONE -> quorum met
