#!/usr/bin/env bash
# verify-bill37.sh — BILL-37 acceptance: Linear harvester end-to-end inside the
# running rag image.
#
# Usage:
#   bash docker/postgres-pgvector/verify-bill37.sh [IMAGE_TAG]
#
# Default IMAGE_TAG is slopstop-rag:latest (the tag `make rag-build` produces).
#
# Two tiers of checks:
#
#   Tier 1 — STRUCTURAL (always run, no Linear credentials needed):
#     The harvester module imports inside the container, the CLI is wired, and
#     identifier parsing works in-image. These gate the merge. The harvester's
#     own logic (chunking, ref extraction, sync orchestration, rate-limit
#     budget) is covered by the host pytest layer (tests/test_harvesters_common
#     .py, tests/test_linear_harvester.py) — NOT re-run here. pytest is a
#     dev-only dep absent from the runtime image; this Docker gate exists to
#     test INTEGRATION (live sync + search), not to duplicate unit tests.
#
#   Tier 2 — LIVE DOGFOOD (only when LINEAR_API_KEY is exported AND the LOU
#     workspace is reachable): actually `sync_ticket LOU-102`, assert it
#     populates ticket_chunks, then assert POST /search surfaces it. When the
#     key is absent these are reported as SKIP with a logged reason — NEVER
#     silently dropped (a silent skip would read as "covered" when it wasn't).
#
# All in-container probes go via `docker exec` (no host port publishing).

set -u

IMAGE="${1:-slopstop-rag:latest}"
CONTAINER="ticket-rag-bill37-verify"
DATA_DIR=$(mktemp -d -t bill37-pgdata.XXXXXX)
PASS=0
FAIL=0
SKIP=0

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

skip() {
    # Explicit, logged skip — keeps the "this was NOT verified" visible.
    echo "  SKIP  $1"
    echo "          reason: $2"
    SKIP=$((SKIP + 1))
}

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

echo "BILL-37 verification — image: $IMAGE"
echo "host data dir: $DATA_DIR"
echo "---"

# -------------------------------------------------------------------
# Boot
# -------------------------------------------------------------------
note "Boot — fresh-volume start"

# Pass LINEAR_API_KEY through to the container iff it's set on the host, so the
# Tier-2 live checks below can authenticate. Absent => Tier 2 skips.
DOCKER_ENV=()
if [ -n "${LINEAR_API_KEY:-}" ]; then
    DOCKER_ENV=(-e "LINEAR_API_KEY=${LINEAR_API_KEY}")
fi

docker run -d \
    --name "$CONTAINER" \
    -v "$DATA_DIR:/var/lib/postgresql" \
    ${DOCKER_ENV[@]+"${DOCKER_ENV[@]}"} \
    "$IMAGE" >/dev/null 2>&1

check "fresh-volume boot: /healthz postgres:ok AND schema:ok within 30s" \
    healthz_ok_both 30

# -------------------------------------------------------------------
# Tier 1 — structural (no credentials needed)
# -------------------------------------------------------------------
note "Tier 1 — structural (no Linear credentials needed)"

# Check — the harvester package + Linear module import cleanly in-image.
check "harvesters._common and harvesters.linear import in-container" \
    bash -c "docker exec $CONTAINER python3 -c 'import rag_service.harvesters._common, rag_service.harvesters.linear'"

# Check — the click CLI is wired (sync-ticket / sync-recent subcommands exist).
check "linear harvester CLI exposes sync-ticket and sync-recent" \
    bash -c "docker exec $CONTAINER python3 -m rag_service.harvesters.linear --help 2>&1 | grep -q 'sync-ticket' && docker exec $CONTAINER python3 -m rag_service.harvesters.linear --help 2>&1 | grep -q 'sync-recent'"

# Check — identifier parsing works in-image (cheap smoke of the module logic).
# The harvester's full logic is covered by the host pytest layer; this just
# confirms the module is importable and functional inside the runtime image.
check "parse_identifier('LOU-102') == ('LOU', 102) in-container" \
    bash -c "docker exec $CONTAINER python3 -c \"from rag_service.harvesters.linear import parse_identifier as p; assert p('LOU-102')==('LOU',102)\""

# -------------------------------------------------------------------
# Tier 2 — live dogfood (LINEAR_API_KEY + LOU workspace required)
# -------------------------------------------------------------------
note "Tier 2 — live dogfood (requires LINEAR_API_KEY + LOU read access)"

count_ticket_chunks() {
    # Echo the number of upstream linear rows for a given ticket id.
    docker exec "$CONTAINER" psql -U postgres -d postgres -tAc \
        "SELECT count(*) FROM ticket_chunks WHERE source='linear' AND ticket_id='$1' AND provenance='upstream';" 2>/dev/null
}

search_top_hit_is() {
    # POST /search for "$1"; pass iff the top result's ticket_id == "$2".
    local query="$1" want="$2"
    docker exec "$CONTAINER" python3 -c "
import json, sys, urllib.request
body = json.dumps({'query': '''$query''', 'k': 5}).encode()
req = urllib.request.Request('http://127.0.0.1:7777/search', data=body,
                            headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        results = json.loads(r.read().decode()).get('results', [])
except Exception as e:
    print('search failed:', e); sys.exit(1)
if not results:
    print('no results'); sys.exit(1)
top = results[0].get('ticket_id')
print('top hit:', top)
sys.exit(0 if top == '$want' else 1)
"
}

if [ -z "${LINEAR_API_KEY:-}" ]; then
    skip "sync_ticket('LOU-102') populates ticket_chunks" \
         "LINEAR_API_KEY not set — export a Linear key with LOU read access to run the live dogfood checks"
    skip "POST /search 'multicol-breaking-001 pixel shift root cause' returns LOU-102 as top hit" \
         "LINEAR_API_KEY not set (depends on the sync above)"
    skip "POST /search 'finalBlockSize expansion gating' surfaces LOU-94 chunks" \
         "LINEAR_API_KEY not set (depends on the sync above)"
else
    # Acceptance: sync_ticket("LOU-102") populates ticket_chunks.
    sync_lou102() {
        docker exec "$CONTAINER" python3 -m rag_service.harvesters.linear \
            sync-ticket LOU-102 >/dev/null 2>&1 || return 1
        local n; n=$(count_ticket_chunks "LOU-102")
        [ -n "$n" ] && [ "$n" -ge 1 ]
    }
    check "sync_ticket('LOU-102') populates ticket_chunks (>=1 upstream row)" sync_lou102

    # Dogfood DoD — retrieval quality (requires the synced corpus above).
    check "POST /search 'multicol-breaking-001 pixel shift root cause' -> LOU-102 top hit" \
        bash -c "$(declare -f search_top_hit_is); CONTAINER=$CONTAINER search_top_hit_is 'multicol-breaking-001 pixel shift root cause' 'LOU-102'"

    # LOU-94 surfacing: also needs LOU-94 indexed; sync it first, then assert it
    # appears anywhere in the top results for the finalBlockSize query.
    lou94_surfaces() {
        docker exec "$CONTAINER" python3 -m rag_service.harvesters.linear \
            sync-ticket LOU-94 >/dev/null 2>&1 || return 1
        docker exec "$CONTAINER" python3 -c "
import json, sys, urllib.request
body = json.dumps({'query': 'finalBlockSize expansion gating', 'k': 10}).encode()
req = urllib.request.Request('http://127.0.0.1:7777/search', data=body,
                            headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=30) as r:
    results = json.loads(r.read().decode()).get('results', [])
ids = {c.get('ticket_id') for c in results}
sys.exit(0 if 'LOU-94' in ids else 1)
"
    }
    check "POST /search 'finalBlockSize expansion gating' surfaces LOU-94 chunks" lou94_surfaces
fi

echo "---"
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
