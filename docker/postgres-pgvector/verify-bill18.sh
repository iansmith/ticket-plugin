#!/usr/bin/env bash
# verify-bill18.sh — BILL-18 acceptance: build pipeline (Makefile entries,
# layer cache, image-size docs, cleanup), end-to-end.
#
# Usage:
#   bash docker/postgres-pgvector/verify-bill18.sh
#
# Slower than verify-bill17.sh — runs two full `make rag-build` invocations
# plus delegates `make rag-run` into the BILL-17 smoke test. Expect ~5-7 min
# wall-clock when GREEN.
#
# Must be run from the repo root (relative paths to Makefile / README / app).

set -u

PASS=0
FAIL=0

# --------------------------------------------------------------------------
# Application-code backup/restore trap so the layer-cache probe doesn't leave
# the working tree dirty if the script aborts mid-check.
#
# BILL-29 relocated the FastAPI app from docker/postgres-pgvector/app/main.py
# to rag-service/rag_service/main.py; the COPY target inside the image is
# /app/rag_service/. APP_PATH and the layer-cache parser (Check 8 below) were
# updated together — they must stay in sync with the Dockerfile's final COPY.
# --------------------------------------------------------------------------
APP_PATH="rag-service/rag_service/main.py"
APP_BACKUP=""
restore_app() {
    if [ -n "$APP_BACKUP" ] && [ -f "$APP_BACKUP" ]; then
        mv "$APP_BACKUP" "$APP_PATH"
        APP_BACKUP=""
    fi
}
trap restore_app EXIT

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

note() { echo "  ----  $*"; }

echo "BILL-18 verification — full build-pipeline pass"
echo "cwd: $(pwd)"
echo "---"

# --------------------------------------------------------------------------
# Phase A — structural (fast: file presence + grep)
# --------------------------------------------------------------------------
note "Phase A — structural"

# Check 1 — Makefile exists at repo root.
check "Makefile exists at repo root" test -f Makefile

# Checks 2-4 — Makefile defines the three rag-* targets.
check "Makefile defines rag-build target" \
    bash -c "grep -qE '^rag-build:' Makefile"
check "Makefile defines rag-run target" \
    bash -c "grep -qE '^rag-run:' Makefile"
check "Makefile defines rag-clean target" \
    bash -c "grep -qE '^rag-clean:' Makefile"

# Check 5 — README has a documented image size (we'll cross-check the value
# against `docker images` after the build in Phase B).
check "README has a documented 'Image size: N GB' line" \
    bash -c "grep -qE 'Image size:[^0-9]*[0-9]+(\\.[0-9]+)?\\s*GB' docker/postgres-pgvector/README.md"

# --------------------------------------------------------------------------
# Phase B — build
# --------------------------------------------------------------------------
note "Phase B — build"

GIT_SHA=$(git rev-parse --short HEAD)

# Check 6 — `make rag-build` succeeds AND tags both slopstop/rag:latest
# and slopstop/rag:<git-sha>.
build_ok() {
    make rag-build > /tmp/bill18-build.log 2>&1 || return 1
    docker image inspect "slopstop/rag:latest"    >/dev/null 2>&1 || return 1
    docker image inspect "slopstop/rag:$GIT_SHA"  >/dev/null 2>&1 || return 1
}
check "make rag-build produces slopstop/rag:latest AND :<git-sha>" build_ok

# Check 7 — README's documented size is within ±10% of actual.
size_match() {
    local documented_gb actual_bytes
    documented_gb=$(grep -oE 'Image size:[^0-9]*[0-9]+(\.[0-9]+)?' \
                    docker/postgres-pgvector/README.md \
                    | grep -oE '[0-9]+(\.[0-9]+)?' | head -1) || return 1
    [ -z "$documented_gb" ] && return 1
    actual_bytes=$(docker image inspect slopstop/rag:latest --format '{{.Size}}' 2>/dev/null) || return 1
    python3 -c "
documented = float('$documented_gb')
actual_gb  = $actual_bytes / 1024**3
import sys
sys.exit(0 if documented * 0.9 <= actual_gb <= documented * 1.1 else 1)
"
}
check "README image size within +/-10% of actual slopstop/rag:latest" size_match

# Check 8 — layer cache: editing ONLY the FastAPI app (rag_service/main.py)
# and rebuilding hits cache for every layer up to and including the model
# COPYs; only the rag_service COPY layer rebuilds. Parses `docker build`
# output for CACHED markers.
layer_cache_ok() {
    APP_BACKUP=$(mktemp -t bill18-app.XXXXXX.py)
    cp "$APP_PATH" "$APP_BACKUP"
    echo "# bill18-layer-cache-probe" >> "$APP_PATH"

    make rag-build > /tmp/bill18-rebuild.log 2>&1
    local build_status=$?
    restore_app
    [ $build_status -ne 0 ] && return 1

    # Every stage-1 step BEFORE the "COPY app/" line must be a cache-hit.
    # BuildKit emits two different "cache-hit" markers depending on step type:
    #   - "#N CACHED"        for COPY / RUN (and other content-producing steps)
    #   - "#N DONE 0.0s"     for FROM (already-local image) and ADD URL
    #                        (already-fetched payload)
    # Either counts as cache-hit. A step that actually ran would emit
    # "#N DONE <non-zero>s" with output between the header and DONE.
    #
    # Why we key on the stage-1 SLOT index ("[stage-1 X/Y]"), not the "#N" id:
    # BuildKit assigns a FRESH "#N" to a step's resolution probe AND to its
    # actual execution/cache emission. The ADD of libgomp1.deb from
    # snapshot.debian.org is the troublemaker — BuildKit intermittently
    # re-validates the remote URL, emitting BOTH
    #     "#10 [stage-1 4/13] ADD ... DONE 0.1s"   (resolution probe)
    #     "#14 [stage-1 4/13] ADD ... CACHED"      (actual cached layer)
    # Keying on "#N" would see #10 as a non-cache "DONE 0.1s" and falsely
    # report a rebuild (this made the check ~50% flaky). The slot index X/Y is
    # stable across both emissions, so we treat a slot as cached iff ANY of its
    # "#N" emissions is a cache hit.
    python3 - /tmp/bill18-rebuild.log <<'PY'
import re, sys
log = open(sys.argv[1]).read()

# Map each BuildKit step id (#N) to its stage-1 slot index (the X in
# "[stage-1 X/Y]"), and locate the application-code COPY slot.
#
# BILL-29: the final COPY is now
#     COPY rag-service/rag_service/ /app/rag_service/
# (was: COPY app/ /app/app/). Match on both source and dest so we don't
# false-positive on the earlier `COPY rag-service/requirements.txt` step
# which also starts with "COPY rag-service/".
id_to_slot = {}
app_slot = None
for line in log.splitlines():
    m = re.match(r'^#(\d+) \[stage-1\s+(\d+)/\d+\] (.*)$', line)
    if not m:
        continue
    sid, slot, body = m.group(1), int(m.group(2)), m.group(3)
    id_to_slot[sid] = slot
    if 'COPY rag-service/rag_service/' in body and '/app/rag_service/' in body:
        app_slot = slot
if app_slot is None:
    sys.exit(2)  # didn't find the app COPY layer in the log

# A slot is cached if ANY of its #N emissions is CACHED or DONE 0.0s.
cached_slots = set()
for line in log.splitlines():
    m = re.match(r'^#(\d+) CACHED', line)
    if not m:
        # "DONE 0.0s" is the FROM / ADD URL cache-hit marker. Allow 0.0s only —
        # a non-zero DONE on a slot with no CACHED emission means real work.
        m = re.match(r'^#(\d+) DONE 0\.0s', line)
    if m and m.group(1) in id_to_slot:
        cached_slots.add(id_to_slot[m.group(1)])

# Every stage-1 slot strictly before the app COPY slot must be cached.
pre_app_slots = {s for s in id_to_slot.values() if s < app_slot}
missing = sorted(pre_app_slots - cached_slots)
sys.exit(0 if not missing else 1)
PY
}
check "layer cache: editing only rag_service/main.py rebuilds only the app COPY layer" layer_cache_ok

# --------------------------------------------------------------------------
# Phase C — smoke-test integration
# --------------------------------------------------------------------------
note "Phase C — smoke-test integration"

# Check 9 — `make rag-run` invokes the BILL-17 smoke test against the freshly
# built image; smoke test reports 8/8 PASS.
check "make rag-run runs verify-bill17.sh against :latest with 0 failures" \
    bash -c "make rag-run > /tmp/bill18-run.log 2>&1 && grep -q 'Results: [0-9]\\+ passed, 0 failed' /tmp/bill18-run.log"

# --------------------------------------------------------------------------
# Phase D — cleanup
# --------------------------------------------------------------------------
note "Phase D — cleanup"

# Check 10 — `make rag-clean` removes both slopstop/rag tags and any
# leftover smoke-test container.
clean_ok() {
    make rag-clean > /tmp/bill18-clean.log 2>&1 || return 1
    # No slopstop/rag images of any tag.
    if docker images --format '{{.Repository}}' | grep -qx 'slopstop/rag'; then
        return 1
    fi
    # No smoke-test container (verify-bill17.sh's leftover name).
    if docker ps -a --format '{{.Names}}' | grep -qx 'ticket-rag-bill17-verify'; then
        return 1
    fi
    return 0
}
check "make rag-clean removes slopstop/rag images and test containers" clean_ok

echo "---"
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
