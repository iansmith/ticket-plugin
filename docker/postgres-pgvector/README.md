# postgres-pgvector — ticket-rag service container

A self-bootstrapping Docker image bundling Postgres 18 + `pgvector` + the ticket-rag FastAPI app + the bge-m3 encoder and bge-reranker-v2-m3 reranker. The single deployable artifact behind the [BILL-13](https://github.com/iansmith/ticket-plugin/issues/13) umbrella.

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

## Build

```bash
docker build -t ticket-plugin/postgres-pgvector:latest docker/postgres-pgvector/
```

Expected size is around 6 GB — the bulk is the bge-m3 and reranker weights. Build pipeline polish (layer-cache strategy, Taskfile, documented exact size) is owned by [BILL-18](https://github.com/iansmith/ticket-plugin/issues/18).

## Run

A single `docker run` brings up postgres, applies the schema, and starts uvicorn — no second `docker exec` step.

```bash
# From the repo root — uses the tracked pgdata/ directory as the mount.
docker run -d \
  --name ticket-rag \
  -v "$PWD/pgdata:/var/lib/postgresql" \
  -p 127.0.0.1:5432:5432 \
  -p 127.0.0.1:7777:7777 \
  ticket-plugin/postgres-pgvector:latest
```

The repo ships an empty `pgdata/` at its root (with a `.gitkeep` so the directory is tracked but its contents are gitignored). After the first `docker run` against it, `pgdata/18/docker/` contains the live cluster. Wipe `pgdata/18/` to start over from a clean initdb.

Notes:
- `-p 127.0.0.1:...` binds both services to localhost only on the host. **Do not** publish on `0.0.0.0` — trust auth means anyone reaching the port has full superuser access.
- The first run against an empty mounted directory initializes a cluster (initdb runs the trust-auth hook), then the entrypoint applies the schema and starts uvicorn. Subsequent runs reuse the cluster and just re-apply schema idempotently.
- If the mounted directory exists but isn't a valid cluster, the upstream entrypoint refuses to start. This prevents accidental `initdb` over user data.

## Verify

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

## Stop / restart

```bash
docker stop ticket-rag     # SIGTERM → entrypoint shuts uvicorn (graceful) and postgres (-m fast stop)
docker start ticket-rag    # reuses the cluster on the mounted volume; re-applies schema idempotently
```

Clean stop is expected to complete within ~10 seconds. The container exits with status 0 on a clean SIGTERM-driven shutdown.

## Entrypoint & process supervisor

`entrypoint.sh` is a custom shell-based supervisor — **not** supervisord, s6, runit, or tini. The rationale, the startup sequence, and the failure modes are documented in the script's header comments. If you ever need to debug why postgres or uvicorn isn't behaving inside the container, **start by reading `entrypoint.sh`'s header.** That is the contract; this is the pointer to it.

In one sentence: postgres starts via the upstream `docker-entrypoint.sh postgres` in the background, the supervisor waits for it to accept real queries, re-applies `schema/*.sql` idempotently, traps SIGTERM/SIGINT, then runs uvicorn in the background and blocks on `wait` so the trap can do an ordered shutdown.

## Where this fits

| Ticket | Layer | Status |
|---|---|---|
| [BILL-12](https://github.com/iansmith/ticket-plugin/issues/12) | Postgres + pgvector, externally-mounted data, trust auth | merged |
| [BILL-14](https://github.com/iansmith/ticket-plugin/issues/14) | Python 3.12 + FastAPI on top | merged |
| [BILL-15](https://github.com/iansmith/ticket-plugin/issues/15) | `bge-m3` encoder + `bge-reranker-v2-m3` baked in | merged |
| [BILL-16](https://github.com/iansmith/ticket-plugin/issues/16) | `ticket_chunks` schema files (applied by BILL-17 entrypoint) | merged |
| **[BILL-17](https://github.com/iansmith/ticket-plugin/issues/17) (this)** | Single entrypoint orchestrating postgres + schema + uvicorn; SIGTERM; smoke test | landing |
| [BILL-18](https://github.com/iansmith/ticket-plugin/issues/18) | Reproducible build pipeline + Taskfile + documented size | open |

The full integrated service container, including the formal end-to-end smoke test (`verify-bill17.sh`), is owned by the [BILL-13](https://github.com/iansmith/ticket-plugin/issues/13) umbrella. Real ticket-search query endpoints (`/search`, `/local/sync`, etc.) are tracked under a separate umbrella to be filed after BILL-13 closes.
