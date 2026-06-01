"""FastAPI app for the ticket-rag service.

Endpoints:
- GET  /healthz     — liveness/readiness (postgres reachability + schema presence).
- POST /search      — dense retrieval + optional cross-encoder rerank (BILL-31).
- POST /search_note — record a search + project note to pgdata for later analysis.

/healthz is expected to be polled at Docker-healthcheck cadence (~1/min). Each
call does one tiny `SELECT 1` + one `SELECT to_regclass(...)` round-trip; this
is fine at one-per-minute. Do not poll in tight loops.

Per design/rag-service-testing.md, every external resource is reached through a
FastAPI dependency provider (get_db_conn / get_embedder / get_reranker) so tests
can swap them via app.dependency_overrides without monkey-patching globals. The
endpoint bodies stay thin glue; the rerank-and-trim logic lives in the pure
rag_service.search.rank_and_trim helper, tested directly at Layer 1.
"""

import os
import pathlib
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from rag_service.db import DB, STAGE1_TOP_K, get_db_conn
from rag_service.embed import Embedder, get_embedder
from rag_service.models import SearchFilters, SearchRequest, SearchResponse
from rag_service.query_preprocessor import preprocess_query
from rag_service.rerank import Reranker, get_reranker
from rag_service.search import rank_and_trim

app = FastAPI()

# Default notes directory (inside the pgdata volume, durable on host disk).
# Override with RAG_SERVICE_SEARCH_NOTES_DIR for tests or alternate deployments.
_DEFAULT_SEARCH_NOTES_DIR = "/var/lib/postgresql/search_notes"


def _search_notes_dir() -> pathlib.Path:
    """Return the notes directory, resolved at call time so tests can redirect
    writes via the RAG_SERVICE_SEARCH_NOTES_DIR env var without reloading the
    module or monkey-patching module-level state."""
    return pathlib.Path(
        os.environ.get("RAG_SERVICE_SEARCH_NOTES_DIR", _DEFAULT_SEARCH_NOTES_DIR)
    )


@app.get("/healthz")
def healthz(db: DB = Depends(get_db_conn)):
    if not db.ping():
        return JSONResponse(
            status_code=503,
            content={"postgres": "unreachable", "schema": "missing"},
        )

    schema_ok = db.has_table("ticket_chunks")
    body = {"postgres": "ok", "schema": "ok" if schema_ok else "missing"}
    return body if schema_ok else JSONResponse(status_code=503, content=body)


@app.post("/search", response_model=SearchResponse)
def search(
    req: SearchRequest,
    db: DB = Depends(get_db_conn),
    embedder: Embedder = Depends(get_embedder),
    reranker: Reranker = Depends(get_reranker),
) -> SearchResponse:
    """Dense retrieval → optional rerank → top-K.

    Stage 1 (dense kNN) is capped at db.STAGE1_TOP_K candidates regardless of
    the request's `k`; `k` only bounds the final response length after the
    optional Stage-2 rerank. See design/ticket-rag.md § Embedding & retrieval.
    """
    query = preprocess_query(req.query)
    vec = embedder.encode_query(query)
    # Merge top-level `project` into filters so the DB layer sees one unified
    # filter object.  Normalise to uppercase — project codes are always caps.
    filters = req.filters or SearchFilters()
    project = req.project.strip().upper()
    if project:
        filters = filters.model_copy(update={"project": project})
    candidates = db.knn_search(vec, k=STAGE1_TOP_K, filters=filters)
    results = rank_and_trim(
        candidates, query, reranker, k=req.k, rerank=req.rerank
    )
    return SearchResponse(results=results)


@app.post("/search_note", status_code=201)
def search_note(req: SearchRequest) -> dict:
    """Record a search note to pgdata for later analysis.

    Writes the project and query string to a timestamped plain-text file in
    /var/lib/postgresql/search_notes/ (the pgdata volume — durable on the host
    at pgdata/search_notes/). No retrieval is performed.

    Use this when a search doesn't return what you expect: the file captures
    enough context for offline debugging — project scope, exact query text,
    and timestamp.
    """
    notes_dir = _search_notes_dir()
    notes_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc)
    fname = notes_dir / f"search_note-{ts.strftime('%Y%m%d-%H%M%S')}.txt"
    fname.write_text(
        f"timestamp: {ts.isoformat()}\n"
        f"project:   {req.project.strip() or '(all)'}\n"
        f"query:     {req.query}\n"
    )
    return {"file": str(fname)}
