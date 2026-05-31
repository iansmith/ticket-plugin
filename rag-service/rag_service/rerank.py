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
slow and its cost grows with input length: a cross-encoder runs full
self-attention over the concatenated (query, passage) pair, so cost is
O(seq^2). Input length is therefore capped via `MAX_LENGTH` (see below) —
without that cap, scoring long ticket chunks is catastrophic (BILL-37
measured ~770 s / ~25 GB for 35 uncapped candidates vs ~28 s / ~3.3 GB at
max_length=512). Stage 1 narrows the candidate set to db.STAGE1_TOP_K before
this stage runs. Worth it because the precision improvement at the top of the
result list is large.

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

# Max input length (tokens) the cross-encoder considers per (query, passage)
# pair. CRITICAL for performance: a cross-encoder runs full self-attention over
# the concatenated pair, which is O(seq^2) in time AND memory. Without a cap,
# scoring real ticket chunks (measured up to ~16k chars / thousands of tokens)
# took ~770 s and peaked ~25 GB for 35 candidates — blowing past request
# timeouts and the container memory budget. Capping at 512 tokens drops the same
# workload to ~28 s / ~3.3 GB (measured, BILL-37 dogfood) with negligible
# ranking-quality loss: chunks are already split by logical unit, and the
# salient head of a chunk dominates relevance. Override via env for tuning.


def _parse_max_length(raw: str) -> int:
    """Parse RAG_SERVICE_RERANKER_MAX_LENGTH, rejecting non-positive values.

    A cap of 0 or negative is meaningless for the O(seq^2) cross-encoder
    (it disables or corrupts the length limit this whole module exists to
    enforce). Catch the misconfiguration at import time with a clear message
    rather than letting it surface as opaque model behavior later.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"RAG_SERVICE_RERANKER_MAX_LENGTH must be an integer, got {raw!r}"
        ) from exc
    if value <= 0:
        raise ValueError(
            f"RAG_SERVICE_RERANKER_MAX_LENGTH must be a positive integer, got {value}"
        )
    return value


MAX_LENGTH: int = _parse_max_length(
    os.environ.get("RAG_SERVICE_RERANKER_MAX_LENGTH", "512")
)


class Reranker:
    """Wraps a sentence-transformers CrossEncoder loaded from a local path.

    Constructed once per process via `get_reranker()`. Tests do NOT construct
    this — they use `FakeReranker` via `app.dependency_overrides`. See
    `design/rag-service-testing.md`.
    """

    def __init__(
        self, model_path: str = MODEL_PATH, max_length: int = MAX_LENGTH
    ) -> None:
        # Lazy import — see embed.py's same pattern + rationale.
        from sentence_transformers import CrossEncoder

        # max_length caps the O(seq^2) cross-encoder cost — see MAX_LENGTH above.
        self._model = CrossEncoder(model_path, max_length=max_length)

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


# Process-wide tokenizer singleton, used by the harvester chunker (BILL-43) to
# count tokens EXACTLY with the reranker's own tokenizer — the only counter that
# guarantees a chunk fits the reranker's MAX_LENGTH window with no truncation.
# None until the first get_reranker_tokenizer() call inside a running container.
_reranker_tokenizer = None


def get_reranker_tokenizer():
    """Lazy process-wide AutoTokenizer for the reranker model.

    Loads ONLY the tokenizer (no model weights), so it's cheap enough to call
    per chunk during the offline/cron harvest. Lives here, not in the chunker,
    so there is a single source of truth for the reranker's model path — the
    chunker must size chunks against the SAME tokenizer the reranker scores with.

    Lazy-imported like `Reranker.__init__`: tests never call this — the chunker
    injects a fake whitespace-word counter (`design/rag-service-testing.md`), so
    no tokenizer or model weights load in pytest.
    """
    global _reranker_tokenizer
    if _reranker_tokenizer is None:
        from transformers import AutoTokenizer

        _reranker_tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    return _reranker_tokenizer
