# crosstalk-mcp

[![build (java)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/build-java.yml)
[![ci (python)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml/badge.svg)](https://github.com/Humbre-tonto/crosstalk-mcp/actions/workflows/ci-python.yml)

A tiny **cross-machine relay MCP server** — a shared "mailbox" two coding agents connect to
so they can message each other and run a back-and-forth until they're done.

Most agent-to-agent messaging tools are **single-machine** (a shared file or SQLite db on one
box). `crosstalk-mcp` instead speaks **streamable-HTTP MCP over the network**, so the two
agents can live on **different machines** — same LAN, a VPN, or a tunnel. One side hosts the
relay; both sides add it with `claude mcp add --transport http`.

```
   Machine A                          Machine B
  ┌──────────┐   post/get_messages   ┌──────────┐
  │ agent A  │ ───────┐     ┌──────── │ agent B  │
  └──────────┘        ▼     ▼         └──────────┘
                 ┌──────────────────┐
                 │   crosstalk-mcp   │  channel mailbox (SQLite)
                 │   /mcp  +  /api   │
                 └──────────────────┘
```

## Pick your edition

Two interchangeable implementations live in this repo — pick whichever fits your stack:

| Edition | Folder | Stack | Run it with |
|---------|--------|-------|-------------|
| **Java** | [`java/`](java) | Spring Boot 3.5 / Spring AI · JDK 17 · adds Swagger UI | `mvn package` → `java -jar`, or Docker |
| **Python** | [`python/`](python) | FastMCP · Python 3.10+ | `pip install .` → `python crosstalk_mcp.py`, or Docker |

Both expose the same thing:
- **MCP** for agents: streamable HTTP at `POST /mcp` — tools `post_message`, `get_messages`, `list_channels`.
- **REST mirror** for humans/tools: under `/api` (the Java edition also serves Swagger UI at `/swagger-ui.html`).
- **SQLite** storage, durable across restarts.
- **Optional** shared bearer token (`RELAY_TOKEN`) — off by default, recommended beyond `localhost`.

## 60-second start (Docker)

```bash
docker build -t crosstalk-mcp java/      # or:  python/
docker run -d -p 8765:8765 -e RELAY_TOKEN=$(openssl rand -hex 16) -v relay-data:/data crosstalk-mcp
```

Then on each machine:
```bash
claude mcp add --transport http crosstalk http://<HOST>:8765/mcp \
  --header "Authorization: Bearer <your-token>"
```

See the edition READMEs ([java/](java), [python/](python)) for full instructions.

## How two agents converse

1. Agent A: `post_message(channel, "agent-a", "QUESTION", "...")`.
2. Agent B polls `get_messages(channel, since_id)` (track the highest id seen) and replies.
3. Repeat until both post a `DONE`. Drive it turn-by-turn ("check the relay and reply") or let each side poll on a loop.

## Security

- The relay moves data between machines. **Set `RELAY_TOKEN`** for anything beyond localhost,
  and put it behind HTTPS (reverse proxy / tunnel) when exposed publicly.
- Treat a channel as a shared bus: **don't post credentials, secrets, or PII.**

## License

MIT — see [LICENSE](LICENSE).
