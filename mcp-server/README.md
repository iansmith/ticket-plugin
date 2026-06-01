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

With the dev container running, verify the server lists its tools and
`rag_health` returns a healthy response:

```bash
python3 - <<'PY'
import json, subprocess

proc = subprocess.Popen(
    ["python3", "mcp-server/server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)

def rpc(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())

# 1. Initialize
rpc({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2024-11-05","capabilities":{},
    "clientInfo":{"name":"smoke","version":"0"}}})
proc.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}) + "\n")
proc.stdin.flush()

# 2. List tools
tools = rpc({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
print("tools:", [t["name"] for t in tools["result"]["tools"]])

# 3. Call rag_health
health = rpc({"jsonrpc":"2.0","id":3,"method":"tools/call",
              "params":{"name":"rag_health","arguments":{}}})
print("health:", health["result"]["content"][0]["text"])

proc.terminate()
PY
```

Expected output:
```
tools: ['search_tickets', 'rag_health']
health: {
  "postgres": "ok",
  "schema": "ok"
}
```

Or open a Claude Code session in this directory and ask:
> "Call rag_health, then search_tickets for 'multicol nested overflow'."
