# Session continuation — 2026-06-01

## Fixes committed this session (commit 22c2038)

Two bugs found and fixed, committed directly to master (no ticket — tiny):

1. **`docker/postgres-pgvector/Dockerfile`** — added `ENV PYTHONPATH=/app`.
   - Root cause: uvicorn runs via `cd /app && python3 -m uvicorn`, but `docker exec` defaults to `/`. Without PYTHONPATH, `import rag_service.*` failed for all `docker exec`-based commands (including the Tier 1 smoke test checks and any CLI harvester invocations from outside the container).

2. **`docker/postgres-pgvector/verify-bill37.sh`** — bash 3.2 empty-array `set -u` crash.
   - `"${DOCKER_ENV[@]}"` with an empty array is unbound in bash 3.2 (macOS default). Fixed with `${DOCKER_ENV[@]+"${DOCKER_ENV[@]}"}`.

## Smoke test results (all on slopstop-rag:22c2038)

| Script | Result |
|---|---|
| verify-bill15 | 5/5 pass |
| verify-bill17 | 8/8 pass |
| verify-bill18 | 10/10 pass |
| verify-bill37 Tier 1 | 4/4 pass |
| verify-bill37 Tier 2 (live Linear) | 3/3 pass (with LINEAR_API_KEY) |

## New infrastructure added this session (not yet committed)

### `search.sh` (repo root)

Interactive semantic search script. Usage:
```bash
./search.sh "some query"           # top 10 with reranking
./search.sh "some query" --k 5
./search.sh "some query" --raw     # raw JSON
```
Requires `slopstop-rag-dev` running (`make rag-dev-start`). Uses `jq` + `curl` to hit `http://localhost:7777/search`.

### Makefile targets added

- `rag-dev-start` — builds if needed, starts `slopstop-rag-dev` with `pgdata/` mounted (stable storage) and port 7777 published to localhost. Sources `.harvester.toml` for `LINEAR_API_KEY`.
- `rag-dev-stop` — stops and removes the dev container (pgdata survives).
- `rag-dev-status` — shows running state.

### Dev container

`pgdata/` at repo root is the stable volume (already gitignored via `pgdata/*`). The dev container was started and is likely still running as of session end. Check with `make rag-dev-status`.

## What to do next

The session ended before running the 50-ticket sanity corpus sync. Start here:

```bash
make rag-dev-status          # confirm container is up, or: make rag-dev-start
source .harvester.toml
for i in $(seq 50 100); do
  docker exec slopstop-rag-dev python3 -m rag_service.harvesters.linear sync-ticket LOU-$i 2>&1
done
```

After the sync completes:
1. Use `search.sh` to interactively test retrieval quality across those tickets
2. Commit `search.sh` and the Makefile `rag-dev-*` targets
