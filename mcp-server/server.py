"""
slopstop RAG MCP server — BILL-50

Exposes the slopstop RAG service (POST /search, GET /healthz) as MCP tools
for Claude Code. Runs as a stdio server; Claude Code launches it automatically
via .mcp.json at the project root.

Configuration
-------------
RAG_SERVICE_URL   Base URL of the running RAG container.
                  Default: http://localhost:7777

Usage
-----
Normally started automatically by Claude Code via .mcp.json.
To test manually:

    python3 mcp-server/server.py
    # then type MCP JSON-RPC messages on stdin
"""

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

RAG_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:7777").rstrip("/")

mcp = FastMCP(
    "slopstop-rag",
    instructions=(
        "Semantic search over the slopstop/LOU ticket corpus. "
        "Call search_tickets with a natural-language query to retrieve "
        "ranked ticket chunks. Use rag_health to check whether the "
        "RAG dev container is running before a search."
    ),
)


# ---------------------------------------------------------------------------
# search_tickets
# ---------------------------------------------------------------------------

@mcp.tool()
def search_tickets(
    query: str,
    project: str = "",
    k: int = 10,
    rerank: bool = True,
    source: list[str] | None = None,
    provenance: list[str] | None = None,
    kind: list[str] | None = None,
    ticket_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search the slopstop ticket corpus using semantic similarity.

    Returns up to `k` ranked chunks, most-relevant first. Each chunk
    contains: id, text, score, source, provenance, kind, ticket_id,
    seq (optional), author (optional).

    Args:
        query:      Natural-language search query (required).
        project:    Restrict to one project prefix, e.g. "LOU" or "BILL".
                    Empty string (default) searches all projects.
        k:          Maximum number of results to return (default 10).
        rerank:     Enable cross-encoder reranking for higher precision
                    (default True; set False for faster but coarser results).
        source:     Filter by source list, e.g. ["linear"]. None = all.
        provenance: Filter by provenance list, e.g. ["upstream"]. None = all.
        kind:       Filter by chunk kind, e.g. ["description", "comment"].
                    None = all.
        ticket_id:  Filter to a single ticket, e.g. "LOU-94". None = all.
    """
    filters: dict[str, Any] | None = None
    if any(v is not None for v in (source, provenance, kind, ticket_id)):
        filters = {}
        if source is not None:
            filters["source"] = source
        if provenance is not None:
            filters["provenance"] = provenance
        if kind is not None:
            filters["kind"] = kind
        if ticket_id is not None:
            filters["ticket_id"] = ticket_id

    body: dict[str, Any] = {
        "query": query,
        "project": project,
        "k": k,
        "rerank": rerank,
    }
    if filters is not None:
        body["filters"] = filters

    try:
        resp = httpx.post(f"{RAG_URL}/search", json=body, timeout=30.0)
        resp.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach RAG service at {RAG_URL}. "
            "Is the slopstop-rag-dev container running? "
            "Start it with: make rag-dev-start"
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"RAG service returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )

    return resp.json()["results"]


# ---------------------------------------------------------------------------
# rag_health
# ---------------------------------------------------------------------------

@mcp.tool()
def rag_health() -> dict[str, str]:
    """Check whether the slopstop RAG service is up and healthy.

    Returns a dict with keys "postgres" and "schema", each "ok" when healthy.
    Raises a RuntimeError with a helpful message when the container is not
    running.
    """
    try:
        resp = httpx.get(f"{RAG_URL}/healthz", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach RAG service at {RAG_URL}. "
            "Start it with: make rag-dev-start"
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"RAG service returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
