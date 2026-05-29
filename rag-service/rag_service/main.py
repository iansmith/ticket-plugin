"""FastAPI app for the ticket-rag service.

Endpoints:
- GET  /healthz — liveness/readiness (postgres reachability + schema presence).
- POST /search  — dense retrieval + optional cross-encoder rerank (BILL-31).

/healthz is expected to be polled at Docker-healthcheck cadence (~1/min). Each
call does one tiny `SELECT 1` + one `SELECT to_regclass(...)` round-trip; this
is fine at one-per-minute. Do not poll in tight loops.

Per design/rag-service-testing.md, every external resource is reached through a
FastAPI dependency provider (get_db_conn / get_embedder / get_reranker) so tests
can swap them via app.dependency_overrides without monkey-patching globals. The
endpoint bodies stay thin glue; the rerank-and-trim logic lives in the pure
rag_service.search.rank_and_trim helper, tested directly at Layer 1.
"""

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from rag_service.db import DB, STAGE1_TOP_K, get_db_conn
from rag_service.embed import Embedder, get_embedder
from rag_service.models import SearchRequest, SearchResponse
from rag_service.rerank import Reranker, get_reranker
from rag_service.search import rank_and_trim

app = FastAPI()


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
    vec = embedder.encode_query(req.query)
    candidates = db.knn_search(vec, k=STAGE1_TOP_K, filters=req.filters)
    results = rank_and_trim(
        candidates, req.query, reranker, k=req.k, rerank=req.rerank
    )
    return SearchResponse(results=results)
