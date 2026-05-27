#!/bin/bash
# Apply ticket-rag schema files in numerical order at cluster init.
#
# Runs from /docker-entrypoint-initdb.d/ as 01-schema.sh, AFTER
# 00-trust-auth.sh, AFTER initdb completes, BEFORE the upstream entrypoint
# starts postgres for real connections.
#
# Iterates over /docker-entrypoint-initdb.d/schema/*.sql in filename-sorted
# order and applies each via `psql --single-transaction -f`. This means
# future schema files (002_*.sql, 003_*.sql, ...) drop in next to
# 001_ticket_chunks.sql with no script change.
#
# ============================================================
# Application mechanism — Option A, deliberately
# ============================================================
# The BILL-16 design memo offers two ways to apply the schema:
#
#   Option A: init scripts under /docker-entrypoint-initdb.d/. Runs once,
#             at cluster initialization, against a fresh data volume.
#   Option B: custom container entrypoint that re-applies on every start.
#             Survives schema-file additions on existing volumes.
#
# BILL-16 ships Option A. Option B requires a custom entrypoint and is
# BILL-17's territory (entrypoint orchestration for the full service
# container, layered on top of this image). The SQL files themselves are
# fully idempotent (CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS), so when
# BILL-17 introduces an entrypoint that reapplies on every start, this
# script's contents can be reused unchanged — only the invocation point
# changes.
#
# Re-application on existing volumes is therefore explicitly out of scope
# for BILL-16; the contract is "fresh-volume bootstrap, idempotent SQL."

set -euo pipefail

SCHEMA_DIR="/docker-entrypoint-initdb.d/schema"

if [[ ! -d "$SCHEMA_DIR" ]]; then
    echo "[ticket-rag] schema dir $SCHEMA_DIR missing; nothing to apply" >&2
    exit 0
fi

shopt -s nullglob
files=("$SCHEMA_DIR"/*.sql)
shopt -u nullglob

if (( ${#files[@]} == 0 )); then
    echo "[ticket-rag] no *.sql files in $SCHEMA_DIR; nothing to apply"
    exit 0
fi

# Sort by filename so 001_ < 002_ < ... regardless of glob order.
IFS=$'\n' files=($(printf '%s\n' "${files[@]}" | sort))
unset IFS

for file in "${files[@]}"; do
    psql -U postgres -d postgres -v ON_ERROR_STOP=1 --single-transaction -f "$file"
    echo "[ticket-rag] applied schema/$(basename "$file")"
done
