"""Endpoint contract tests for POST /search.

Originally written as Phase-0 RED tests (commit c7dca35) against a route
that didn't exist yet. During BILL-31 implementation they were updated in
two representation-only ways (the behaviors under test are unchanged):

  1. The inline `_SeedChunk` dataclass shim was replaced with the real
     `rag_service.models.Chunk`. Required because the endpoint's
     rank_and_trim() calls `chunk.model_copy(...)`, which only exists on a
     real Pydantic model.
  2. The filter-propagation assertion compares `SearchFilters.model_dump()`
     to the request dict, because FastAPI parses the JSON `filters` object
     into a `SearchFilters` model before it reaches `db.knn_search` — it
     does not arrive as a raw dict.

Layer-2 tests per design/rag-service-testing.md: TestClient +
app.dependency_overrides (via the `client` fixture). No real postgres, no
model loads.
"""

from __future__ import annotations

from typing import Any

from rag_service.models import Chunk


def _chunk(id: int, text: str, score: float = 0.0) -> Chunk:
    """Build a real Chunk with sensible metadata defaults for seeding FakeDB."""
    return Chunk(
        id=id,
        text=text,
        score=score,
        source="github",
        provenance="upstream",
        kind="comment",
        ticket_id="iansmith/ticket-plugin#17",
    )


# ---------------------------------------------------------------------------
# 1. Route exists + request validation
# ---------------------------------------------------------------------------


def test_search_rejects_missing_query_with_422(client):
    """POST /search with no `query` field must be rejected by FastAPI
    validation as 422 (not 500, not a silent default to empty query)."""
    r = client.post("/search", json={"k": 10})
    assert r.status_code == 422


def test_search_accepts_minimal_request(client, fake_db):
    """The minimal valid body — `{"query": "..."}` — must return 200 with
    the documented response shape `{"results": [...]}`. Empty corpus is
    fine; the test exercises that the route exists and the shape is right.
    """
    fake_db.chunks = []
    r = client.post("/search", json={"query": "anything"})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert body["results"] == []


# ---------------------------------------------------------------------------
# 2. Ordering — rerank=true (the default) follows the reranker
# ---------------------------------------------------------------------------


def test_search_reranks_when_rerank_true(client, fake_db, fake_reranker):
    """With rerank=true (default), the response ordering follows the
    reranker's scores, NOT the kNN candidate order. FakeReranker scores
    `1/(1+len(passage))` → shortest passage ranks highest.
    """
    fake_db.chunks = [
        _chunk(id=1, text="a much longer passage about the scheduler dispatch loop"),
        _chunk(id=2, text="short hit"),
        _chunk(id=3, text="medium-length passage"),
    ]
    r = client.post("/search", json={"query": "scheduler", "k": 3, "rerank": True})
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["results"]]
    # FakeReranker: shortest first → id=2 (9 chars), id=3 (21 chars), id=1 (longest).
    assert ids == [2, 3, 1]


# ---------------------------------------------------------------------------
# 3. Ordering — rerank=false uses db candidate order
# ---------------------------------------------------------------------------


def test_search_preserves_db_order_when_rerank_false(client, fake_db):
    """With rerank=false, the response ordering must match the order
    db.knn_search returned — no rerank invocation. The FakeDB returns
    chunks in the order they were seeded.
    """
    fake_db.chunks = [
        _chunk(id=10, text="alpha"),
        _chunk(id=20, text="beta"),
        _chunk(id=30, text="gamma"),
    ]
    r = client.post("/search", json={"query": "anything", "k": 3, "rerank": False})
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["results"]]
    assert ids == [10, 20, 30]


# ---------------------------------------------------------------------------
# 4. k caps the response length
# ---------------------------------------------------------------------------


def test_search_respects_k_cap(client, fake_db):
    """`k` is a hard upper bound on the number of results returned, even
    when the candidate pool is larger. Default k=10 per the design doc.
    """
    fake_db.chunks = [_chunk(id=i, text=f"chunk-{i}") for i in range(20)]
    r = client.post("/search", json={"query": "anything", "k": 5, "rerank": False})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 5


# ---------------------------------------------------------------------------
# 5. Filters propagate to db.knn_search
# ---------------------------------------------------------------------------


def test_search_passes_filters_to_db(client, fake_db):
    """Filters in the request body must reach db.knn_search with the right
    values. FastAPI parses the JSON `filters` object into a SearchFilters
    model; we capture it and compare its model_dump() to the request dict.
    """
    captured: dict[str, Any] = {}

    def recording_knn_search(vec, k, filters):
        captured["k"] = k
        captured["filters"] = filters
        return fake_db.chunks[:k]

    fake_db.knn_search = recording_knn_search  # type: ignore[method-assign]
    fake_db.chunks = [_chunk(id=1, text="x")]

    payload = {
        "query": "anything",
        "k": 10,
        "filters": {
            "source": ["github"],
            "provenance": ["upstream"],
            "kind": ["comment"],
            "ticket_id": "iansmith/ticket-plugin#17",
        },
        "rerank": False,
    }
    r = client.post("/search", json=payload)
    assert r.status_code == 200
    assert captured["filters"].model_dump() == payload["filters"]


# ---------------------------------------------------------------------------
# 6. STAGE1_TOP_K is the kNN candidate cap regardless of response k
# ---------------------------------------------------------------------------


def test_search_uses_STAGE1_TOP_K_as_knn_cap(client, fake_db):
    """Per BILL-29's `db.STAGE1_TOP_K = 35` and BILL-31's pipeline spec,
    Stage 1 (dense kNN) caps at STAGE1_TOP_K candidates regardless of the
    request's `k` (which only governs the final response length after
    rerank). Confirms the endpoint doesn't accidentally call knn_search
    with the user-supplied k.
    """
    captured: dict[str, Any] = {}

    def recording_knn_search(vec, k, filters):
        captured["k"] = k
        return fake_db.chunks[:k]

    fake_db.knn_search = recording_knn_search  # type: ignore[method-assign]
    fake_db.chunks = [_chunk(id=i, text=f"c-{i}") for i in range(50)]

    r = client.post("/search", json={"query": "x", "k": 10, "rerank": False})
    assert r.status_code == 200

    from rag_service.db import STAGE1_TOP_K

    assert captured["k"] == STAGE1_TOP_K
