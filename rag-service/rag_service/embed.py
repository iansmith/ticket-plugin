"""Embedder for the rag-service: wraps the bge-m3 dense encoder.

Loads `BAAI/bge-m3` from a local path (no HuggingFace Hub fetch at runtime —
`HF_HUB_OFFLINE=1` in the container makes any Hub fetch a hard fail). The
`encode_query` / `encode_passage` methods produce 1024-dim numpy arrays used
to populate `ticket_chunks.embedding` at ingest time and to form the query
vector for kNN search at retrieval time.

# Asymmetric prompts

bge-m3 handles query and passage encoding identically — it does NOT require
an instruction prefix the way bge-small / bge-base / bge-large do. The two
methods `encode_query` and `encode_passage` exist as distinct call sites
anyway, so that retrieval-side code reads clearly and so a future
asymmetric model (e.g. swapping bge-m3 for bge-base) drops in without
touching harvester or search code.

# Lazy loading

The heavy `import sentence_transformers` (which pulls torch + transformers,
~2-5 s of cold-start time) happens inside `Embedder.__init__`, not at module
import. Tests use `FakeEmbedder` via `app.dependency_overrides` and never
trigger the real constructor — so pytest start-up stays fast. See
`design/rag-service-testing.md`.

The first real `get_embedder()` call inside a running container takes a few
seconds while the model loads; subsequent calls return the cached singleton.
A future ticket may add an eager preload via FastAPI lifespan startup to
avoid the first-/search-call latency hit.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported only by type checkers; runtime annotations are strings via
    # `from __future__ import annotations`, so numpy doesn't have to be
    # importable at module-load time.
    import numpy as np

# Default to the in-container model path baked in by BILL-15. Override the
# env var for local dev outside Docker (pre-fetch via fetch-models.sh first).
_DEFAULT_MODEL_PATH = "/models/bge-m3"
MODEL_PATH: str = os.environ.get("RAG_SERVICE_BGE_M3_PATH", _DEFAULT_MODEL_PATH)


class Embedder:
    """Wraps a sentence-transformers SentenceTransformer loaded from a local path.

    Constructed once per process via `get_embedder()`. Tests do NOT construct
    this — they use `FakeEmbedder` via `app.dependency_overrides`. See
    `design/rag-service-testing.md`.
    """

    def __init__(self, model_path: str = MODEL_PATH) -> None:
        # Lazy import so that simply importing this module does NOT pull in
        # torch + transformers + sentence-transformers. Tests using
        # FakeEmbedder via dependency_overrides import this module's symbols
        # but never invoke this constructor.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_path)

    def encode_query(self, text: str) -> np.ndarray:
        """Encode a query string for retrieval.

        Returns a 1024-dim float32 numpy array suitable for cosine-distance
        kNN against `ticket_chunks.embedding`.
        """
        return self._model.encode(text)

    def encode_passage(self, text: str) -> np.ndarray:
        """Encode a passage (description / comment / local-finding section)
        for storage in `ticket_chunks.embedding`.

        For bge-m3 this is functionally identical to `encode_query` — the
        model is symmetric. The method exists as a distinct call site so that
        a future asymmetric model swaps in without touching harvester or
        search code.
        """
        return self._model.encode(text)


# Process-wide singleton. None until the first get_embedder() call inside
# a running container. Tests never populate this (they swap the provider).
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """FastAPI dependency provider. Returns the process-wide singleton,
    loading the model on first call.

    Per `design/rag-service-testing.md`, tests swap this function via
    `app.dependency_overrides` with a `FakeEmbedder`; the real model never
    loads in pytest.
    """
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
