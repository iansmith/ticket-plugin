"""Canonical pytest fixtures for rag-service unit tests.

This file is the seam between production code and tests. Every external
dependency declared in production via FastAPI `Depends(...)` has a fake
counterpart here and is swapped into the app through
`app.dependency_overrides`. Test bodies receive ready-to-use fakes and a
configured `TestClient`; teardown clears the overrides so fixtures don't
bleed between tests.

The patterns here are binding — see `design/rag-service-testing.md` for
the full contract. In short:

- No postgres in unit tests. `FakeDB` stands in for the real DB.
- No model loads. `FakeEmbedder` / `FakeReranker` return canned outputs.
- No monkey-patching globals. Always swap via `app.dependency_overrides`.

Fakes are deliberately minimal: just enough surface area to satisfy
current endpoints. Extend them as new endpoints are added; do NOT silently
broaden them with logic that drifts from the real implementation.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from rag_service.db import get_db_conn
from rag_service.embed import get_embedder
from rag_service.main import app
from rag_service.rerank import get_reranker


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDB:
    """In-memory stand-in for `rag_service.db.DB`.

    Tests configure behavior by setting attributes directly:

        fake_db.ping_returns = False         # simulate postgres unreachable
        fake_db.tables = {"ticket_chunks"}   # control has_table() responses
        fake_db.has_table_raises = RuntimeError(...)  # simulate caller misuse
        fake_db.chunks = [Chunk(...), ...]   # seed knn_search results

    Defaults model the happy path: ping() True, schema present, no chunks.
    """

    def __init__(self) -> None:
        self.ping_returns: bool = True
        self.tables: set[str] = {"ticket_chunks"}
        self.has_table_raises: BaseException | None = None
        # Seed knn_search results here. The default knn_search returns the
        # first `k` of these verbatim (it does NOT re-rank by the query — the
        # fake stands in for the real pgvector ordering, which the test
        # controls by seeding chunks in the order it wants them returned).
        self.chunks: list = []

    def ping(self) -> bool:
        return self.ping_returns

    def has_table(self, name: str) -> bool:
        if self.has_table_raises is not None:
            raise self.has_table_raises
        return name in self.tables

    def knn_search(self, vec, k, filters=None):
        """Return the first `k` seeded chunks. Tests that need to assert on
        the `filters` or `k` arguments override this method per-test with a
        recording stand-in (see test_search.py)."""
        return self.chunks[:k]


class FakeEmbedder:
    """Deterministic 1024-dim embedder. Vector is seeded by text length so
    different inputs produce distinguishable vectors without invoking a
    real model. Matches `rag_service.embed.Embedder` surface.
    """

    def encode_query(self, text: str) -> np.ndarray:
        return np.full(1024, float(len(text) % 7), dtype=np.float32)

    def encode_passage(self, text: str) -> np.ndarray:
        return np.full(1024, float(len(text) % 7), dtype=np.float32)


class FakeReranker:
    """Deterministic reranker. Score is `1 / (1 + len(passage))`, so shorter
    passages rank higher. Stable, easy to assert against, and avoids
    loading the real cross-encoder. Matches `rag_service.rerank.Reranker`.
    """

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 / (1 + len(p)) for p in passages]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_reranker() -> FakeReranker:
    return FakeReranker()


@pytest.fixture
def client(
    fake_db: FakeDB,
    fake_embedder: FakeEmbedder,
    fake_reranker: FakeReranker,
):
    """TestClient wired to fake dependencies.

    Mutate the fakes (e.g. `fake_db.ping_returns = False`) BEFORE the
    request to control endpoint behavior. The override is per-request via
    a fresh lambda, so changes to fake state are picked up on the next
    call.

    Teardown clears `app.dependency_overrides` so other tests start from
    a clean slate — overrides bleed between tests otherwise.
    """
    app.dependency_overrides[get_db_conn] = lambda: fake_db
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_reranker] = lambda: fake_reranker
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
