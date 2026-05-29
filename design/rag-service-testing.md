# rag-service testing strategy

**Status:** Draft, 2026-05-28. Authoritative for all Python+FastAPI work in `rag-service/`.

This doc is the testing contract for the ticket-rag service ([BILL-28 umbrella](https://github.com/iansmith/ticket-plugin/issues/28)). Anyone adding code to `rag-service/` — human or AI — is expected to follow these patterns. Deviations need a written justification in the relevant ticket's `findings.md`, not silently in code.

The goal: **every endpoint and every helper has unit tests that run in milliseconds on a developer's laptop, with no Docker, no postgres, no model load, no HTTP socket** — and the tests still exercise real production code paths, not mocks-of-themselves.

The repo also has Docker-level end-to-end tests (`verify-bill17.sh`, `verify-bill18.sh`) that DO spin up the container. Those stay. This doc covers the *unit-test* layer that complements them.

---

## Principles

1. **Speed.** Unit tests must run in milliseconds, not seconds. A full pytest pass should be sub-second once warm. If a test takes longer than 100ms, it's an integration test in disguise — push it into a separate group, don't let it slow down the inner loop.

2. **Real functionality.** Tests exercise the production code path, not a mock of it. If you find yourself mocking the thing under test, you're testing the wrong layer.

3. **External-dep isolation.** Postgres, model files, network calls, HuggingFace Hub, GraphQL APIs — none of these run in unit tests. They're swapped via FastAPI's `Depends`-based dependency injection.

4. **Layering.** Pick the lowest layer that exercises the behavior you want to test. Endpoint tests are slower and noisier than direct function calls; only reach for `TestClient` when you're actually testing endpoint behavior (validation, response shape, status code, routing).

5. **Docker-level smoke tests are separate.** `verify-bill17.sh` and `verify-bill18.sh` are the integration gate. Don't try to make pytest cover what those already cover.

---

## The five testing layers

### Layer 1 — Pure business logic (the bulk of tests)

For `embed.encode_query()`, `chunking.split_on_headings()`, `code_refs.extract()`, `harvesters.github.parse_comment()`, anything that's a deterministic function of its inputs:

**Don't use FastAPI's testing tools. Don't mock anything. Just import the function and call it.**

```python
# rag-service/tests/test_chunking.py
from rag_service.chunking import split_on_headings

def test_split_on_headings_splits_by_h2():
    md = "## A\nfoo content\n## B\nbar content"
    assert split_on_headings(md) == [
        ("A", "foo content"),
        ("B", "bar content"),
    ]

def test_split_on_headings_returns_empty_for_no_headings():
    assert split_on_headings("just prose, no headings") == []
```

This is where the bulk of tests live. Cheap to write, fast to run, catches the most bugs per minute of effort.

**Heuristic:** if a function doesn't take a DB connection, a model, or an HTTP client as an argument, it belongs here.

### Layer 2 — Endpoints via `fastapi.testclient.TestClient`

For testing endpoint behavior — request validation, response shape, status codes, routing, error handling — use FastAPI's bundled `TestClient`.

**`TestClient` is NOT a real HTTP client.** It uses Starlette's ASGI test infrastructure to invoke the app in-process. No socket. No port. No server. Synchronous interface (perfect with pytest).

```python
# rag-service/tests/test_search_endpoint.py
from fastapi.testclient import TestClient
from rag_service.main import app

client = TestClient(app)

def test_search_rejects_missing_query():
    r = client.post("/search", json={})
    assert r.status_code == 422  # FastAPI's validation rejection

def test_search_returns_results_with_score():
    r = client.post("/search", json={"query": "scheduler", "k": 5})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert all("score" in chunk for chunk in body["results"])
```

`TestClient` is bundled with FastAPI — no extra install required.

**Heuristic:** reach for this when the thing you're testing is "what does this endpoint do?", not "what does this helper function return?".

### Layer 3 — External-dep isolation via `app.dependency_overrides`

The previous example assumed `/search` could just answer with results, but in reality it depends on a postgres connection, an embedder, and a reranker. None of those should run in a unit test.

**FastAPI's dependency injection is the seam.** Any external dependency declared via `Depends()` in production code can be swapped for a fake at test time, without touching the production code.

The production code:

```python
# rag-service/rag_service/main.py
from fastapi import Depends, FastAPI
from rag_service.db import get_db_conn
from rag_service.embed import get_embedder
from rag_service.rerank import get_reranker

app = FastAPI()

@app.post("/search")
def search(
    req: SearchRequest,
    db = Depends(get_db_conn),
    embedder = Depends(get_embedder),
    reranker = Depends(get_reranker),
):
    vec = embedder.encode_query(req.query)
    candidates = db.knn_search(vec, k=100, filters=req.filters)
    if req.rerank:
        scores = reranker.score(req.query, [c.text for c in candidates])
        candidates = sort_by_score(candidates, scores)
    return {"results": candidates[: req.k]}
```

The test:

```python
# rag-service/tests/test_search_endpoint.py
from fastapi.testclient import TestClient
from rag_service.main import app
from rag_service.db import get_db_conn
from rag_service.embed import get_embedder
from rag_service.rerank import get_reranker

class FakeEmbedder:
    def encode_query(self, text): return [0.1] * 1024  # canned vector

class FakeReranker:
    def score(self, query, passages): return [0.9, 0.8, 0.7][: len(passages)]

class FakeDB:
    def __init__(self, seeded): self.seeded = seeded
    def knn_search(self, vec, k, filters): return self.seeded[:k]

def test_search_orders_results_by_rerank_score():
    seeded = [
        Chunk(id=1, text="off-topic but high cosine"),
        Chunk(id=2, text="exact match"),
        Chunk(id=3, text="loosely related"),
    ]
    app.dependency_overrides[get_db_conn] = lambda: FakeDB(seeded)
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_reranker] = lambda: FakeReranker()
    try:
        client = TestClient(app)
        r = client.post("/search", json={"query": "thing", "k": 3, "rerank": True})
        # FakeReranker gives id=1 highest score; assert ordering
        assert [c["id"] for c in r.json()["results"]] == [1, 2, 3]
    finally:
        app.dependency_overrides.clear()
```

The `try / finally` to clear overrides is important — fixtures bleed between tests otherwise. A `conftest.py` fixture is cleaner; see "Canonical fixtures" below.

**Heuristic:** every external dependency the endpoint touches should be a `Depends()` parameter, not a module-level singleton. If it's a singleton, you can't swap it in tests, and the test has to monkey-patch globals — much uglier.

### Layer 4 — Async paths (deferred)

If we ever add async endpoints (concurrent harvester calls, streaming responses, etc.), use `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` plus `pytest-asyncio`. Same in-process pattern, async wrapper.

**Phase A endpoints stay sync.** `TestClient` handles them. Don't add `pytest-asyncio` to dev deps speculatively — wait until a specific endpoint genuinely needs async.

### Layer 5 — Lifespan / startup testing (deferred)

If a test needs to exercise FastAPI's lifespan events (e.g. "does the app refuse to start if `/models/bge-m3` is missing?"), use `asgi-lifespan`. Probably not needed early; defer until a test demands it.

---

## Code-shape rules — write code that's testable by default

These are not optional. Code that doesn't follow them is harder to test, and the tests for it will be uglier. They cost almost nothing to follow from day one and a lot to retrofit later.

### Rule 1 — Use `Depends()` for every external dependency

Anything that touches the outside world (DB, models, HTTP client, filesystem) is a `Depends()` parameter on the endpoint, not a module-level import or singleton.

```python
# YES
@app.post("/search")
def search(req: SearchRequest, db = Depends(get_db_conn)):
    ...

# NO — can't swap in tests without monkey-patching
_db = psycopg.connect(...)
@app.post("/search")
def search(req: SearchRequest):
    ...
    _db.execute(...)
```

### Rule 2 — Keep dependency providers small and pure

The function passed to `Depends()` should be tiny — just construct or return the thing. No business logic.

```python
# YES
def get_db_conn() -> Connection:
    return _pool.getconn()

# NO — business logic in the provider makes it untestable
def get_db_conn() -> Connection:
    conn = _pool.getconn()
    log_query_count(conn)
    refresh_schema_if_stale(conn)
    return conn
```

### Rule 3 — Business logic in pure functions, not endpoint bodies

The endpoint should glue dependencies together and call business-logic functions. The business logic itself should be importable and testable in isolation (Layer 1).

```python
# YES — business logic separated; endpoint is glue
def rank_candidates(candidates, query, reranker):
    scores = reranker.score(query, [c.text for c in candidates])
    return sort_by_score(candidates, scores)

@app.post("/search")
def search(req, db = Depends(...), embedder = Depends(...), reranker = Depends(...)):
    vec = embedder.encode_query(req.query)
    candidates = db.knn_search(vec, k=100, filters=req.filters)
    if req.rerank:
        candidates = rank_candidates(candidates, req.query, reranker)
    return {"results": candidates[: req.k]}

# Then test rank_candidates directly at Layer 1, no FastAPI involved.
```

### Rule 4 — Type-annotate everything that crosses a boundary

Pydantic models for request/response bodies. Type hints on function signatures. The tests benefit (type-checked fixtures are easier to keep accurate) and so does runtime validation.

### Rule 5 — One concept per test

Each test should fail for one reason. If `test_search_returns_results` is also testing rerank ordering, validation rejection, and the response shape, it's three tests jammed into one and the failure messages won't tell you what broke.

---

## Canonical fixtures (`tests/conftest.py`)

This file is what every new test starts from. The Phase-A scaffold ticket lands a version of this; future tickets extend it.

```python
# rag-service/tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from rag_service.main import app
from rag_service.db import get_db_conn
from rag_service.embed import get_embedder
from rag_service.rerank import get_reranker


class FakeEmbedder:
    """Deterministic embedder for tests. Returns a 1024-dim vector seeded by input length."""
    def encode_query(self, text: str): return [float(len(text) % 7)] * 1024
    def encode_passage(self, text: str): return [float(len(text) % 7)] * 1024


class FakeReranker:
    """Deterministic reranker for tests. Returns descending scores by passage length."""
    def score(self, query: str, passages: list[str]) -> list[float]:
        return sorted([1.0 / (1 + len(p)) for p in passages], reverse=True)


class FakeDB:
    """In-memory DB stand-in. Seed with chunks in your test."""
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
    def knn_search(self, vec, k, filters):
        return self.chunks[:k]


@pytest.fixture
def fake_db():
    return FakeDB()

@pytest.fixture
def fake_embedder():
    return FakeEmbedder()

@pytest.fixture
def fake_reranker():
    return FakeReranker()


@pytest.fixture
def client(fake_db, fake_embedder, fake_reranker):
    """A TestClient with all external deps overridden to fakes.
    Override individual fakes in your test by passing different fixtures or
    by re-overriding before calling the client."""
    app.dependency_overrides[get_db_conn] = lambda: fake_db
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_reranker] = lambda: fake_reranker
    yield TestClient(app)
    app.dependency_overrides.clear()
```

A test using this fixture set:

```python
# rag-service/tests/test_search.py
def test_search_returns_seeded_chunks(client, fake_db):
    fake_db.chunks = [Chunk(id=1, text="..."), Chunk(id=2, text="...")]
    r = client.post("/search", json={"query": "anything", "k": 10})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 2
```

The `client` fixture handles override + teardown. Test bodies stay short and focused.

---

## Dev dependencies

Goes into `rag-service/requirements-dev.txt` (or the `[project.optional-dependencies] dev` section of `pyproject.toml`):

```
pytest>=8.0
pytest-cov
```

That's it. **Do not add** the following speculatively — only when a specific test demands one:

- `pytest-asyncio` — only when an async endpoint exists (Phase A doesn't have any)
- `asgi-lifespan` — only when a test needs to exercise startup/shutdown events
- `respx` — only when a harvester test needs to mock outbound HTTP (and even then, prefer dependency-overriding the harvester's HTTP client)
- `freezegun` — only when a test needs deterministic time (and even then, prefer passing time as a parameter)

Each of these adds dependency surface and slows the dev loop. Adding one should be justified in the test's docstring.

---

## Running the tests

There are two test-execution environments, by design. Both run the **same** `rag-service/tests/` suite; they differ only in which external deps are present.

### 1. Host virtualenv — the fast inner loop (default)

A local Python venv with the light deps only — `fastapi`, `pydantic`, `numpy`, `psycopg[binary]`, plus `pytest`/`pytest-cov`. It does **not** install `torch` / `sentence-transformers` and does **not** have the ~4.5 GB of model weights (those live only in the image, baked by BILL-15). The full suite runs sub-second here. The two live-model tests (`test_embed.py::test_encode_query_and_passage_*`, `test_rerank.py::test_score_returns_float_per_passage_*`) **auto-skip** via `@pytest.mark.skipif(not os.path.isdir(MODEL_PATH))` because the weights aren't on disk — that's expected and correct.

This is the environment the whole DI-and-fakes design exists to enable: `FakeDB`/`FakeEmbedder`/`FakeReranker` stand in for postgres and the models, so the real production code paths run without either.

Create it once:

```bash
cd rag-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Run:

```bash
cd rag-service && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/
```

> **Session-local note (BILL-29 / BILL-31):** during these tickets the working venv was created at `/tmp/bill29-venv` rather than `rag-service/.venv`, to keep it out of the repo tree entirely. That path is **ephemeral and machine-local** — never committed, never referenced by any committed file, and not guaranteed to exist in a fresh session or on another machine. Any agent or developer should recreate a venv as above (the `.venv/` form is `.gitignore`-covered) rather than assume `/tmp/bill29-venv` is present. The `**Test command:**` line a ticket's `task_plan.md` records is the authoritative invocation for that ticket; if it names a `/tmp` path, treat that as "a venv with the deps installed," not a literal requirement.

### 2. Inside the image — the high-fidelity check

Runs the same suite where `torch`, `sentence-transformers`, the real models, and `pgvector` all exist, so the live-model tests actually execute (~11 s total instead of sub-second):

```bash
docker run --rm -v "$PWD/rag-service:/work" -w /work --entrypoint bash \
  ticket-plugin/rag:latest -c "PYTHONPATH=/work python -m pytest tests/"
```

Use the host venv for the edit-run-edit loop; run the in-image pass before opening a PR (and the Docker-level `verify-bill17.sh` / `verify-bill18.sh` gates remain the integration source of truth).

---

## Anti-patterns — don't

- **Don't `monkeypatch` module-level globals to swap dependencies.** Use `app.dependency_overrides`. The override mechanism is built for this; monkeypatch is a leaky workaround.
- **Don't write tests that spin up postgres.** That's what `verify-bill17.sh` does. Unit tests use the `FakeDB` (or whatever you seed via `dependency_overrides`).
- **Don't write tests that load real models.** Model load takes 2-5 seconds; doing it per-test destroys the inner loop. Use `FakeEmbedder` / `FakeReranker`. If you need to verify the *real* model's behavior, that's a Layer 5 smoke test under `verify-billN.sh`, not a unit test.
- **Don't write tests that hit live APIs** (GitHub, Linear, JIRA). Harvester tests should pass canned JSON fixtures into the parsing code. If you must test the HTTP layer specifically, mock the HTTP client via `dependency_overrides`, not via global patching.
- **Don't write a "TestSearch" class with shared state between tests.** Pytest's function-style tests with per-test fixtures avoid the entire category of inter-test bleed bugs.
- **Don't write tests longer than ~20 lines.** If a test needs more setup than that, the production code probably needs decomposing.
- **Don't skip writing a test because "it's hard to test."** That's the strongest signal that the production code is structured badly. Fix the structure first.

---

## When to deviate

These principles are defaults, not absolutes. Deviation is justified when:

1. A specific bug or class of bugs CAN'T be caught at a lower layer. (E.g. a race condition that only manifests under real postgres locking — gets a Layer 5 integration test under `verify-billN.sh`.)
2. A production code path is genuinely async and the sync `TestClient` doesn't exercise it correctly.
3. A test discovers that the dependency-injection shape needs to change for testability. Update the production code; don't work around it in the test.

Each deviation is a one-paragraph note in the relevant ticket's `findings.md` explaining why.

---

## See also

- [BILL-28](https://github.com/iansmith/ticket-plugin/issues/28) — application-layer umbrella; this doc is its testing contract.
- [`design/ticket-rag.md`](ticket-rag.md) — full architecture and endpoint contracts.
- [`docker/postgres-pgvector/verify-bill17.sh`](../docker/postgres-pgvector/verify-bill17.sh), [`docker/postgres-pgvector/verify-bill18.sh`](../docker/postgres-pgvector/verify-bill18.sh) — the existing Docker-level end-to-end tests this layer complements.
- FastAPI testing docs: [https://fastapi.tiangolo.com/tutorial/testing/](https://fastapi.tiangolo.com/tutorial/testing/)
- FastAPI dependency-overrides docs: [https://fastapi.tiangolo.com/advanced/testing-dependencies/](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
