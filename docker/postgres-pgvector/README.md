# postgres-pgvector — ticket-rag service container

A self-bootstrapping Docker image bundling Postgres 18 + `pgvector` + the ticket-rag FastAPI app + the bge-m3 encoder and bge-reranker-v2-m3 reranker. The single deployable artifact behind the [BILL-13](https://github.com/iansmith/slopstop/issues/13) umbrella.

> The build-context directory keeps its historical name (`docker/postgres-pgvector/`) for git-blame continuity, but the canonical image *tag* is now **`slopstop/rag`** (renamed from `slopstop/postgres-pgvector` in BILL-18 — the old name had become a misnomer once FastAPI + models + the orchestrator layered on top).

## ⚠️ Trust auth is on

Every postgres connection method (Unix socket, IPv4, IPv6) accepts any user with no password. **Run on `127.0.0.1` only, behind no network.** Do not expose this image to any untrusted network. The configuration is correct for a local single-user RAG sidecar; it is wildly unsuitable for anything else.

## Architecture

- **Base:** [`pgvector/pgvector:0.8.2-pg18`](https://hub.docker.com/r/pgvector/pgvector) — official multi-arch image (`linux/amd64`, `linux/arm64`) maintained by the pgvector project. Built on **Debian 12 (bookworm)**. Pinned to pgvector 0.8.2 on Postgres 18 for reproducibility; bump the FROM line to roll forward.
- **Auth override:** an init script under `/docker-entrypoint-initdb.d/` overwrites `pg_hba.conf` after `initdb` to guarantee trust on every connection type. Runs once per fresh data directory.
- **Schema:** `ticket_chunks` (per `design/ticket-rag.md`). Re-applied **every container start** by the entrypoint script using fully idempotent `CREATE ... IF NOT EXISTS` statements. Future schema files drop into `schema/` and are picked up in filename-sorted order.
- **App:** Python 3.12 + FastAPI + uvicorn. Currently exposes `/healthz` only; query endpoints land in future tickets under BILL-13.
- **Models:** `BAAI/bge-m3` (encoder) and `BAAI/bge-reranker-v2-m3` (cross-encoder reranker) baked into the image at build time, loaded offline at runtime (`HF_HUB_OFFLINE=1`).
- **Entrypoint:** `entrypoint.sh` is a small shell-based supervisor. See [Entrypoint & process supervisor](#entrypoint--process-supervisor) below.
- **Data directory:** expected to be host-mounted at **`/var/lib/postgresql`** (the new pg18+ convention — the upstream image creates a major-version subdirectory like `/var/lib/postgresql/18/docker/` underneath, so the same mount can later hold both a pg18 and pg19 cluster for `pg_upgrade --link`). The image carries no baked-in cluster data. See [docker-library/postgres#1259](https://github.com/docker-library/postgres/pull/1259) for background.

## Image size and disk requirements

**Image size:** ~5.7 GB (measured against `slopstop/rag:latest` as of BILL-18 — `docker image inspect ... --format '{{.Size}}'` divided by 1024³). The bulk is the bge-m3 and reranker model weights (~4.5 GB combined) plus the Python + ML stack from `requirements.txt` (~700 MB resident). Shrinking toward the design doc's ~3 GB target (multi-stage Python build, fp16 weights, etc.) is a follow-up — out of scope for BILL-18.

**Peak Docker disk during a from-scratch build:** ~12-13 GB. Breakdown — `pgvector/pgvector:0.8.2-pg18` + `python:3.12-slim-bookworm` base images (~1.5 GB combined) + transient build state (pip downloads, dpkg unpacks, model COPYs in flight ≈ 4-5 GB) + the final 6 GB image being exported. A clean Docker Desktop install with its default VM allocation (typically 64 GB) has plenty of headroom. If disk pressure shows up after many rebuild cycles, `make rag-clean-deep` clears both the rag images and the BuildKit cache.

## Build

```bash
# Recommended — from the repo root:
make rag-build
```

`make rag-build` tags the image as both `slopstop/rag:<git-sha>` (immutable per commit) and `slopstop/rag:latest` (moving pointer to the last successful build). Under the hood it just runs:

```bash
docker build -t slopstop/rag:$(git rev-parse --short HEAD) -t slopstop/rag:latest docker/postgres-pgvector/
```

The Dockerfile's layer order is intentional — the most-changing layer (the FastAPI app code) is last, so editing `app/main.py` and re-running `make rag-build` rebuilds only that single layer; the postgres base, system deps, Python deps, models, schema, and entrypoint stay cached.

## Run

A single `docker run` brings up postgres, applies the schema, and starts uvicorn — no second `docker exec` step.

```bash
# From the repo root — uses the tracked pgdata/ directory as the mount.
docker run -d \
  --name ticket-rag \
  -v "$PWD/pgdata:/var/lib/postgresql" \
  -p 127.0.0.1:5432:5432 \
  -p 127.0.0.1:7777:7777 \
  slopstop/rag:latest
```

The repo ships an empty `pgdata/` at its root (with a `.gitkeep` so the directory is tracked but its contents are gitignored). After the first `docker run` against it, `pgdata/18/docker/` contains the live cluster. Wipe `pgdata/18/` to start over from a clean initdb.

Notes:
- `-p 127.0.0.1:...` binds both services to localhost only on the host. **Do not** publish on `0.0.0.0` — trust auth means anyone reaching the port has full superuser access.
- The first run against an empty mounted directory initializes a cluster (initdb runs the trust-auth hook), then the entrypoint applies the schema and starts uvicorn. Subsequent runs reuse the cluster and just re-apply schema idempotently.
- If the mounted directory exists but isn't a valid cluster, the upstream entrypoint refuses to start. This prevents accidental `initdb` over user data.

## Verify

The end-to-end smoke test is a single command:

```bash
make rag-run
```

This builds (or reuses) the image, then runs `verify-bill17.sh` against `slopstop/rag:latest`. Output ends with `Results: 8 passed, 0 failed` when healthy. Eight checks cover fresh-volume boot, schema presence, uvicorn startup, no FATAL/panic/Traceback in logs, clean stop within 15s with exit 0, and clean restart with reused volume.

For fine-grained diagnostics:

```bash
# Wait up to ~10 seconds after `docker run`, then:
curl -s http://127.0.0.1:7777/healthz
# {"postgres":"ok","schema":"ok"}    -> HTTP 200, container is fully up
# {"postgres":"unreachable",...}     -> HTTP 503, postgres not yet reachable (or down)
# {"postgres":"ok","schema":"missing"} -> HTTP 503, schema bootstrap didn't run/failed

# Direct postgres checks (also useful):
psql -h 127.0.0.1 -p 5432 -U postgres -c 'SELECT version();'
psql -h 127.0.0.1 -p 5432 -U postgres -c "SELECT to_regclass('public.ticket_chunks');"
```

The `ticket_chunks` table is created by the entrypoint, not by `psql` from the host. `\d ticket_chunks` should show all the columns + indexes defined in `schema/001_ticket_chunks.sql`.

> **Note:** `verify-bill17.sh`'s default-arg image (`slopstop/postgres-pgvector:bill15`) is a historical RED-baseline that's no longer kept around locally. Always invoke with an explicit tag; `make rag-run` does this for you.

## Stop / restart

```bash
docker stop ticket-rag     # SIGTERM → entrypoint shuts uvicorn (graceful) and postgres (-m fast stop)
docker start ticket-rag    # reuses the cluster on the mounted volume; re-applies schema idempotently
```

Clean stop is expected to complete within ~10 seconds. The container exits with status 0 on a clean SIGTERM-driven shutdown.

## Cleanup

```bash
make rag-clean         # remove slopstop/rag images + smoke-test container
make rag-clean-deep    # all of the above, PLUS prune BuildKit's build cache
```

`rag-clean` removes the `slopstop/rag` images (all tags) and any leftover `ticket-rag-bill17-verify` container from prior smoke-test runs. Reach for `rag-clean-deep` when Docker Desktop VM disk pressure accumulates from repeated builds — it adds `docker builder prune -a -f` which reclaims the layer cache.

Neither target touches the host's `pgdata/` directory or any other data you mounted — that's your data, not ours to delete.

## Entrypoint & process supervisor

`entrypoint.sh` is a custom shell-based supervisor — **not** supervisord, s6, runit, or tini. The rationale, the startup sequence, and the failure modes are documented in the script's header comments. If you ever need to debug why postgres or uvicorn isn't behaving inside the container, **start by reading `entrypoint.sh`'s header.** That is the contract; this is the pointer to it.

In one sentence: postgres starts via the upstream `docker-entrypoint.sh postgres` in the background, the supervisor waits for it to accept real queries, re-applies `schema/*.sql` idempotently, traps SIGTERM/SIGINT, then runs uvicorn in the background and blocks on `wait` so the trap can do an ordered shutdown.

## Where this fits

| Ticket | Layer | Status |
|---|---|---|
| [BILL-12](https://github.com/iansmith/slopstop/issues/12) | Postgres + pgvector, externally-mounted data, trust auth | merged |
| [BILL-14](https://github.com/iansmith/slopstop/issues/14) | Python 3.12 + FastAPI on top | merged |
| [BILL-15](https://github.com/iansmith/slopstop/issues/15) | `bge-m3` encoder + `bge-reranker-v2-m3` baked in | merged |
| [BILL-16](https://github.com/iansmith/slopstop/issues/16) | `ticket_chunks` schema files (applied by BILL-17 entrypoint) | merged |
| [BILL-17](https://github.com/iansmith/slopstop/issues/17) | Single entrypoint orchestrating postgres + schema + uvicorn; SIGTERM; smoke test | merged |
| **[BILL-18](https://github.com/iansmith/slopstop/issues/18) (this)** | Reproducible build pipeline (Makefile + layer-cache + image-size docs) | landing |

After BILL-18 closes, the [BILL-13](https://github.com/iansmith/slopstop/issues/13) umbrella has shipped its full scope: a self-bootstrapping, reproducibly-built, smoke-tested service container. Real ticket-search query endpoints (`/search`, `/local/sync`, etc.) are tracked under a separate umbrella to be filed once BILL-13 wraps.
