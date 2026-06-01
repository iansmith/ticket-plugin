# slopstop RAG MCP server

Exposes the slopstop RAG service as two MCP tools for Claude Code:

| Tool | What it does |
|---|---|
| `search_tickets` | Semantic search over the LOU/BILL ticket corpus |
| `rag_health` | Health-check the RAG container before searching |

## Prerequisites

1. **RAG dev container running.**  From the repo root:

   ```bash
   make rag-dev-start
   ```

   Confirm it's up:

   ```bash
   curl http://localhost:7777/healthz
   # → {"postgres":"ok","schema":"ok"}
   ```

2. **Python dependencies installed** (one-time, system Python):

   ```bash
   pip3 install -r mcp-server/requirements.txt
   ```

## Claude Code wiring

The project `.mcp.json` at the repo root wires the server automatically.
Open any Claude Code session inside the `ticket-plugin` directory and the
`slopstop-rag` MCP server will be available without any extra steps.

To override the RAG service URL (e.g. a non-default port):

```bash
RAG_SERVICE_URL=http://localhost:9000 claude
```

Or edit `.mcp.json` directly.

## Manual smoke test

With the dev container running:

```bash
python3 -c "
import subprocess, json, sys

req = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}
})
# FastMCP speaks stdio — pipe in an initialize + tools/list sequence
"
```

Or just open a Claude Code session and ask:
> "Call rag_health, then search_tickets for 'multicol nested overflow'."
