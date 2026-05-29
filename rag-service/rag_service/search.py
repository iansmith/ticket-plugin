"""Pure search-pipeline business logic — no FastAPI, no DB, no model loads.

Kept separate from the endpoint glue in main.py per design/rag-service-testing.md
Rule 3 (business logic in pure functions). That makes the Stage-2 rerank-and-trim
behavior testable at Layer 1 by direct call, without standing up TestClient.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rag_service.models import Chunk

if TYPE_CHECKING:
    from rag_service.rerank import Reranker


def rank_and_trim(
    candidates: list[Chunk],
    query: str,
    reranker: Reranker | None,
    k: int,
    rerank: bool,
) -> list[Chunk]:
    """Stage-2: optionally rerank the dense-retrieval candidates, then trim to k.

    - `rerank=False` (or empty candidates): return the first `k` candidates in
      the order dense retrieval produced them — the reranker is NOT called.
    - `rerank=True`: score every candidate's text against the query via the
      cross-encoder, overwrite each chunk's `score` with the rerank score, sort
      descending, and return the top `k`.

    Ties preserve input order (Python's sort is stable), so a reranker that
    returns equal scores degrades gracefully to dense-retrieval order.

    The reranker is only required when `rerank=True`; callers may pass `None`
    when reranking is disabled.
    """
    if not candidates or not rerank:
        return candidates[:k]

    scores = reranker.score(query, [c.text for c in candidates])
    rescored = [c.model_copy(update={"score": s}) for c, s in zip(candidates, scores)]
    rescored.sort(key=lambda c: c.score, reverse=True)
    return rescored[:k]
