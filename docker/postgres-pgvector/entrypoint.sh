#!/bin/bash
# entrypoint.sh — ticket-rag service container orchestrator (BILL-17).
#
# ============================================================
# Process-supervisor choice — documented per BILL-17 contract
# ============================================================
# This is a custom shell-based supervisor, NOT supervisord / s6 / runit / tini.
#
# Why a shell wrapper:
#   - Two processes total need supervision: postgres and uvicorn.
#   - The upstream postgres image's docker-entrypoint.sh already gives us
#     correct SIGTERM handling for postgres on its own.
#   - A ~50-line shell wrapper is enough to launch uvicorn alongside and
#     propagate SIGTERM to both children.
#   - A real process supervisor would add tens of MB of dependencies plus a
#     second config language for negligible gain at this scale.
#
# When to revisit:
#   - If a third long-lived process needs supervising (background harvester,
#     periodic re-embed worker, etc.).
#   - If this script gets harder to debug than a real supervisor would be.
#
# Anyone touching the container's process model should start by reading
# this header. Failure modes worth knowing about are listed at the bottom.
#
# ============================================================
# Startup sequence
# ============================================================
#   1. Start postgres via the upstream entrypoint in the background.
#      On a fresh volume this runs initdb + the /docker-entrypoint-initdb.d/
#      hooks (00-trust-auth.sh) before opening for real connections.
#      On a reused volume it just starts the postmaster.
#   2. Wait until postgres accepts a real `SELECT 1` query (not just the
#      listener — initdb on a fresh volume keeps the listener silent during
#      bootstrap).
#   3. Re-apply schema/*.sql in numerical order, idempotently. Every CREATE
#      in those files uses IF NOT EXISTS; this is a hard repo convention.
#      The init-time copy under /docker-entrypoint-initdb.d/ was removed in
#      BILL-17 — the entrypoint is the single application point now.
#   4. Install a SIGTERM/SIGINT trap that forwards to uvicorn (graceful
#      TERM) and postgres (pg_ctl -m fast stop), then exits 0.
#   5. Launch uvicorn in the background and block on it. If uvicorn exits
#      on its own (crash), bring postgres down cleanly and exit with
#      uvicorn's status code.
#
# Failure modes worth knowing about:
#   - postgres never becomes ready within PG_READY_TIMEOUT_SEC -> exit 1
#     before uvicorn starts. Inspect the postgres logs above the FATAL line.
#   - a schema/*.sql file fails -> psql aborts the whole script (set -e +
#     ON_ERROR_STOP). The schema file is the bug; fix it in-place. Idempotent
#     SQL is non-negotiable for files in /docker-entrypoint-initdb.d/schema/.
#   - uvicorn crashes -> postgres is stopped, container exits with uvicorn's
#     status. Docker restart policy (if any) handles the rest.

set -e

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-7777}"
PG_READY_TIMEOUT_SEC="${PG_READY_TIMEOUT_SEC:-30}"
SCHEMA_DIR="/docker-entrypoint-initdb.d/schema"

UVICORN_PID=""

# ----------------------------------------------------------------------
# SIGTERM / SIGINT handler: ordered shutdown, exit 0.
# ----------------------------------------------------------------------
shutdown() {
    echo "[entrypoint] signal received; shutting down"
    if [ -n "$UVICORN_PID" ]; then
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
    fi
    # pg_ctl talks to whichever postmaster owns $PGDATA, regardless of how
    # postgres was launched. -m fast disconnects clients and skips
    # checkpoint-on-shutdown waits; next start needs no recovery.
    pg_ctl -D "$PGDATA" -m fast stop 2>/dev/null || true
    if [ -n "$UVICORN_PID" ]; then
        wait "$UVICORN_PID" 2>/dev/null || true
    fi
    exit 0
}
trap shutdown SIGTERM SIGINT

# ----------------------------------------------------------------------
# Step 1 — postgres in the background via the upstream entrypoint.
# ----------------------------------------------------------------------
echo "[entrypoint] starting postgres via docker-entrypoint.sh postgres"
docker-entrypoint.sh postgres &

# ----------------------------------------------------------------------
# Step 2 — wait for a real SELECT, not just for the listener.
# ----------------------------------------------------------------------
echo "[entrypoint] waiting up to ${PG_READY_TIMEOUT_SEC}s for postgres to accept queries"
ready=0
for _ in $(seq 1 "$PG_READY_TIMEOUT_SEC"); do
    if pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -q \
       && psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc 'SELECT 1' >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    echo "[entrypoint] FATAL: postgres did not become ready within ${PG_READY_TIMEOUT_SEC}s" >&2
    exit 1
fi
echo "[entrypoint] postgres accepting queries"

# ----------------------------------------------------------------------
# Step 3 — re-apply schema/*.sql idempotently on every start.
# ----------------------------------------------------------------------
if [ -d "$SCHEMA_DIR" ]; then
    shopt -s nullglob
    schema_files=("$SCHEMA_DIR"/*.sql)
    shopt -u nullglob
    if [ "${#schema_files[@]}" -gt 0 ]; then
        # Filename-sorted so 001_ < 002_ < ... regardless of glob order.
        IFS=$'\n' schema_files=($(printf '%s\n' "${schema_files[@]}" | sort))
        unset IFS
        for f in "${schema_files[@]}"; do
            psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                 -v ON_ERROR_STOP=1 --single-transaction -f "$f" >/dev/null
            echo "[entrypoint] applied schema/$(basename "$f")"
        done
    else
        echo "[entrypoint] no *.sql files in $SCHEMA_DIR"
    fi
else
    echo "[entrypoint] schema dir $SCHEMA_DIR missing; skipping"
fi

# ----------------------------------------------------------------------
# Step 4 — uvicorn in the background. Supervisor stays alive on `wait` so
# the SIGTERM trap can fire. Do NOT `exec uvicorn` — that hands PID 1 to
# uvicorn and we lose the trap.
# ----------------------------------------------------------------------
echo "[entrypoint] starting uvicorn on $APP_HOST:$APP_PORT"
cd /app
python3 -m uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT" &
UVICORN_PID=$!

# Drop set -e so a non-zero uvicorn exit doesn't bypass the postgres cleanup
# below. The container's overall exit code comes from uvicorn in that case.
set +e

wait "$UVICORN_PID"
uv_status=$?

echo "[entrypoint] uvicorn exited with status $uv_status; stopping postgres"
pg_ctl -D "$PGDATA" -m fast stop 2>/dev/null || true

exit "$uv_status"
