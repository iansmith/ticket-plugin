# postgres-pgvector — ticket-rag base image (BILL-12)

A minimal Postgres 18 + `pgvector` Docker image with externally-mounted data and full trust auth. The database-only building block of the larger ticket-rag service container ([BILL-13](https://github.com/iansmith/ticket-plugin/issues/13) umbrella).

## ⚠️ Trust auth is on

Every connection method (Unix socket, IPv4, IPv6) accepts any user with no password. **Run on `127.0.0.1` only, behind no network.** Do not expose this image to any untrusted network. The configuration is correct for a local single-user RAG sidecar; it is wildly unsuitable for anything else.

## Architecture

- **Base:** [`pgvector/pgvector:0.8.2-pg18`](https://hub.docker.com/r/pgvector/pgvector) — official multi-arch image (`linux/amd64`, `linux/arm64`) maintained by the pgvector project. Built on **Debian 12 (bookworm)**. Pinned to pgvector 0.8.2 on Postgres 18 for reproducibility; bump the FROM line to roll forward.
- **Auth override:** an init script under `/docker-entrypoint-initdb.d/` overwrites `pg_hba.conf` after `initdb` to guarantee trust on every connection type. The override runs once per fresh data directory (because that's when initdb runs); subsequent starts reuse the configured cluster as-is.
- **Data directory:** expected to be host-mounted at **`/var/lib/postgresql`** (the new pg18+ convention — the upstream image creates a major-version subdirectory like `/var/lib/postgresql/18/docker/` underneath, so the same mount can later hold both a pg18 and pg19 cluster for `pg_upgrade --link`). The image carries no baked-in cluster data. See [docker-library/postgres#1259](https://github.com/docker-library/postgres/pull/1259) for background.
- **Lifecycle:** inherited from the upstream postgres image — `initdb` on empty volume, reuse on existing cluster, fast-shutdown on SIGTERM.

## Build

```bash
docker build -t ticket-plugin/postgres-pgvector:latest docker/postgres-pgvector/
```

## Run

```bash
# From the repo root — uses the tracked pgdata/ directory as the mount.
docker run -d \
  --name ticket-rag-pg \
  -v "$PWD/pgdata:/var/lib/postgresql" \
  -p 127.0.0.1:5432:5432 \
  ticket-plugin/postgres-pgvector:latest
```

The repo ships an empty `pgdata/` at its root (with a `.gitkeep` so the directory is tracked but its contents are gitignored). After the first `docker run` against it, `pgdata/18/docker/` contains the live cluster. Wipe `pgdata/18/` to start over from a clean initdb.

Notes:
- `-p 127.0.0.1:5432:5432` binds postgres to localhost only on the host. **Do not** publish on `0.0.0.0` — trust auth means anyone reaching the port has full superuser access.
- The first run against an empty mounted directory initializes a cluster. Subsequent runs reuse it.
- If the mounted directory exists but isn't a valid cluster, the upstream entrypoint refuses to start. This prevents accidental `initdb` over user data.

## Verify

```bash
# Server reachable
psql -h 127.0.0.1 -p 5432 -U postgres -c 'SELECT version();'

# pgvector available
psql -h 127.0.0.1 -p 5432 -U postgres -c 'CREATE EXTENSION vector;'
psql -h 127.0.0.1 -p 5432 -U postgres -c "SELECT 'vector'::regtype;"
```

The `CREATE EXTENSION vector;` succeeds once per database. The schema bootstrap that creates `ticket_chunks` runs the same `CREATE EXTENSION IF NOT EXISTS vector` and is owned by [BILL-16](https://github.com/iansmith/ticket-plugin/issues/16); it is not the responsibility of this image.

## Stop / restart

```bash
docker stop ticket-rag-pg     # SIGTERM → fast-shutdown; no recovery needed on next start
docker start ticket-rag-pg    # reuses the cluster on the mounted volume
```

## Where this fits

This image is the **base layer** of the ticket-rag service container.

| Ticket | Layer |
|---|---|
| **BILL-12 (this image)** | Postgres + pgvector, externally-mounted data, trust auth |
| [BILL-14](https://github.com/iansmith/ticket-plugin/issues/14) | Python 3.12 + FastAPI on top |
| [BILL-15](https://github.com/iansmith/ticket-plugin/issues/15) | `bge-m3` encoder + `bge-reranker-v2-m3` baked in |
| [BILL-16](https://github.com/iansmith/ticket-plugin/issues/16) | `ticket_chunks` schema applied on container start |
| [BILL-17](https://github.com/iansmith/ticket-plugin/issues/17) | Single entrypoint orchestrating postgres + uvicorn |
| [BILL-18](https://github.com/iansmith/ticket-plugin/issues/18) | Reproducible build pipeline + Taskfile + documented size |

The full integrated service container, including the formal end-to-end smoke test, is owned by [BILL-13](https://github.com/iansmith/ticket-plugin/issues/13).
