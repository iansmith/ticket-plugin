#!/usr/bin/env bash
# verify-bill17.sh — BILL-17 acceptance: entrypoint orchestration + end-to-end smoke test.
#
# Usage:
#   bash docker/postgres-pgvector/verify-bill17.sh [IMAGE_TAG]
#
# Default IMAGE_TAG is the pre-BILL-17 baseline so the script starts RED:
#   ticket-plugin/postgres-pgvector:bill15
#
# After building the BILL-17 image, pass the new tag:
#   bash docker/postgres-pgvector/verify-bill17.sh ticket-plugin/postgres-pgvector:bill17
#
# All in-container probes go via `docker exec` (no host port publishing), so
# the script does not conflict with other containers bound to host 7777/5432.

set -u

IMAGE="${1:-ticket-plugin/postgres-pgvector:bill15}"
CONTAINER="ticket-rag-bill17-verify"
DATA_DIR=$(mktemp -d -t bill17-pgdata.XXXXXX)
PASS=0
FAIL=0

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    chmod -R u+w "$DATA_DIR" 2>/dev/null || true
    rm -rf "$DATA_DIR"
}
trap cleanup EXIT

note() { echo "  ----  $*"; }

check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS  $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name"
        FAIL=$((FAIL + 1))
    fi
}

# Probe /healthz inside the container, with retry. Pass iff BOTH postgres:ok
# AND schema:ok in the JSON body.
healthz_ok_both() {
    local timeout="${1:-30}"
    for _ in $(seq 1 "$timeout"); do
        if docker exec "$CONTAINER" python3 -c "
import json, sys, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:7777/healthz', timeout=2) as r:
        d = json.loads(r.read().decode())
        sys.exit(0 if d.get('postgres')=='ok' and d.get('schema')=='ok' else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

echo "BILL-17 verification — image: $IMAGE"
echo "host data dir: $DATA_DIR"
echo "---"

# -------------------------------------------------------------------
# Phase A — fresh-volume boot via single `docker run` (no manual exec)
# -------------------------------------------------------------------
note "Phase A — fresh-volume boot"

docker run -d \
    --name "$CONTAINER" \
    -v "$DATA_DIR:/var/lib/postgresql" \
    "$IMAGE" >/dev/null 2>&1

# Check 1 — the headline acceptance: self-boot reports both subsystems ok.
check "fresh-volume boot: /healthz returns 200 with postgres:ok AND schema:ok within 30s" \
    healthz_ok_both 30

# Check 2 — table is materialised in the database itself (not just the app's view).
check "ticket_chunks table exists after first boot" \
    bash -c "docker exec $CONTAINER psql -U postgres -d postgres -tAc \"SELECT to_regclass('public.ticket_chunks') IS NOT NULL;\" 2>/dev/null | grep -q '^t\$'"

# Check 3 — uvicorn actually started (not just postgres).
check "logs contain a uvicorn startup line" \
    bash -c "docker logs $CONTAINER 2>&1 | grep -q -i 'Uvicorn running on'"

# Check 4 — no fatal/panic/Traceback markers from either subsystem.
check "logs do not contain FATAL / panic / Traceback markers" \
    bash -c "! docker logs $CONTAINER 2>&1 | grep -E -i '(FATAL|panic|^Traceback)'"

# -------------------------------------------------------------------
# Phase B — graceful stop, then reuse-volume restart
# -------------------------------------------------------------------
note "Phase B — graceful stop and reuse-volume restart"

# Check 5 — docker stop returns within 15s (the SIGTERM-graceful window).
stop_within_15s() {
    local start end
    start=$(date +%s)
    docker stop --time 15 "$CONTAINER" >/dev/null 2>&1 || return 1
    end=$(date +%s)
    [ $((end - start)) -lt 15 ]
}
check "docker stop returns cleanly within 15s" stop_within_15s

# Check 6 — container exited 0 (no SIGKILL, no panic exit).
check "container exit code is 0 after clean stop" \
    bash -c "[ \"\$(docker inspect -f '{{.State.ExitCode}}' $CONTAINER 2>/dev/null)\" = \"0\" ]"

# Check 7 — restart reuses the same volume and /healthz comes back green.
restart_and_healthz() {
    docker start "$CONTAINER" >/dev/null 2>&1 || return 1
    healthz_ok_both 30
}
check "docker start re-uses volume; /healthz returns 200 with both checks" \
    restart_and_healthz

# Check 8 — the second-boot schema reapply does not emit psql errors.
# (Idempotent re-application is the contract; "already exists" must be
# silenced by IF NOT EXISTS, not raised.)
check "second-boot logs contain no schema apply ERROR lines" \
    bash -c "! docker logs --since=1m $CONTAINER 2>&1 | grep -E -i 'ERROR.*(ticket_chunks|already exists)'"

echo "---"
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
