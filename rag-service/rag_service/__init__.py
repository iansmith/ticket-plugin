"""rag-service — the ticket-rag application.

A FastAPI service providing semantic search over ticket descriptions and
comments across Linear, JIRA, and GitHub. See `design/ticket-rag.md` for
the architecture and `design/rag-service-testing.md` for the binding
testing contract for everything in this package.

This package is the application layer that runs inside the container shipped
by BILL-13. The Dockerfile launches uvicorn against `rag_service.main:app`.
"""
