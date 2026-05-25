-- ticket_chunks schema bootstrap.
--
-- Canonical source: design/ticket-rag.md, "Data model" section.
-- Ticket: BILL-16 (subtask of BILL-13, ticket-rag service container).
--
-- This file is the v1 schema for the entire ticket-rag service. It is
-- applied at cluster initialization by /docker-entrypoint-initdb.d/01-schema.sh
-- (Option A — see init-schema.sh header for rationale).
--
-- Every CREATE uses IF NOT EXISTS so re-application is a clean no-op.
-- Future schema changes land as additional 002_*.sql, 003_*.sql, etc.,
-- applied in numerical order by the same init script.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ticket_chunks (
    id            BIGSERIAL PRIMARY KEY,

    -- Identity & provenance
    source        TEXT NOT NULL,           -- 'linear' | 'jira' | 'github'
    ticket_id     TEXT NOT NULL,           -- 'MAZ-43' | 'PLTF-12' | 'iansmith/ticket-plugin#7'
    provenance    TEXT NOT NULL,           -- 'upstream' | 'local'

    -- Chunk identity within the ticket
    kind          TEXT NOT NULL,           -- 'description' | 'comment' | 'local-finding'
    seq           INT  NOT NULL,           -- order within ticket; 0 for description
    upstream_id   TEXT,                    -- source-system comment ID, if any

    -- Authorship & timing
    author        TEXT,
    created_at    TIMESTAMPTZ,
    indexed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Content
    text          TEXT NOT NULL,           -- exact text that was embedded
    embedding     vector(1024) NOT NULL,

    -- Structured signals extracted from the chunk
    code_refs     JSONB,                   -- [{file,func,module}, ...]
    ticket_refs   JSONB,                   -- ['MAZ-15', 'iansmith/mazzy#42', ...]
    raw_meta      JSONB,                   -- catch-all: labels, linked PRs, reactions

    UNIQUE (source, ticket_id, provenance, kind, seq)
);

CREATE INDEX IF NOT EXISTS ticket_chunks_embedding_idx
    ON ticket_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS ticket_chunks_ticket_idx
    ON ticket_chunks (source, ticket_id);

CREATE INDEX IF NOT EXISTS ticket_chunks_code_refs_idx
    ON ticket_chunks USING gin (code_refs);

CREATE INDEX IF NOT EXISTS ticket_chunks_ticket_refs_idx
    ON ticket_chunks USING gin (ticket_refs);
