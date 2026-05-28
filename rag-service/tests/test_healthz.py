"""Unit tests for the /healthz endpoint.

Layer-2 tests (FastAPI `TestClient` + `app.dependency_overrides`) per
`design/rag-service-testing.md`. No postgres, no model loads — `FakeDB`
from conftest stands in for the real DB.

Three response paths exercised:

1. Happy path: postgres reachable + ticket_chunks present → 200,
   {postgres: ok, schema: ok}.
2. Postgres unreachable: db.ping() False → 503, {postgres: unreachable,
   schema: missing}. has_table is NOT called (db contract: only call
   after a successful ping).
3. Schema missing: ping ok but ticket_chunks absent → 503,
   {postgres: ok, schema: missing}.

The fourth combinatoric (ping True + has_table raises) is a programming
bug per the db.py contract, not an endpoint-behavior case. Not covered
here.
"""

from __future__ import annotations


def test_healthz_ok_when_postgres_reachable_and_schema_present(client, fake_db):
    # Defaults from FakeDB: ping True, tables = {"ticket_chunks"}.
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"postgres": "ok", "schema": "ok"}


def test_healthz_503_when_postgres_unreachable(client, fake_db):
    fake_db.ping_returns = False
    # If has_table is called despite ping returning False, the endpoint is
    # violating the db.py caller contract. Make that loud.
    fake_db.has_table_raises = AssertionError(
        "has_table must not be called when ping() is False"
    )

    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"postgres": "unreachable", "schema": "missing"}


def test_healthz_503_when_schema_missing(client, fake_db):
    fake_db.tables = set()  # ticket_chunks absent

    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"postgres": "ok", "schema": "missing"}
