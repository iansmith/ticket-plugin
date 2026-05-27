#!/usr/bin/env bash
# verify-bill15.sh — BILL-15 acceptance checks: model bake-in and offline load.
#
# Usage:
#   bash docker/postgres-pgvector/verify-bill15.sh [IMAGE_TAG]
#
# Default IMAGE_TAG is the pre-BILL-15 baseline so the script starts RED:
#   ticket-plugin/postgres-pgvector:bill21-after
#
# After building the BILL-15 image, pass the new tag:
#   bash docker/postgres-pgvector/verify-bill15.sh ticket-plugin/postgres-pgvector:bill15
#
# Each check uses --entrypoint /bin/sh (or --entrypoint python3) to skip
# postgres startup — all five checks complete in seconds.

IMAGE="${1:-ticket-plugin/postgres-pgvector:bill21-after}"
PASS=0
FAIL=0

check() {
    local name="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS  $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "BILL-15 verification — image: $IMAGE"
echo "---"

# Check 1: /models/bge-m3 directory exists in the image.
check "bge-m3 model directory present" \
    docker run --rm --entrypoint /bin/sh "$IMAGE" -c "test -d /models/bge-m3"

# Check 2: /models/bge-reranker-v2-m3 directory exists.
check "bge-reranker-v2-m3 model directory present" \
    docker run --rm --entrypoint /bin/sh "$IMAGE" -c "test -d /models/bge-reranker-v2-m3"

# Check 3: HF_HUB_OFFLINE=1 is set in the image environment.
check "HF_HUB_OFFLINE=1 set in image env" \
    docker run --rm --entrypoint /bin/sh "$IMAGE" -c 'test "$HF_HUB_OFFLINE" = "1"'

# Check 4: bge-m3 loads from the local path and returns a 1024-dim vector.
# Fails fast on the baseline image because /models/bge-m3 does not exist.
check "bge-m3 offline encode returns 1024-dim vector" \
    docker run --rm --entrypoint python3 "$IMAGE" -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('/models/bge-m3')
v = m.encode(['hello'])
assert len(v[0]) == 1024, f'expected 1024 dims, got {len(v[0])}'
print('dims ok')
"

# Check 5: reranker loads from the local path and scores a (query, passage) pair.
# Fails fast on the baseline image because /models/bge-reranker-v2-m3 does not exist.
check "bge-reranker-v2-m3 offline cross-encode succeeds" \
    docker run --rm --entrypoint python3 "$IMAGE" -c "
from sentence_transformers import CrossEncoder
m = CrossEncoder('/models/bge-reranker-v2-m3')
scores = m.predict([['query text', 'passage text']])
assert len(scores) == 1, f'expected 1 score, got {len(scores)}'
print('score ok')
"

echo "---"
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
