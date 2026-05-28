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

import psycopg

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
