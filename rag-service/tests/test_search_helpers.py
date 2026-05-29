"""Layer-1 unit tests for the pure search-pipeline helpers.

No FastAPI, no DB, no model loads — direct function calls on
rag_service.search.rank_and_trim and rag_service.db._build_knn_sql.
Per design/rag-service-testing.md, this is the cheapest, fastest layer
and should hold the bulk of the logic assertions.
"""

from __future__ import annotations

from rag_service.db import _build_knn_sql
from rag_service.models import Chunk, SearchFilters
from rag_service.search import rank_and_trim


def _chunk(id: int, text: str, score: float = 0.0) -> Chunk:
    return Chunk(
        id=id,
        text=text,
        score=score,
        source="github",
        provenance="upstream",
        kind="comment",
        ticket_id="iansmith/slopstop#17",
    )


class _RecordingReranker:
    """Returns a fixed score per passage and records that it was called."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self.called = False

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.called = True
        return self._scores[: len(passages)]


# ---------------------------------------------------------------------------
# rank_and_trim
# ---------------------------------------------------------------------------


def test_rank_and_trim_preserves_order_when_rerank_false():
    candidates = [_chunk(1, "a"), _chunk(2, "b"), _chunk(3, "c")]
    reranker = _RecordingReranker([9, 9, 9])
    out = rank_and_trim(candidates, "q", reranker, k=3, rerank=False)
    assert [c.id for c in out] == [1, 2, 3]
    assert reranker.called is False  # reranker must NOT be touched


def test_rank_and_trim_reorders_by_rerank_score():
    candidates = [_chunk(1, "a"), _chunk(2, "b"), _chunk(3, "c")]
    # Reranker prefers candidate 3 > 1 > 2.
    reranker = _RecordingReranker([0.5, 0.1, 0.9])
    out = rank_and_trim(candidates, "q", reranker, k=3, rerank=True)
    assert [c.id for c in out] == [3, 1, 2]
    assert reranker.called is True
    # The returned chunks carry the rerank scores, not the originals.
    assert out[0].score == 0.9


def test_rank_and_trim_empty_input_returns_empty():
    reranker = _RecordingReranker([])
    assert rank_and_trim([], "q", reranker, k=10, rerank=True) == []
    assert reranker.called is False  # short-circuit before calling reranker


def test_rank_and_trim_caps_at_k():
    candidates = [_chunk(i, f"c{i}") for i in range(10)]
    reranker = _RecordingReranker([float(i) for i in range(10)])
    out = rank_and_trim(candidates, "q", reranker, k=3, rerank=True)
    assert len(out) == 3
    # Highest scores are the last indices (score == i), so ids 9, 8, 7.
    assert [c.id for c in out] == [9, 8, 7]


def test_rank_and_trim_stable_on_tied_scores():
    candidates = [_chunk(1, "a"), _chunk(2, "b"), _chunk(3, "c")]
    reranker = _RecordingReranker([0.5, 0.5, 0.5])  # all tied
    out = rank_and_trim(candidates, "q", reranker, k=3, rerank=True)
    assert [c.id for c in out] == [1, 2, 3]  # input order preserved


# ---------------------------------------------------------------------------
# _build_knn_sql
# ---------------------------------------------------------------------------


def test_build_knn_sql_no_filters():
    vec = [0.1, 0.2, 0.3]
    sql, params = _build_knn_sql(vec, k=35, filters=None)
    assert "WHERE" not in sql
    assert "embedding <=> %s::vector" in sql
    assert sql.rstrip().endswith("LIMIT %s")
    # Bind order: score-vec, ordering-vec, k.
    assert params == [vec, vec, 35]


def test_build_knn_sql_all_filters_parameterized():
    vec = [0.0] * 3
    filters = SearchFilters(
        source=["github"],
        provenance=["upstream"],
        kind=["comment"],
        ticket_id="iansmith/slopstop#17",
    )
    sql, params = _build_knn_sql(vec, k=10, filters=filters)
    assert "source = ANY(%s)" in sql
    assert "provenance = ANY(%s)" in sql
    assert "kind = ANY(%s)" in sql
    assert "ticket_id = %s" in sql
    # No filter value is ever inlined into the SQL string (injection safety).
    assert "github" not in sql
    assert "iansmith/slopstop#17" not in sql
    # Bind order: score-vec, source, provenance, kind, ticket_id, ordering-vec, k.
    assert params == [
        vec,
        ["github"],
        ["upstream"],
        ["comment"],
        "iansmith/slopstop#17",
        vec,
        10,
    ]


def test_build_knn_sql_partial_filters_only_emit_set_clauses():
    vec = [0.0] * 3
    filters = SearchFilters(provenance=["local"])
    sql, params = _build_knn_sql(vec, k=5, filters=filters)
    assert "provenance = ANY(%s)" in sql
    assert "source = ANY(%s)" not in sql
    assert "kind = ANY(%s)" not in sql
    assert "ticket_id = %s" not in sql
    assert params == [vec, ["local"], vec, 5]
