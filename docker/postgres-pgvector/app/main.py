"""FastAPI app for the ticket-rag service.

Only /healthz is wired up so far. Real query endpoints land in future
tickets under the BILL-13 umbrella.

Trust auth: connects as `postgres` over localhost with no password. See
init-trust-auth.sh in this directory for the auth configuration.

/healthz reports two subsystems (postgres reachability + schema presence)
and is expected to be polled at Docker-healthcheck cadence (~1/min). Each
call does one tiny `SELECT 1` + one `SELECT to_regclass(...)` round-trip;
this is fine at one-per-minute. Do not poll in tight loops.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import psycopg

app = FastAPI()

# Short connect timeout so the degraded path responds well under 2 seconds
# even if postgres is fully down.
_CONN_STR = "dbname=postgres user=postgres host=localhost connect_timeout=1"


@app.get("/healthz")
def healthz():
    try:
        with psycopg.connect(_CONN_STR) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.ticket_chunks') IS NOT NULL")
                (schema_ok,) = cur.fetchone()
    except psycopg.OperationalError:
        return JSONResponse(
            status_code=503,
            content={"postgres": "unreachable", "schema": "unknown"},
        )

    body = {
        "postgres": "ok",
        "schema": "ok" if schema_ok else "missing",
    }
    return body if schema_ok else JSONResponse(status_code=503, content=body)
