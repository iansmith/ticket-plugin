"""Unit tests for rag_service.rerank.

Layer-1 tests (pure, no FastAPI involved) plus a live-model test that
auto-skips when the real bge-reranker-v2-m3 weights aren't on disk. Same
shape as test_embed.py — see that file's docstring for the rationale.
"""

from __future__ import annotations

import os

import pytest

from rag_service import rerank
from rag_service.rerank import MODEL_PATH, Reranker, get_reranker


# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------


def test_model_path_defaults_to_baked_in_container_path():
    if "RAG_SERVICE_BGE_RERANKER_PATH" in os.environ:
        pytest.skip(
            "RAG_SERVICE_BGE_RERANKER_PATH set; default-path test not applicable"
        )
    assert MODEL_PATH == "/models/bge-reranker-v2-m3"


# ---------------------------------------------------------------------------
# Empty-passages short-circuit
# ---------------------------------------------------------------------------


def test_score_returns_empty_list_for_empty_passages():
    """score([]) must return [] WITHOUT touching the underlying model — the
    real CrossEncoder.predict raises on an empty input. We bypass __init__
    via object.__new__ so this test doesn't need the real model on disk.
    """
    r = object.__new__(Reranker)
    # Trip-wire: if score forgets the short-circuit and calls into _model,
    # this attribute makes the failure mode loud and obvious instead of
    # AttributeError several frames deep.
    r._model = None
    assert r.score("anything", []) == []


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------


def test_get_reranker_returns_cached_singleton(monkeypatch):
    monkeypatch.setattr(rerank, "_reranker", None)

    construct_count = {"n": 0}

    class _Counting:
        def __init__(self):
            construct_count["n"] += 1

    monkeypatch.setattr(rerank, "Reranker", _Counting)

    a = get_reranker()
    b = get_reranker()
    assert a is b
    assert construct_count["n"] == 1


# ---------------------------------------------------------------------------
# Live model — auto-skip when weights absent
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.path.isdir(MODEL_PATH),
    reason=f"bge-reranker-v2-m3 weights not present at {MODEL_PATH}; live model "
    "test only runs inside the rag image (verify-bill17.sh covers it).",
)
def test_score_returns_float_per_passage_in_input_order():
    r = Reranker()
    query = "scheduler dispatch loop"
    passages = [
        "the scheduler dispatches jobs every N seconds",
        "unrelated content about authentication flows",
        "more scheduler internals: queue draining",
    ]
    scores = r.score(query, passages)
    assert len(scores) == len(passages)
    assert all(isinstance(s, float) for s in scores)
