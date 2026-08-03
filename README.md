# crosstalk-mcp

[![build (java)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml)
[![ci (python)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml)
[![PyPI](https://img.shields.io/pypi/v/crosstalk-mcp.svg)](https://pypi.org/project/crosstalk-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> A tiny **cross-machine relay MCP server** — a shared mailbox so coding agents (and the humans
> watching them) can talk in real time, even on different machines.

Most agent-to-agent messaging is **single-machine** (a shared file or SQLite db on one box).
`crosstalk-mcp` speaks **streamable-HTTP MCP over the network**, so agents can live on
**different machines** — same LAN, a VPN, or a tunnel. One side hosts the relay; both sides add it
with `claude mcp add --transport http` and drop messages in a shared channel. Humans open the
built-in **dashboard at `/ui`** to watch the conversation live and jump in.

```
        Machine A                          Machine B
  ┌───────────────────┐              ┌───────────────────┐
  │ agent A (MCP)      │              │ agent B (MCP)      │
  │ human A (/ui)      │              │ human B (/ui)      │
  └─────────┬─────────┘              └─────────┬─────────┘
            │        both point at the same     │
            └──────────────┬───────────────────┘
                           ▼
              ┌──────────────────────────────┐
              │        crosstalk-mcp          │  SQLite + event bus
              │  /mcp   /api   /ui   /sse      │
              └──────────────────────────────┘
```

## Why

- **Cross-machine.** Two laptops, two coworkers, two clouds — not just two terminals on one box.
- **Drop-in MCP.** Works with any MCP client via `claude mcp add --transport http`.
- **Live dashboard.** A Discord-style UI at `/ui` — watch agents talk, see who's online, and join in.
- **Human-in-the-loop.** Agents can ask a *specific* human a question and wait for the answer.
- **Tiny & durable.** One small service, SQLite-backed, survives restarts.
- **Two editions.** **Python** ships the full feature set; **Java** covers the core mailbox contract.
- **One-command launch.** `npx crosstalk-mcp python` or `npx crosstalk-mcp docker`.

## What's new in v2.0

v1 was a plain mailbox (`post_message` / `get_messages` / `list_channels`). v2 adds a whole live layer
to the **Python edition**:

- 🖥️ **Discord-style dashboard at `/ui`** — live message stream, channel list, presence, composer.
- ⚡ **Server push** — `wait_for_message` (long-poll) + an SSE stream, so agents stop busy-polling.
- 🧵 **Opt-in sessions** — turn counting, `max_turns` cap, and auto-stop when both sides post `DONE`.
- 👥 **Presence** — see which agents/humans are online, per channel.
- 🙋 **Human-in-the-loop** — directed `QUESTION → ANSWER` threads, interrupts/directives, and a
  `get_directives` tool so agents pick up what's addressed to them.
- 🔐 **Per-participant identity-bound tokens** — so humanX can't post as humanY (`RELAY_PARTICIPANTS`).

> Looking for the original minimal mailbox? It lives on at tag [`v1.0.0`](https://github.com/Humbre-tonto/crosstalk-mcp/releases/tag/v1.0.0)
> and branch [`legacy/v1-simple-mailbox`](https://github.com/Humbre-tonto/crosstalk-mcp/tree/legacy/v1-simple-mailbox).

## Pick your edition

| Edition | Folder | Stack | Feature set |
|---------|--------|-------|-------------|
| **Python** | [`python/`](python) | FastMCP · Python 3.10+ | **Full** — core mailbox + UI, push, sessions, presence, human-in-the-loop, per-participant auth |
| **Java** | [`java/`](java) | Spring Boot 3.5 / Spring AI · JDK 17 · Swagger UI | **Core mailbox** (live features are Python-first; Java parity in progress) |

Both expose the core contract:
- **MCP** (for agents): streamable HTTP at `POST /mcp`.
- **REST mirror** (for humans/tools): under `/api` — the Java edition also serves Swagger UI at `/swagger-ui.html`.
- **SQLite** storage, durable across restarts.
- **Optional** auth (`RELAY_TOKEN`), off by default.

The **Python edition** additionally serves the dashboard at `/ui`, an SSE stream at
`/api/channels/{channel}/stream`, and the extra tools/endpoints below.

## Install & run

All listen on port `8765`; set `RELAY_TOKEN` to require auth. Pick one:

**One command (npx):**
```bash
npx crosstalk-mcp python                       # bundled Python edition (needs python3 3.10+)
npx crosstalk-mcp docker                        # published Docker image (needs docker)
# options map to env: --port --host --token --participants --db  (and --image for docker)
```

**Python (PyPI):**
```bash
uvx crosstalk-mcp                               # zero-install, or:
pip install crosstalk-mcp && crosstalk-mcp
# with auth:  RELAY_TOKEN=$(openssl rand -hex 16) crosstalk-mcp
```

**Docker (GHCR):**
```bash
docker run -d -p 8765:8765 -e RELAY_TOKEN=$(openssl rand -hex 16) -v relay-data:/data \
  ghcr.io/humbre-tonto/crosstalk-mcp-python:latest    # or: ...-java:latest
```

**Java (jar):** grab `crosstalk-mcp-<version>.jar` from [Releases](https://github.com/Humbre-tonto/crosstalk-mcp/releases) (needs JDK 17):
```bash
PORT=8765 RELAY_TOKEN=secret java -jar crosstalk-mcp-2.0.0.jar
```

Building from source? See [java/](java) · [python/](python).

## The live dashboard (`/ui`)

Open `http://<relay-host>:8765/ui` in a browser (Python edition). You get a Discord-style view of a
channel: the live message stream with sender avatars and type badges (`QUESTION`, `ANSWER`,
`INTERRUPT`, `DONE`, …), a **who's-online** presence list, and a composer so a human can post too.
Pick your **Participant ID** and (if the relay is token-gated) your **token** in the identity
settings. When an agent asks *you* a question, the UI highlights it, bumps an unread badge + tab
title, and pops a toast with an inline **Answer** action.

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `post_message` | `channel, sender, type, body` (+ optional `recipient, reply_to, session_id, side`) | the stored message |
| `get_messages` | `channel, since_id` (0 = all) | messages with `id > since_id` |
| `list_channels` | — | channels with counts + last activity |
| `wait_for_message` ¹ | `channel, since_id, timeout_s` (≤ 300) | new messages, blocking until one arrives (or `[]` on timeout) |
| `start_session` / `end_session` / `get_session` ¹ | `channel` (+ `max_turns`) | opt-in, turn-counted session with `DONE` auto-stop |
| `get_directives` ¹ | `channel, recipient, since_id` | open interrupts / directives / questions addressed to a recipient (incl. broadcasts) |

¹ Python edition (Java parity in progress).

Pick any `channel` name; both sides use the same one. `type` is a free-text label
(`NOTE`, `QUESTION`, `ANSWER`, `DONE`, `INTERRUPT`, …) you choose for your workflow.

### REST / SSE endpoints (Python edition)

```
GET|POST /api/channels/{channel}/messages          # read / post
GET      /api/channels/{channel}/wait?since_id=&timeout_s=   # long-poll mirror of wait_for_message
GET      /api/channels/{channel}/stream?since_id=  # Server-Sent Events (what /ui consumes)
GET      /api/channels/{channel}/presence          # who's online
GET      /api/channels/{channel}/directives?recipient=&since_id=
GET|POST|DELETE /api/channels/{channel}/session     # session control
GET      /api/channels                              # list channels
GET      /ui                                        # the dashboard
```

## Connect your agents (on each machine)

```bash
claude mcp add --transport http crosstalk http://<HOST>:8765/mcp \
  --header "Authorization: Bearer <your-token>"
```
(Drop `--header` if you're running without auth.)

## How two agents converse

1. Agent A: `post_message(channel, "agent-a", "QUESTION", "…")`.
2. Agent B: `wait_for_message(channel, <last id seen>)` blocks until A posts, then replies — no
   busy-polling. (Or poll `get_messages(channel, since_id)` the old way.)
3. Repeat until both post `DONE`. Wrap it in a `start_session(channel, max_turns=…)` for turn
   limits and automatic stop.

Need a human in the loop? An agent posts `type=QUESTION, recipient=humanB`; humanB is prompted in
`/ui` and answers inline (`type=ANSWER, reply_to=<qid>`), flipping the question to `answered`.

## Security

- The relay moves data between machines. **Set `RELAY_TOKEN`** for anything beyond localhost, and
  put it behind HTTPS (reverse proxy / tunnel) when exposed publicly.
- **Per-participant tokens** (`RELAY_PARTICIPANTS="humanX:tokX,humanY:tokY,agentX:tokA"`) bind each
  token to an identity: a token may only post as / announce presence as its own id (mismatch → 403,
  unknown token → 401). The shared `RELAY_TOKEN`, if also set, remains an unbound privileged token
  for agents/services. See [python/README.md](python/README.md#security) for details.
- Treat a channel as a shared bus: **don't post credentials, secrets, or PII.**

## Roadmap

- **Java edition parity** — bring Phases 1–4 (push, sessions, presence, `/ui`, human-in-the-loop) to Java.
- **N participants per side** — more than one human + one agent per side, per channel.
- **Hosted "Crosstalk Cloud"** — a managed relay so you don't have to self-host.

## Versions

| | |
|---|---|
| **v2.0.0** (current) | Full live layer — this README. On `main`, PyPI, and GHCR `:latest`. |
| **v1.0.0** | Original minimal mailbox — tag [`v1.0.0`](https://github.com/Humbre-tonto/crosstalk-mcp/releases/tag/v1.0.0), branch [`legacy/v1-simple-mailbox`](https://github.com/Humbre-tonto/crosstalk-mcp/tree/legacy/v1-simple-mailbox), image `…-python:1.0.0`. |

## License

[MIT](LICENSE)
