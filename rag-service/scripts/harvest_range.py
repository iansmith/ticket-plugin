"""Ingest a contiguous range of Linear tickets through the real spine.

Unlike the per-ticket CLI (`sync-ticket`), this builds the client, DB
connection, and embedder ONCE and reuses them across the range — so the model
weights load a single time instead of once per ticket. Same code path otherwise:
it calls the public `sync_ticket()` for each identifier.

Usage (inside the container):
    python3 -m scripts.harvest_range LOU 1 100
"""

from __future__ import annotations

import sys

from rag_service.embed import get_embedder
from rag_service.harvesters.linear import (
    _build_real_client,
    _open_conn,
    parse_identifier,
    sync_ticket,
)


def main() -> int:
    prefix, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    parse_identifier(f"{prefix}-{lo}")  # validate prefix is well-formed once
    client = _build_real_client()
    embedder = get_embedder()
    conn = _open_conn()
    total_rows = 0
    found = 0
    missing = 0
    try:
        for n in range(lo, hi + 1):
            ident = f"{prefix}-{n}"
            rows = sync_ticket(ident, client=client, conn=conn, embedder=embedder)
            if rows > 0:
                found += 1
                total_rows += rows
                print(f"{ident}: {rows} rows", flush=True)
            else:
                missing += 1
                print(f"{ident}: (no rows — not found or empty)", flush=True)
    finally:
        conn.close()
    print(
        f"DONE: {found} tickets ingested, {missing} empty/missing, "
        f"{total_rows} total chunk rows",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
