# crosstalk-mcp (Python edition)

A tiny **cross-machine relay MCP server** — a shared "mailbox" two coding agents connect to
so they can message each other and run a back-and-forth until they're done. Unlike file- or
SQLite-on-one-box approaches, this relay speaks **streamable-HTTP MCP over the network**, so
the two agents can live on **different machines** (same LAN, a VPN, or a tunnel).

> This is the **Python** (FastMCP) edition. A functionally equivalent **Java** edition lives
> on the [`java`](../java) folder — pick whichever fits your stack.

- **MCP** (for agents): streamable HTTP at `POST /mcp` — tools `post_message`, `get_messages`, `list_channels`.
- **REST mirror** (for humans/tools): `GET /api/channels`, `GET|POST /api/channels/{channel}/messages`, `GET /api/channels/{channel}/wait?since_id=&timeout_s=` (long-poll mirror of `wait_for_message`), `GET /api/channels/{channel}/stream?since_id=` (Server-Sent Events stream).
- **Storage:** SQLite file (`relay.db`), durable across restarts.
- **Auth:** optional shared bearer token (`RELAY_TOKEN`) — off by default, **strongly recommended** whenever the relay is reachable beyond `localhost`.

## Quick start

### Option A — Docker (recommended)
```bash
docker build -t crosstalk-mcp .
docker run -d --name relay -p 8765:8765 \
  -e RELAY_TOKEN=$(openssl rand -hex 16) \
  -v relay-data:/data \
  crosstalk-mcp
```

### Option B — Python (3.10+)
```bash
pip install -r requirements.txt          # or: pip install .
RELAY_TOKEN=your-shared-secret PORT=8765 python crosstalk_mcp.py
# (after `pip install .` you can also run the console script: crosstalk-mcp)
```

The host must allow inbound TCP on the port (default 8765) for other machines to connect.

## Connect your agents (run on each machine)

```bash
# same machine:
claude mcp add --transport http crosstalk http://localhost:8765/mcp \
  --header "Authorization: Bearer your-shared-secret"

# another machine (use the host's reachable IP/hostname):
claude mcp add --transport http crosstalk http://<HOST>:8765/mcp \
  --header "Authorization: Bearer your-shared-secret"
```
(Drop the `--header` if you run without `RELAY_TOKEN`.) Allowlist the `crosstalk` tools on each
side so polling doesn't prompt for permission every time.

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `post_message` | `channel, sender, type, body` | `{id, channel, created_at}` |
| `get_messages` | `channel, since_id` (0 = all) | messages with `id > since_id` |
| `list_channels` | — | channels with counts + last activity |
| `wait_for_message` | `channel, since_id, timeout_s` (default 30, capped 300) | new messages with `id > since_id`, blocking until one arrives or timeout (`[]` on timeout) |

Pick any `channel` name; both sides use the same one. `type` is a free-text label
(`NOTE`, `QUESTION`, `ANSWER`, `DONE`, …) you define for your workflow.

## How two agents converse

1. Agent A: `post_message(channel, "agent-a", "QUESTION", "...")`.
2. Agent B polls `get_messages(channel, since_id)` (track the highest id seen), replies with `post_message(...)`.
3. Repeat until both post a `DONE`. Drive it turn-by-turn ("check the relay and reply") or let each side poll on a loop.

For **low-latency, cost-efficient back-and-forth**, skip polling altogether: an agent `post_message(...)` then `wait_for_message(channel, <last_id_seen>)` to block until the peer replies (or timeout). Loop that turn-by-turn instead. The relay still just moves messages; the agents drive who speaks when.

## Security

- The relay moves data between machines. **Set `RELAY_TOKEN`** for anything beyond localhost,
  and prefer HTTPS (terminate TLS at a reverse proxy / tunnel) when exposed publicly.
- Treat a channel as a shared bus: **don't post credentials, secrets, or PII.**
- The token is a single shared secret; rotate it if it leaks.

## License

MIT — see [LICENSE](LICENSE).
