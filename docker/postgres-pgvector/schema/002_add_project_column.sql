-- Migration 002: add `project` column to ticket_chunks.
--
-- `project` is the ticket-identifier prefix — 'LOU', 'BILL', 'PLTF', etc.
-- Extracted from `ticket_id` at ingest time and stored for direct equality
-- filtering without prefix-matching on ticket_id.
--
-- Nullable: GitHub-style identifiers ('iansmith/slopstop#7') have no
-- dash-delimited prefix and are stored with project = NULL.
--
-- Backfill: extract from existing rows using a regex that matches the
-- standard '<PROJECT>-<NUMBER>' shape; non-matching rows (GitHub IDs) stay NULL.
--
-- IF NOT EXISTS / idempotent: safe to re-apply on every container start.

ALTER TABLE ticket_chunks ADD COLUMN IF NOT EXISTS project TEXT;

UPDATE ticket_chunks
   SET project = substring(ticket_id FROM '^([A-Z][A-Z0-9]+)-\d+$')
 WHERE project IS NULL;

CREATE INDEX IF NOT EXISTS ticket_chunks_project_idx
    ON ticket_chunks (project);
