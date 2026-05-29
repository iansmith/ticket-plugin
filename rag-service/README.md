# rag-service

Python + FastAPI implementation of the ticket-rag service.

**Status:** Scaffolding stage. The directory structure is in place; the first child ticket of [BILL-28](https://github.com/iansmith/slopstop/issues/28) lands `pyproject.toml`, the embed/rerank/db modules, the pytest harness, and the initial `main.py`.

## Layout

```
rag-service/
├── README.md          ← this file
├── rag_service/       ← Python package (source modules)
│   └── ...            ← (populated by BILL-28 child tickets)
└── tests/             ← pytest tests, conftest.py
    └── ...
```

## How this fits

This is the application layer that runs *inside* the container shipped by [BILL-13](https://github.com/iansmith/slopstop/issues/13). The Dockerfile at [`docker/postgres-pgvector/Dockerfile`](../docker/postgres-pgvector/Dockerfile) builds the image; the entrypoint at [`docker/postgres-pgvector/entrypoint.sh`](../docker/postgres-pgvector/entrypoint.sh) launches uvicorn against `rag_service.main:app`.

Until the first child ticket of BILL-28 lands, the actual `main.py` still lives at `docker/postgres-pgvector/app/main.py` (placeholder `/healthz` only). That ticket moves it here and updates the Dockerfile's COPY path.

## Testing

**See [`design/rag-service-testing.md`](../design/rag-service-testing.md) before adding code to this directory.** It's the authoritative contract:

- Pytest + FastAPI's `TestClient` — no Docker, no postgres, no real model load for unit tests.
- Every external dependency wired via FastAPI `Depends()`, swapped in tests via `app.dependency_overrides`.
- Pure business logic stays in importable functions, tested directly.
- Docker-level end-to-end smoke tests (`verify-bill17.sh`, `verify-bill18.sh`) stay as the integration gate.

Code that doesn't follow that doc's patterns is harder to test and will get pushback in review.

## Running the service

The service runs inside the container shipped by BILL-13. To exercise it locally:

```bash
make rag-build    # from the repo root — builds slopstop/rag:latest
make rag-run      # builds (if needed) + runs the BILL-17 smoke test
make rag-clean    # removes the image and any test containers
```

Standalone (outside Docker) local-dev workflow lands with the first BILL-28 child ticket.

## See also

- [BILL-28](https://github.com/iansmith/slopstop/issues/28) — application-layer umbrella
- [`design/ticket-rag.md`](../design/ticket-rag.md) — full architecture and endpoint contracts
- [`design/rag-service-testing.md`](../design/rag-service-testing.md) — testing strategy
- [`docker/postgres-pgvector/README.md`](../docker/postgres-pgvector/README.md) — container build + run docs
