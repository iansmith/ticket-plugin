#!/usr/bin/env bash
#
# fetch-models.sh — pre-fetch BILL-15 encoder + reranker weights into ./models/
# on the HOST, so the Dockerfile can COPY them in.
#
# Why host-fetch instead of `RUN huggingface-cli download` inside the build:
#   HF's newer Xet protocol (cas-bridge.xethub.hf.co) stalled reliably inside
#   Docker Desktop's VM NAT — all parallel sockets froze simultaneously after
#   ~1 second, no timeout, no error. Direct curl from the host hit 18.5 MB/s
#   against the same URL, confirming the host network is fine. Bypassing
#   Docker's network for the download is the reliable workaround.
#
# Idempotent: re-running with existing files is a no-op (huggingface_hub
# verifies the local files match the pinned revision).

set -euo pipefail

BGE_M3_REVISION=5617a9f61b028005a4858fdac845db406aefb181
BGE_RERANKER_REVISION=953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$SCRIPT_DIR/models"

HF="${HF_CLI:-hf}"
if ! command -v "$HF" >/dev/null 2>&1; then
  echo "error: '$HF' not on PATH." >&2
  echo "  Install with: pip install --user huggingface_hub" >&2
  echo "  Or set HF_CLI=/path/to/hf if you have it elsewhere (e.g. a venv)." >&2
  exit 1
fi

mkdir -p "$MODELS_DIR"

echo "Fetching BAAI/bge-m3 @ $BGE_M3_REVISION → $MODELS_DIR/bge-m3"
"$HF" download BAAI/bge-m3 \
  --revision "$BGE_M3_REVISION" \
  --local-dir "$MODELS_DIR/bge-m3" \
  --exclude "onnx/*" \
  --exclude "imgs/*"

echo "Fetching BAAI/bge-reranker-v2-m3 @ $BGE_RERANKER_REVISION → $MODELS_DIR/bge-reranker-v2-m3"
"$HF" download BAAI/bge-reranker-v2-m3 \
  --revision "$BGE_RERANKER_REVISION" \
  --local-dir "$MODELS_DIR/bge-reranker-v2-m3" \
  --exclude "assets/*"

echo
echo "Done. Sizes:"
du -sh "$MODELS_DIR/bge-m3" "$MODELS_DIR/bge-reranker-v2-m3"
