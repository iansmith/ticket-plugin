"""Database access layer for the rag-service.

The `DB` class wraps a psycopg connection (or `None`, when postgres was
unreachable at request time). `ping()` returns a bool so endpoints can render
structured degraded responses. `has_table()` returns a bool when the table is
genuinely absent, but RAISES on query failure — those are programming or
configuration errors (bad permissions, connection died mid-request, etc.) and
should be loud, not silently degraded into "schema missing".

The `get_db_conn()` FastAPI dependency provider opens one connection per
request and tears it down on response. If postgres is unreachable, it yields
a `DB(conn=None)` instead of raising — so endpoints like /healthz can render
structured 503 bodies instead of FastAPI's default 500 traceback.

No connection pool today. The service is single-user (per
`design/ticket-rag.md`); opening one connection per request adds ~5-20 ms of
overhead, which is fine for non-throughput-critical endpoints and saves us
adding `psycopg-pool` as a runtime dep before measurements justify it. If we
ever measure search-endpoint contention or want to support concurrent
harvesters, swap in `psycopg_pool.ConnectionPool` here — the public `DB` and
`get_db_conn` interfaces don't change.

Tests do NOT instantiate `DB` or call `get_db_conn` directly — they swap the
provider via `app.dependency_overrides[get_db_conn] = lambda: FakeDB(...)`.
See `design/rag-service-testing.md` for the canonical fixture pattern.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import psycopg

from rag_service.models import Chunk, SearchFilters

if TYPE_CHECKING:
    # Runtime annotations are strings (PEP 563 via the __future__ import),
    # so numpy doesn't have to be importable at module-load time — keeps the
    # heavy import off the pytest fast path.
    import numpy as np

# DSN overridable so that local dev outside Docker can point at any postgres.
# Default matches the BILL-12/17 container config: trust auth on localhost,
# 1-second connect timeout so the /healthz degraded path responds well under
# 2 seconds when postgres is fully down.
PG_DSN: str = os.environ.get(
    "RAG_SERVICE_PG_DSN",
    "dbname=postgres user=postgres host=localhost connect_timeout=1",
)

# Stage-1 retrieval cap: how many candidates `knn_search` returns to the
# /search endpoint, which then feeds them to the reranker for Stage-2
# rescoring. The reranker takes ~100 ms per (query, passage) pair on CPU,
# so 35 candidates ≈ 3.5 s of rerank work — a deliberate latency / recall
# trade-off picked over the design doc's earlier 100-candidate suggestion.
#
# Tuning notes:
#   - Lower values reduce end-to-end search latency proportionally.
#   - Higher values give the reranker more material to consider; helps recall
#     on queries where the truly-relevant chunk has a middling dense-retrieval
#     score and would have been left out of a smaller candidate set.
#   - Hard constant by design (not env-overridable). Change it here when we
#     have measurements that justify a different number, not at runtime.
#
# As more tunable constants accumulate, extract them into a `config.py`
# module — for one knob it would be premature.
STAGE1_TOP_K: int = 35


# Columns surfaced by knn_search, in SELECT order. Kept as a module constant
# so the SQL builder and the row→Chunk mapping can't drift apart.
_CHUNK_COLUMNS = (
    "id",
    "text",
    "source",
    "provenance",
    "kind",
    "ticket_id",
    "seq",
    "author",
)


def _build_knn_sql(
    vec_list: list[float],
    k: int,
    filters: SearchFilters | None,
) -> tuple[str, list[Any]]:
    """Build the parameterized kNN SQL + ordered bind values.

    Pure function (no DB handle) so it's unit-testable at Layer 1 without a
    live postgres. Every filter value is a bound parameter — NEVER
    string-formatted into the SQL — so this is injection-safe by construction.

    Cosine distance via the `<=>` operator matches the HNSW `vector_cosine_ops`
    index from BILL-16's schema; `score = 1 - distance` so higher is more
    similar. The ORDER BY uses the raw `embedding <=> %s::vector` expression
    (NOT `ORDER BY score`) because the HNSW index is only consulted when the
    planner sees that exact distance-against-a-constant shape — hence the
    vector is bound twice (once for the score projection, once for ordering).

    Bind order: [vec (score projection), <filter params in clause order>,
    vec (ordering), k].
    """
    select_cols = ", ".join(_CHUNK_COLUMNS)
    params: list[Any] = [vec_list]  # score projection vector

    where_clauses: list[str] = []
    f = filters or SearchFilters()
    if f.source:
        where_clauses.append("source = ANY(%s)")
        params.append(f.source)
    if f.provenance:
        where_clauses.append("provenance = ANY(%s)")
        params.append(f.provenance)
    if f.kind:
        where_clauses.append("kind = ANY(%s)")
        params.append(f.kind)
    if f.ticket_id:
        where_clauses.append("ticket_id = %s")
        params.append(f.ticket_id)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = (
        f"SELECT {select_cols}, 1 - (embedding <=> %s::vector) AS score "
        f"FROM ticket_chunks{where_sql} "
        f"ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    params.append(vec_list)  # ordering vector
    params.append(k)
    return sql, params


class DB:
    """Per-request wrapper around a (possibly-absent) psycopg connection.

    Constructed by `get_db_conn()` — do not instantiate directly outside
    tests. Tests should swap the provider, not the DB class itself; see
    `design/rag-service-testing.md`.
    """

    def __init__(self, conn: psycopg.Connection | None) -> None:
        self._conn = conn

    def ping(self) -> bool:
        """True iff the underlying connection can execute a trivial query.

        Returns False if the connection was never established (postgres
        unreachable at provider time) or if a fresh `SELECT 1` fails. The
        endpoint flow is: check `ping()` first; only call other methods if
        True.
        """
        if self._conn is None:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except psycopg.Error:
            return False

    def has_table(self, name: str) -> bool:
        """True iff a table by this name exists in the `public` schema.

        Used by /healthz to verify that BILL-16's schema bootstrap (run by
        the BILL-17 entrypoint on every container start) has actually
        applied.

        Returns False ONLY for the legitimate "table genuinely not present"
        case. RAISES if the connection is missing or the query itself fails —
        those are programming or configuration bugs (caller didn't `ping()`
        first, bad permissions, connection died mid-request) and silently
        degrading them to "schema missing" would hide real problems.

        Caller contract: only call after a successful `ping()`.
        """
        if self._conn is None:
            raise RuntimeError(
                "has_table called on a disconnected DB — caller must ping() first"
            )
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass(%s) IS NOT NULL",
                (f"public.{name}",),
            )
            row = cur.fetchone()
        return bool(row and row[0])

    def knn_search(
        self,
        vec: np.ndarray,
        k: int,
        filters: SearchFilters | None = None,
    ) -> list[Chunk]:
        """Stage-1 dense retrieval: top-`k` chunks by cosine similarity to `vec`.

        `vec` is the query embedding from `Embedder.encode_query` (a 1024-dim
        numpy array). It's converted to a plain Python list before binding —
        psycopg has no native adapter for numpy arrays, and the `::vector` cast
        in the SQL turns the bound list into a pgvector value.

        `filters` narrows the candidate set (source / provenance / kind /
        ticket_id); an unset filter dimension imposes no constraint. Results
        come back ordered most-similar-first with `score = 1 - cosine_distance`.

        RAISES if the connection is absent — like `has_table`, a missing
        connection here is a caller-contract violation (search requires a live
        DB), not a degradable condition. The /search endpoint reaches this only
        through `Depends(get_db_conn)`, which yields a live connection in the
        normal path.
        """
        if self._conn is None:
            raise RuntimeError(
                "knn_search called on a disconnected DB — postgres was "
                "unreachable at request time"
            )
        sql, params = _build_knn_sql(vec.tolist(), k, filters)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        # Row order matches _CHUNK_COLUMNS + trailing score.
        return [
            Chunk(
                id=row[0],
                text=row[1],
                source=row[2],
                provenance=row[3],
                kind=row[4],
                ticket_id=row[5],
                seq=row[6],
                author=row[7],
                score=row[8],
            )
            for row in rows
        ]


def get_db_conn():
    """FastAPI dependency provider: yields a `DB` per request, closes on response.

    On `psycopg.OperationalError` (postgres unreachable at request time),
    yields a `DB(conn=None)` instead of raising. Endpoints can then call
    `db.ping()` and render structured error responses (e.g. /healthz
    returning 503 with a JSON body) rather than letting FastAPI propagate
    a 500 traceback.

    The connection has `autocommit = True` so that read-only health checks
    don't accumulate transaction state across method calls.

    Per `design/rag-service-testing.md`, tests swap this whole function via
    `app.dependency_overrides`. Tests must NOT mock `psycopg.connect`.
    """
    try:
        conn = psycopg.connect(PG_DSN)
    except psycopg.OperationalError:
        yield DB(conn=None)
        return
    conn.autocommit = True
    try:
        yield DB(conn=conn)
    finally:
        conn.close()
