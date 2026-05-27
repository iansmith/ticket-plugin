# syntax=docker/dockerfile:1.7

# Postgres 18 + pgvector base image for the ticket-rag service container.
#
# Layers:
#   BILL-12 — postgres + pgvector base + trust-auth init script.
#   BILL-16 — ticket_chunks schema applied at cluster init.
#   BILL-14 — Python 3.12 + FastAPI runtime + placeholder /healthz app (this).
#   BILL-15 — encoder + reranker model files baked at build time.
#   BILL-17 — single entrypoint orchestrating postgres + uvicorn (future).
#
# WARNING — TRUST AUTH IS ON. Every connection method (Unix socket,
# IPv4, IPv6) is configured to accept any user without a password.
# This image is intended to run on 127.0.0.1 only, behind no network.
# Do not expose to any untrusted network.

# ----------------------------------------------------------------------
# Stage 1: Python 3.12 source. Debian 12 bookworm ships python3.11 only;
# the official python:3.12-slim-bookworm image is built on the same
# Debian 12 base, so /usr/local can be copied straight into the postgres
# image with matching glibc / OpenSSL ABIs.
# ----------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS python-stage

# ----------------------------------------------------------------------
# Stage 2: postgres+pgvector base with Python layered on top.
# ----------------------------------------------------------------------
FROM pgvector/pgvector:0.8.2-pg18

# POSTGRES_HOST_AUTH_METHOD=trust tells the upstream entrypoint to write
# host-based pg_hba entries as trust. The init script below overwrites
# pg_hba.conf wholesale to guarantee local-socket entries are also trust
# regardless of initdb defaults, and to add explicit IPv6 catch-alls.
ENV POSTGRES_HOST_AUTH_METHOD=trust

# Init script runs after initdb, before postgres starts accepting real
# connections — so the replaced pg_hba.conf applies on the post-init start.
COPY --chmod=0755 init-trust-auth.sh /docker-entrypoint-initdb.d/00-trust-auth.sh

# BILL-16 schema bootstrap (Option A): SQL files applied at cluster init by
# init-schema.sh in numerical order. The 01- prefix ensures it runs after
# 00-trust-auth.sh. Future migrations land as additional schema/NNN_*.sql.
# BILL-17 will add an entrypoint that re-applies on every start; the SQL is
# already idempotent for that future use.
COPY schema/ /docker-entrypoint-initdb.d/schema/
COPY --chmod=0755 init-schema.sh /docker-entrypoint-initdb.d/01-schema.sh

# Python 3.12 from the multi-stage source. /usr/local holds the interpreter,
# pip, and site-packages — copying it whole gives us a working Python without
# pulling deadsnakes or building from source.
COPY --from=python-stage /usr/local /usr/local
# Library dependency sentence-transformers needs at runtime; rest of the
# base image's apt state is left untouched.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Sanity-check that the Python copy is functional before pip runs.
RUN python3 --version && pip3 --version

# Pinned runtime deps. requirements.txt edits bust this layer; app code
# edits below do not.
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# BILL-15 — encoder + reranker baked at image-build time.
#
# Models are pre-fetched on the host into ./models/ by fetch-models.sh
# (pinned commit SHAs live there) and COPYed in here. Downloading inside
# the build via huggingface-cli stalled reliably on the Xet protocol path
# through Docker Desktop's VM NAT; host-side download bypasses that.
#
# onnx/ excluded from bge-m3 (~2.3 GB ONNX data, unused by sentence-transformers).
# imgs/ and assets/ excluded (documentation images only).
# NOTE: fp32 weights add ~4.54 GB; expected total image ~6 GB.
# See BILL-13 umbrella for the measured size.
COPY models/bge-m3 /models/bge-m3
COPY models/bge-reranker-v2-m3 /models/bge-reranker-v2-m3

ENV HF_HUB_OFFLINE=1

# Placeholder FastAPI app. Real endpoints land in future tickets.
COPY app/ /app/app/

EXPOSE 5432
# BILL-14: FastAPI listens on 127.0.0.1:7777 per design doc. BILL-17 will
# add an entrypoint that starts uvicorn alongside postgres; for now the
# user runs `docker exec -d <c> python3 -m uvicorn app.main:app
# --host 127.0.0.1 --port 7777` manually after `docker run`.
EXPOSE 7777

# CMD and ENTRYPOINT are inherited from pgvector/pgvector:pg18, which
# inherits the standard postgres entrypoint. SIGTERM → fast shutdown is
# already wired up upstream.
