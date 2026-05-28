"""Reranker for the rag-service: wraps the bge-reranker-v2-m3 cross-encoder.

This is **Stage 2 of the search pipeline** (Stage 1 is dense retrieval via
`db.knn_search`; see `design/ticket-rag.md` "Retrieval pipeline" for the full
shape). For each `(query, candidate.text)` pair, `score()` returns a
relevance score; callers sort by score descending to get the final ordering.

# Why a separate model from the encoder

The encoder (bge-m3) produces fixed-size vectors that compare quickly via
cosine distance — that's what makes Stage 1 fast. The reranker is a
cross-encoder: it looks at the query and the candidate's text TOGETHER as
one transformer input and produces a relevance score. That joint attention
gives qualitatively better ranking than vector-only comparison, but it's
slow — ~100 ms per pair on CPU. Stage 1 narrows the candidate set to ~100
before this stage runs; even so, a full rerank against 100 pairs takes
~10 s. Worth it because the precision improvement at the top of the result
list is large.

# Lazy loading

Same pattern as `embed.py`: the heavy `import sentence_transformers` happens
inside `Reranker.__init__`, not at module import. Tests use `FakeReranker`
via `app.dependency_overrides` and never trigger the real constructor. See
`design/rag-service-testing.md`.
"""

from __future__ import annotations

import os

# Default to the in-container model path baked in by BILL-15. Override the
# env var for local dev outside Docker (pre-fetch via fetch-models.sh first).
_DEFAULT_MODEL_PATH = "/models/bge-reranker-v2-m3"
MODEL_PATH: str = os.environ.get("RAG_SERVICE_BGE_RERANKER_PATH", _DEFAULT_MODEL_PATH)


class Reranker:
    """Wraps a sentence-transformers CrossEncoder loaded from a local path.

    Constructed once per process via `get_reranker()`. Tests do NOT construct
    this — they use `FakeReranker` via `app.dependency_overrides`. See
    `design/rag-service-testing.md`.
    """

    def __init__(self, model_path: str = MODEL_PATH) -> None:
        # Lazy import — see embed.py's same pattern + rationale.
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_path)

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Score each passage's relevance to the query.

        Returns a list of floats in the same order as the input passages:
        `returns[i]` is the score for `passages[i]`. Higher scores are more
        relevant; the caller is responsible for sorting.

        Returns `[]` for an empty `passages` list — guard against calling
        the underlying `model.predict` on an empty input.
        """
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs)
        # CrossEncoder.predict returns a numpy array; convert to plain Python
        # floats so callers (and JSON serialization) don't have to deal with
        # numpy scalar types.
        return scores.tolist()


# Process-wide singleton. None until the first get_reranker() call inside
# a running container. Tests never populate this (they swap the provider).
_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    """FastAPI dependency provider. Returns the process-wide singleton,
    loading the model on first call.

    Per `design/rag-service-testing.md`, tests swap this function via
    `app.dependency_overrides` with a `FakeReranker`; the real model never
    loads in pytest.
    """
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
