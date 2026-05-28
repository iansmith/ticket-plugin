"""Unit tests for rag_service.embed.

Layer-1 tests (pure, no FastAPI involved) and a Layer-1 live-model test
that auto-skips when the real bge-m3 weights aren't on disk — they're only
present inside the rag image, not in a typical dev environment. The
in-container model exercise is owned by verify-bill17.sh; this file's job
is to lock in the *API surface* and the offline-friendly bits.

Per design/rag-service-testing.md: pytest must not load the real model in
the normal dev loop. The live test below is opt-in via filesystem presence.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from rag_service import embed
from rag_service.embed import MODEL_PATH, Embedder, get_embedder


# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------


def test_model_path_defaults_to_baked_in_container_path():
    """Outside the container the env override is unset, so MODEL_PATH must be
    the BILL-15 baked-in path. Guards against accidental hard-coding drift.
    """
    if "RAG_SERVICE_BGE_M3_PATH" in os.environ:
        pytest.skip("RAG_SERVICE_BGE_M3_PATH set; default-path test not applicable")
    assert MODEL_PATH == "/models/bge-m3"


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------


def test_get_embedder_returns_cached_singleton(monkeypatch):
    """get_embedder() must construct the Embedder once and return the same
    instance on subsequent calls. We swap Embedder for a counting stand-in
    so the test doesn't load the real model.
    """
    monkeypatch.setattr(embed, "_embedder", None)

    construct_count = {"n": 0}

    class _Counting:
        def __init__(self):
            construct_count["n"] += 1

    monkeypatch.setattr(embed, "Embedder", _Counting)

    a = get_embedder()
    b = get_embedder()
    assert a is b
    assert construct_count["n"] == 1


# ---------------------------------------------------------------------------
# Live model — auto-skip when weights absent
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.path.isdir(MODEL_PATH),
    reason=f"bge-m3 weights not present at {MODEL_PATH}; live model test only "
    "runs inside the rag image (verify-bill17.sh covers the in-container path).",
)
def test_encode_query_and_passage_return_1024d_float_arrays():
    e = Embedder()
    q = e.encode_query("how does the scheduler work")
    p = e.encode_passage("the scheduler dispatches jobs every N seconds")
    for v in (q, p):
        assert isinstance(v, np.ndarray)
        assert v.shape == (1024,)
        assert v.dtype.kind == "f"
