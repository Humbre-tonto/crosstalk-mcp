# crosstalk-mcp (npx launcher)

One-command launcher for the [crosstalk-mcp](https://github.com/Humbre-tonto/crosstalk-mcp) relay —
a tiny cross-machine relay MCP server. It boots the relay two ways:

```bash
# Run the bundled Python edition (needs python3 3.10+ on the host)
npx crosstalk-mcp python --port 8765 --token s3cret

# Run the published Docker image (needs docker on the host)
npx crosstalk-mcp docker --port 8765 --participants "humanX:tokX,humanY:tokY"
```

Once running, agents connect their MCP client to `POST /mcp`, and humans open `/ui` in a browser.

## Commands

| Command | What it does | Requires |
|---------|--------------|----------|
| `python` | Runs the bundled `crosstalk_mcp.py`, installing its Python deps (`mcp`, `uvicorn`) on first run if missing. | `python3` 3.10+ |
| `docker` | `docker run`s `ghcr.io/humbre-tonto/crosstalk-mcp-python:latest`, publishing the port. | `docker` |
| `help` | Prints usage. | — |

## Options

Both commands accept the same options, which map onto the relay's environment variables — so you can
also set the env vars directly instead of passing flags.

| Flag | Env var | Default | Notes |
|------|---------|---------|-------|
| `-p, --port <n>` | `PORT` | `8765` | Port to listen on. |
| `--host <addr>` | `HOST` | `0.0.0.0` | Bind address (python only; docker always binds `0.0.0.0` inside the container). |
| `--token <tok>` | `RELAY_TOKEN` | — | Shared bearer token. |
| `--participants <map>` | `RELAY_PARTICIPANTS` | — | Per-participant identity-bound tokens, `"id:tok,id2:tok2"`. |
| `--db <path>` | `RELAY_DB` | `relay.db` | SQLite database file. |
| `--image <ref>` | `CROSSTALK_IMAGE` | `ghcr.io/humbre-tonto/crosstalk-mcp-python:latest` | `docker` command only. |

## Notes

- **Python deps:** the `python` command installs `mcp` and `uvicorn` into the active Python
  environment on first run. Prefer a virtualenv to avoid touching system packages.
- **Image version:** the `docker` command pulls the published image; make sure a release has been
  cut (`v*` tag) so the image reflects the current relay.
- This launcher wraps the existing editions; the Python edition remains the source of truth (the
  Python files are bundled into the package at publish time).
