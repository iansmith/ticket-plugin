"""Placeholder FastAPI app for the ticket-rag service (BILL-14).

Only /healthz is wired up. Real endpoints land in future tickets. The point
of this module is to prove that Python + FastAPI + a postgres connection
work inside the container.

Trust auth: connects as `postgres` over localhost with no password. See
init-trust-auth.sh in this directory for the auth configuration.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import psycopg

app = FastAPI()

# Short connect timeout so the degraded path responds well under 2 seconds
# even if postgres is fully down (ticket acceptance criterion).
_CONN_STR = "dbname=postgres user=postgres host=localhost connect_timeout=1"


@app.get("/healthz")
def healthz():
    try:
        with psycopg.connect(_CONN_STR) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except psycopg.OperationalError:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "postgres": "unreachable"},
        )
    return {"status": "ok", "postgres": "reachable"}
