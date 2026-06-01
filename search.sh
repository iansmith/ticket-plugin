#!/usr/bin/env bash
# search.sh — interactive semantic search against the slopstop-rag dev container.
#
# Usage:
#   ./search.sh <query>
#   ./search.sh <query> --k 5        # return top 5 instead of 10
#   ./search.sh <query> --no-rerank  # skip cross-encoder rerank (faster, lower quality)
#   ./search.sh <query> --raw        # dump raw JSON
#
# Equivalent curl (for reference):
#   curl -s -X POST http://localhost:7777/search \
#        -H 'Content-Type: application/json' \
#        -d '{"query":"<query>","k":10,"rerank":true}'
#
# Requires: slopstop-rag-dev running.  Start it with:  make rag-dev-start

set -euo pipefail

DEV_CONTAINER="slopstop-rag-dev"
PORT="7777"

# ---------- parse args ----------
QUERY=""
K=10
RERANK=true
RAW=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --k)        K="$2"; shift 2 ;;
        --no-rerank) RERANK=false; shift ;;
        --raw)      RAW=true; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) QUERY="${QUERY:+$QUERY }$1"; shift ;;
    esac
done

if [ -z "$QUERY" ]; then
    echo "Usage: $0 <query> [--k N] [--no-rerank] [--raw]" >&2
    exit 1
fi

# ---------- check container ----------
if ! docker ps -q --filter "name=^${DEV_CONTAINER}$" 2>/dev/null | grep -q .; then
    echo "error: ${DEV_CONTAINER} is not running" >&2
    echo "       start it with: make rag-dev-start" >&2
    exit 1
fi

# ---------- build request ----------
BODY=$(jq -n --arg q "$QUERY" --argjson k "$K" --argjson r "$RERANK" \
    '{"query":$q,"k":$k,"rerank":$r}')

# ---------- run search ----------
if $RAW; then
    curl -s -X POST "http://localhost:${PORT}/search" \
         -H "Content-Type: application/json" -d "$BODY"
    exit 0
fi

printf 'query: "%s"  k=%d  rerank=%s\n' "$QUERY" "$K" "$RERANK"
printf -- '---\n'

curl -s -X POST "http://localhost:${PORT}/search" \
     -H "Content-Type: application/json" \
     -d "$BODY" \
  | jq -r '
      .results | to_entries[] | (
        "[\(.key + 1)] \(.value.ticket_id)  score=\(.value.score | . * 1000 | round / 1000 )  \(.value.kind)/\(.value.provenance)  seq=\(.value.seq // "?")",
        (.value.text | .[0:300]),
        ""
      )'
