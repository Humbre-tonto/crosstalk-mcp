# crosstalk-mcp

[![build (java)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml)
[![ci (python)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> A tiny **cross-machine relay MCP server** — a shared mailbox so two coding agents can talk, even on different machines.

Most agent-to-agent messaging tools are **single-machine** (a shared file or SQLite db on one
box). `crosstalk-mcp` speaks **streamable-HTTP MCP over the network**, so the two agents can live
on **different machines** — same LAN, a VPN, or a tunnel. One side hosts the relay; both sides
add it with `claude mcp add --transport http` and start dropping messages in a shared channel.

```
   Machine A                          Machine B
  ┌──────────┐                       ┌──────────┐
  │  agent A │                       │  agent B │
  └────┬─────┘                       └────┬─────┘
       │   post_message / get_messages    │
       └───────────────┬──────────────────┘
                       ▼
            ┌────────────────────┐
            │     crosstalk-mcp   │   channel mailbox (SQLite)
            │     /mcp  +  /api    │
            └────────────────────┘
```

## Why

- **Cross-machine.** Two laptops, two coworkers, two clouds — not just two terminals on one box.
- **Drop-in MCP.** Works with any MCP client via `claude mcp add --transport http`.
- **Tiny & durable.** One small service, SQLite-backed, survives restarts.
- **Two editions, same contract.** Pick **Java** or **Python** — identical tools and wire format.
- **Optional auth.** A shared bearer token when you go beyond `localhost`.

## Pick your edition

| Edition | Folder | Stack | Run it with |
|---------|--------|-------|-------------|
| **Java** | [`java/`](java) | Spring Boot 3.5 / Spring AI · JDK 17 · Swagger UI | `mvn package` → `java -jar`, or Docker |
| **Python** | [`python/`](python) | FastMCP · Python 3.10+ | `pip install .` → `python crosstalk_mcp.py`, or Docker |

Both expose the same thing:
- **MCP** (for agents): streamable HTTP at `POST /mcp`.
- **REST mirror** (for humans/tools): under `/api` — the Java edition also serves Swagger UI at `/swagger-ui.html`.
- **SQLite** storage, durable across restarts.
- **Optional** shared bearer token (`RELAY_TOKEN`), off by default.

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `post_message` | `channel, sender, type, body` | `{ id, channel, created_at }` |
| `get_messages` | `channel, since_id` (0 = all) | messages with `id > since_id` |
| `list_channels` | — | channels with counts + last activity |

Pick any `channel` name; both sides use the same one. `type` is a free-text label
(`NOTE`, `QUESTION`, `ANSWER`, `DONE`, …) you choose for your workflow.

## Install & run

Pick one. All listen on port `8765`; set `RELAY_TOKEN` to require auth.

**Python (PyPI):**
```bash
uvx crosstalk-mcp                      # zero-install, or:
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
PORT=8765 RELAY_TOKEN=secret java -jar crosstalk-mcp-1.0.0.jar
```

Building from source instead? See [java/](java) · [python/](python).

## Connect your agents (on each machine)

```bash
claude mcp add --transport http crosstalk http://<HOST>:8765/mcp \
  --header "Authorization: Bearer <your-token>"
```
(Drop `--header` if you're running without `RELAY_TOKEN`.)

## How two agents converse

1. Agent A: `post_message(channel, "agent-a", "QUESTION", "...")`.
2. Agent B polls `get_messages(channel, since_id)` (track the highest id seen) and replies.
3. Repeat until both post a `DONE`. Drive it turn-by-turn ("check the relay and reply") or let each side poll on a loop.

## Security

- The relay moves data between machines. **Set `RELAY_TOKEN`** for anything beyond localhost,
  and put it behind HTTPS (reverse proxy / tunnel) when exposed publicly.
- Treat a channel as a shared bus: **don't post credentials, secrets, or PII.**

## License

[MIT](LICENSE)
