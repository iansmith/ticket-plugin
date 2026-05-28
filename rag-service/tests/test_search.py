"""RED tests for BILL-31 — POST /search.

Written FIRST (TDD Phase 0). These tests describe the expected post-fix
behavior of the /search endpoint per the BILL-31 ticket body and
`design/ticket-rag.md` § Query API → POST /search. They fail on the
current code because /search isn't implemented yet (404 from a route
that doesn't exist).

Once BILL-31 lands, these turn green and serve as the binding contract
for the endpoint.

What's covered here (Layer-2 endpoint tests):
- Endpoint exists at POST /search (route registration).
- 422 rejection on a malformed body (FastAPI Pydantic validation).
- 200 + {results: []} on an empty corpus.
- Top-K ordering follows the reranker when rerank=true.
- Top-K ordering follows the candidate order when rerank=false.
- Filters propagate to db.knn_search verbatim.
- k caps the response length.

NOT covered here (deliberately deferred to Layer-1 tests inside the
implementation commit):
- The pure sort/trim helper. Adding a Layer-1 test for it now would
  require the helper to exist — that's a collection-time import error,
  which violates Phase 0's "tests must actually run, just fail their
  assertions" rule. The sort/trim Layer-1 tests land alongside the
  helper in the implementation.

Per `design/rag-service-testing.md`: no real postgres, no real model
loads. Uses the `client` fixture from conftest.py which already wires
FakeDB / FakeEmbedder / FakeReranker via app.dependency_overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# A minimal Chunk-shaped record. The real Chunk type lands in db.py during
# BILL-31 implementation; this test-local shape only needs `.id`, `.text`,
# and the metadata fields the endpoint will surface in the response. We
# deliberately use a plain dataclass instead of importing from production
# so the test file collects cleanly even before the production Chunk type
# exists.
@dataclass
class _SeedChunk:
    id: int
    text: str
    source: str = "github"
    provenance: str = "upstream"
    kind: str = "comment"
    ticket_id: str = "iansmith/ticket-plugin#17"
    score: float | None = None  # populated by db.knn_search in real flow


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
        _SeedChunk(id=1, text="a much longer passage about the scheduler dispatch loop"),
        _SeedChunk(id=2, text="short hit"),
        _SeedChunk(id=3, text="medium-length passage"),
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
        _SeedChunk(id=10, text="alpha"),
        _SeedChunk(id=20, text="beta"),
        _SeedChunk(id=30, text="gamma"),
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
    fake_db.chunks = [_SeedChunk(id=i, text=f"chunk-{i}") for i in range(20)]
    r = client.post("/search", json={"query": "anything", "k": 5, "rerank": False})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 5


# ---------------------------------------------------------------------------
# 5. Filters propagate to db.knn_search
# ---------------------------------------------------------------------------


def test_search_passes_filters_to_db(client, fake_db):
    """Filters in the request body must reach db.knn_search verbatim.
    We capture the filters arg on the fake and assert on it.
    """
    # Extend FakeDB inline to record the filters it was called with.
    captured: dict[str, Any] = {}

    real_knn_search = getattr(fake_db, "knn_search", None)

    def recording_knn_search(vec, k, filters):
        captured["k"] = k
        captured["filters"] = filters
        return fake_db.chunks[:k]

    fake_db.knn_search = recording_knn_search  # type: ignore[method-assign]
    fake_db.chunks = [_SeedChunk(id=1, text="x")]

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
    assert captured["filters"] == payload["filters"]


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
    fake_db.chunks = [_SeedChunk(id=i, text=f"c-{i}") for i in range(50)]

    r = client.post("/search", json={"query": "x", "k": 10, "rerank": False})
    assert r.status_code == 200

    # Import here so a missing constant doesn't break collection of other tests.
    from rag_service.db import STAGE1_TOP_K

    assert captured["k"] == STAGE1_TOP_K
