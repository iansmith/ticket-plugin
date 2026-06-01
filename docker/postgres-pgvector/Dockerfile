# syntax=docker/dockerfile:1.7

# Postgres 18 + pgvector base image for the ticket-rag service container.
#
# Layers:
#   BILL-12 — postgres + pgvector base + trust-auth init script.
#   BILL-16 — ticket_chunks schema (applied by the BILL-17 entrypoint, see below).
#   BILL-14 — Python 3.12 + FastAPI runtime + /healthz app.
#   BILL-15 — encoder + reranker model files baked at build time.
#   BILL-17 — single entrypoint orchestrating postgres + schema + uvicorn (this).
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
COPY --chmod=0755 docker/postgres-pgvector/init-trust-auth.sh /docker-entrypoint-initdb.d/00-trust-auth.sh

# Python 3.12 from the multi-stage source. /usr/local holds the interpreter,
# pip, and site-packages — copying it whole gives us a working Python without
# pulling deadsnakes or building from source.
COPY --from=python-stage /usr/local /usr/local
# Library dependency sentence-transformers needs at runtime: libgomp (the
# GCC OpenMP runtime). We fetch the .deb directly from snapshot.debian.org
# and `dpkg -i` it, instead of `apt-get update && apt-get install`.
#
# Why not apt-get: bookworm became Debian oldstable in 2025, and `apt-get
# update` inside BuildKit's network sandbox fails on some networks with
# "At least one invalid signature was encountered" against the standard
# bookworm mirrors. The underlying signatures verify fine outside BuildKit
# (gpgv reports GOODSIG on the same bytes), so this is a BuildKit/sandbox
# interaction rather than a real signature problem — but the result is the
# same: the build is unreproducible-from-scratch in that state. Pulling the
# single .deb we actually need via ADD bypasses apt entirely and uses
# BuildKit's own HTTP fetcher, which works.
#
# URL pinned to a snapshot.debian.org timestamp so the build is bit-stable
# regardless of future Debian point-release rotations. SHA-256 verified in
# the RUN step below so a tampered/wrong .deb is caught before install.
# Multi-arch: TARGETARCH is set by BuildKit per build platform.
ARG TARGETARCH
ADD --chmod=0644 \
    https://snapshot.debian.org/archive/debian/20251001T000000Z/pool/main/g/gcc-12/libgomp1_12.2.0-14%2Bdeb12u1_${TARGETARCH}.deb \
    /tmp/libgomp1.deb
RUN set -eu; \
    case "$(dpkg --print-architecture)" in \
        arm64) EXPECTED=a48b70dd1a95585466b40bd94564bce56ebcb2566e49ccdb6441e996aaab2098 ;; \
        amd64) EXPECTED=48fec46bda7f5b1638b9e959889bfbc20491247d402d120bb152687eb48143d7 ;; \
        *) echo "unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    ACTUAL=$(sha256sum /tmp/libgomp1.deb | awk '{print $1}'); \
    [ "$ACTUAL" = "$EXPECTED" ] || { echo "libgomp1.deb sha256 mismatch: got $ACTUAL, expected $EXPECTED" >&2; exit 1; }; \
    dpkg -i /tmp/libgomp1.deb; \
    rm /tmp/libgomp1.deb

# Sanity-check that the Python copy is functional before pip runs.
RUN python3 --version && pip3 --version

# Pinned runtime deps. requirements.txt edits bust this layer; app code
# edits below do not. Source-of-truth moved to rag-service/ in BILL-29.
COPY rag-service/requirements.txt /app/requirements.txt
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
COPY docker/postgres-pgvector/models/bge-m3 /models/bge-m3
COPY docker/postgres-pgvector/models/bge-reranker-v2-m3 /models/bge-reranker-v2-m3

ENV HF_HUB_OFFLINE=1

# BILL-16 schema files. Applied by /entrypoint.sh (BILL-17) on every start,
# idempotently — every CREATE in schema/*.sql uses IF NOT EXISTS, which is
# a hard repo convention for any file landing under this directory. Future
# migrations land as additional schema/NNN_*.sql; the entrypoint picks them
# up in filename order.
#
# Placement: AFTER the model COPYs and BEFORE the entrypoint + app COPYs.
# Schema evolves more often than models or system deps, so it sits low in the
# stack to keep those upstream layers cached when schema changes. It still
# sits above app/ — app code changes most frequently and stays last for
# maximum cache reuse per BILL-18's layer-cache acceptance.
COPY docker/postgres-pgvector/schema/ /docker-entrypoint-initdb.d/schema/

# BILL-17 entrypoint: starts postgres via the upstream entrypoint, waits for
# readiness, re-applies schema idempotently, then launches uvicorn under a
# small shell-based supervisor. SIGTERM is propagated to both children with
# pg_ctl -m fast stop on postgres. See entrypoint.sh's header for the full
# process-supervisor rationale and failure-mode notes.
#
# Placement: above the app COPY because entrypoint.sh changes less often
# than app code. Editing only app/main.py shouldn't invalidate this layer.
COPY --chmod=0755 docker/postgres-pgvector/entrypoint.sh /entrypoint.sh

# FastAPI app — currently /healthz only. Real query endpoints land in future
# tickets under the BILL-13 umbrella.
#
# BILL-29 relocated the package from docker/postgres-pgvector/app/ to
# rag-service/rag_service/. Import path inside the container is now
# rag_service.main:app (see entrypoint.sh's uvicorn invocation).
#
# Placement: LAST cacheable layer. Per BILL-18's layer-cache acceptance,
# everything above this line stays CACHED when only app code changes; only
# this COPY layer rebuilds.
COPY rag-service/rag_service/ /app/rag_service/

# Dev/ops utility scripts (fetch-only ticket dumper, etc.). Run as
# `python3 -m scripts.<name>` from /app (namespace package — no __init__.py
# needed). These are operator tools, not part of the service runtime, so they
# sit as the last cacheable layer where edits don't invalidate anything above.
COPY rag-service/scripts/ /app/scripts/

ENV PYTHONPATH=/app

EXPOSE 5432
EXPOSE 7777

ENTRYPOINT ["/entrypoint.sh"]
CMD []
