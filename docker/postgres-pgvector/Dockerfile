# syntax=docker/dockerfile:1.7

# Postgres 18 + pgvector base image for the ticket-rag service container.
#
# This is the database-only building block (BILL-12) of the larger
# ticket-rag service container (BILL-13 umbrella). Python/FastAPI, model
# bake-in, and entrypoint orchestration layer on top via BILL-14 / BILL-15
# / BILL-17.
#
# WARNING — TRUST AUTH IS ON. Every connection method (Unix socket,
# IPv4, IPv6) is configured to accept any user without a password.
# This image is intended to run on 127.0.0.1 only, behind no network.
# Do not expose to any untrusted network.

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

EXPOSE 5432

# CMD and ENTRYPOINT are inherited from pgvector/pgvector:pg18, which
# inherits the standard postgres entrypoint. SIGTERM → fast shutdown is
# already wired up upstream.
