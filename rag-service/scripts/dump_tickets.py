"""Fetch-only ticket dumper (NOT ingestion).

Pulls a range of Linear tickets via the read-only LinearGraphQLClient and emits
them as a JSON array on stdout. Deliberately bypasses the ingestion spine
(no chunking, no embedding, no DB write) — this is a corpus-capture tool for
testing retrieval quality against ground-truth ticket text held OUTSIDE the RAG
system.

Usage (inside the dev container):
    python3 -m scripts.dump_tickets LOU 100 110
"""

from __future__ import annotations

import dataclasses
import json
import sys

from rag_service.harvesters.linear import _build_real_client, parse_identifier


def main() -> int:
    prefix, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    parse_identifier(f"{prefix}-{lo}")  # validate prefix is well-formed once
    client = _build_real_client()
    out = []
    for n in range(lo, hi + 1):
        ident = f"{prefix}-{n}"
        ticket = client.fetch_ticket(ident)
        if ticket is None:
            print(f"# {ident}: not found", file=sys.stderr)
            continue
        print(f"# {ident}: {ticket.title!r} ({len(ticket.comments)} comments)", file=sys.stderr)
        out.append(dataclasses.asdict(ticket))
    json.dump(out, sys.stdout, default=str, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
