"""FastAPI app for the ticket-rag service.

Only /healthz is wired up so far. Real query endpoints land in future
tickets under the BILL-13 umbrella.

/healthz reports two subsystems (postgres reachability + schema presence)
and is expected to be polled at Docker-healthcheck cadence (~1/min). Each
call does one tiny `SELECT 1` + one `SELECT to_regclass(...)` round-trip;
this is fine at one-per-minute. Do not poll in tight loops.

Per design/rag-service-testing.md, external resources are reached through
FastAPI dependency providers (here, get_db_conn) so tests can swap them
via app.dependency_overrides without monkey-patching globals.
"""

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from rag_service.db import DB, get_db_conn

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
