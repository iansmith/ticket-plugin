# Ticket-search RAG — Design Document

**Status:** Draft, 2026-05-24.

## Summary

A standalone, containerized RAG service that indexes the prose content of tickets — descriptions, comments, and (rarely) local supplementary notes — and exposes semantic retrieval to Claude Code via an MCP wrapper. Backends: Linear, JIRA, and GitHub Issues as first-class peers.

The motivating query class is the kind that existing ticket-system filters cannot answer: *"find tickets where there was a substantial argument in the comments,"* *"which tickets discuss the scheduler hot path,"* *"who has weighed in on the caching strategy."* JQL, Linear filters, and `gh issue list` already handle structured-metadata search (priority, status, assignee, dates) — the RAG deliberately does not duplicate that.

## Goals

- Semantic retrieval over ticket descriptions and comments across Linear (MAZ), JIRA (PLTF), and GitHub Issues (`owner/repo#N`).
- Quality of retrieval is the dominant priority. Index size and indexing throughput are secondary.
- Corpus scale: up to ~10K tickets per project. (Even with 10× growth this remains a small-corpus problem.)
- Sit as an *optional* component of `slopstop`: existing skills continue to work without it, gaining capability when it is running.
- Self-contained — a single Docker container, started locally, listening only on `127.0.0.1`.

## Non-goals

- Structured-metadata search. Already covered upstream.
- Writing to ticket systems. Strictly read-only retrieval.
- Multi-tenant deployment. Single-user, localhost-only.
- Authentication, TLS, network exposure. Out of scope by design.
- Acting as an archive of record. The ticket system is the source of truth; the RAG mirrors current state.

## Architecture

```
┌─────────────────────┐
│  Claude Code        │
│  (/slopstop:search    │◄─── MCP ────┐
│   skill)            │             │
└─────────────────────┘             │
                                    ▼
                          ┌─────────────────────┐
                          │  MCP wrapper        │
                          │  (stdio JSON-RPC)   │
                          └──────────┬──────────┘
                                     │ HTTP (127.0.0.1)
                                     ▼
                          ┌─────────────────────┐
                          │  RAG service        │
                          │  Python + FastAPI   │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  Postgres + pgvector│
                          └─────────────────────┘
                                     ▲
                                     │ ingestion
            ┌────────────────────────┼────────────────────────┐
            │                        │                        │
   ┌────────┴────────┐    ┌──────────┴────────┐    ┌─────────┴─────────┐
   │ Linear harvester│    │ JIRA harvester    │    │ GitHub harvester  │
   │   (GraphQL)     │    │   (REST)          │    │   (GraphQL)       │
   └─────────────────┘    └───────────────────┘    └───────────────────┘

       (Local content arrives via direct POST /local/sync from ticket
       skills when they write findings.md — no filesystem watcher.)
```

**Components:**

1. **Service container.** Postgres with pgvector + a Python/FastAPI process, in a single image. Models (encoder + reranker) baked in at image-build time. Postgres data on a named volume; the rest of the container is stateless.
2. **MCP wrapper.** A thin stdio process that Claude Code invokes. Translates MCP tool calls into HTTP requests to the localhost service. No business logic — protocol translation only.
3. **Harvesters.** Three pluggable ingestion modules, one per ticket system. Each owns its API rate-limit budget and can be invoked manually or on cron.
4. **Skill-driven local push.** When a ticket skill (`:document`, `:archive`, `:pause`, `:update`) writes to `findings.md`, the skill POSTs the file's current contents to `/local/sync`. The RAG parses, re-embeds, and atomically replaces the `provenance='local'` rows for that ticket. The RAG never reads the local filesystem itself. `progress.md` is never pushed (operational diary; mirrored from `:document`'s upstream-push exclusion).

The container is the unit of deployment. Everything else is configuration.

## Data model

### `ticket_chunks` (single table)

```sql
CREATE TABLE ticket_chunks (
    id            BIGSERIAL PRIMARY KEY,

    -- Identity & provenance
    source        TEXT NOT NULL,           -- 'linear' | 'jira' | 'github'
    ticket_id     TEXT NOT NULL,           -- 'MAZ-43' | 'PLTF-12' | 'iansmith/slopstop#7'
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

CREATE INDEX ticket_chunks_embedding_idx
    ON ticket_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ticket_chunks_ticket_idx
    ON ticket_chunks (source, ticket_id);

CREATE INDEX ticket_chunks_code_refs_idx
    ON ticket_chunks USING gin (code_refs);

CREATE INDEX ticket_chunks_ticket_refs_idx
    ON ticket_chunks USING gin (ticket_refs);
```

### Schema notes

- One row per logical chunk. Description = one row. Each comment = one row. Each `## Heading` section of a local `findings.md` = one row.
- `ticket_id` is system-qualified — no collisions across backends.
- `provenance` separates the two ingestion paths. Upstream re-syncs never touch local rows; local file changes never touch upstream rows.
- The `UNIQUE` constraint enables safe full re-sync per ticket: `DELETE WHERE (source, ticket_id, provenance) = (?, ?, 'upstream'); INSERT ...`.
- HNSW chosen over IVF for recall bias. With ≤100K rows in the worst case, both build cost and memory are negligible.
- GIN indexes on the structured JSONB columns make hotspot and cross-reference queries cheap.

### Why JSONB rather than child tables

`code_refs`, `ticket_refs`, and `raw_meta` could each be normalized. They are not, because:

- The shape varies by source. GitHub exposes reactions and linked PRs; Linear and JIRA don't. JIRA's metadata varies by instance configuration.
- These columns are returned alongside results, not joined in the hot path.
- JSONB + GIN lets a field be promoted to a query target later (materialized view → child table) without forcing schema migration upfront.

## Ingestion

### Two paths, one table

| Path | Owns rows where… | Triggered by |
|---|---|---|
| Upstream harvester | `provenance = 'upstream'` | Cron / manual / `/invalidate` |
| Skill-driven local push | `provenance = 'local'` | Ticket skill HTTP POST on `findings.md` write |

Neither path can clobber the other. That falls out of the schema, not from coordination logic.

### Upstream harvesters

Each harvester implements:

```
sync_ticket(ticket_id) -> None       # full re-fetch + replace for one ticket
sync_recent(since: datetime) -> int  # batch catch-up since timestamp
```

**Full re-sync per ticket** is the only correct deletion semantics:

```sql
BEGIN;
DELETE FROM ticket_chunks
 WHERE source = $1 AND ticket_id = $2 AND provenance = 'upstream';
INSERT INTO ticket_chunks (...) VALUES (...);
COMMIT;
```

Comment deleted upstream? Gone from the index on next re-sync. Comment edited? Old row gone, new row inserted. **No tombstones, no soft-delete** — that defeats the user-facing "respect deletions" policy.

### Rate-limit budgets

| System | Budget (authenticated) | Strategy |
|---|---|---|
| Linear  | 1500 GraphQL req/hr   | Batch up to 50 tickets per request; 30 batches/hr ceiling |
| JIRA Cloud | 10 req/sec per user | Throttle to 5/sec; well inside ceiling |
| GitHub  | 5000 GraphQL points/hr | Issues + comments cost 2–5 points; `first: 100` batching |

"Slow walk overnight" is `sync_recent(since=long_ago)` with a configurable sleep between batches. Default 1 req/sec — comfortably inside every system's budget.

### Tier-1 (deep coverage): worked tickets

Tickets present in `.claude/ticket-active/` and `.claude/ticket-archive/` are tickets the user has actually engaged with. They get:

- Full upstream re-sync on every harvester pass (cheap; few of them).
- Local `findings.md` indexed on file change.
- Both row types live in the same table, joinable on `(source, ticket_id)`.

### Tier-2 (breadth): historical sweep

A bulk-fetch program walks the broader project history slowly. Designed for unattended overnight runs. Indexes everything reachable; provides surrounding context so that semantic retrieval has more material to surface.

### Local ingestion

No filesystem watcher. The ticket skills know exactly when `findings.md` changes — they write it. After any write, the skill makes a single HTTP call:

```
POST /local/sync
{
  "source":      "linear",
  "ticket_id":   "MAZ-43",
  "findings_md": "<full current contents of findings.md as a string>"
}
```

The RAG parses, splits on `## Heading` sections, and atomically replaces all `provenance='local'` rows for that ticket. The skill provides the content; the RAG handles parsing + embedding + storage.

- One chunk per `## Heading` section. After the planned `:pause` / `:update` restructure (separate spec), `findings.md` is the durable home for substantive prose with content-titled sections — this granularity is natural.
- The RAG never reads the local filesystem. No path resolution, no permissions handling, no watcher daemon, no race conditions between skill writes and async readers.
- `progress.md` is never pushed — operational diary, never durable. `:document` already excludes it from upstream push; the local-push path mirrors the same policy.
- Tracking files (`CURRENT-*`) are never pushed — pure state, no prose.
- An empty `findings_md` body clears all `provenance='local'` rows for that ticket. Used implicitly when the file becomes a template-only stub or is deleted.
- If the RAG service is unreachable, the skill prints a one-line notice (`"ticket-rag service unreachable; local index not updated"`) and continues with its primary work. Ticket skills never fail because the RAG is down.

The `provenance = 'local'` channel is expected to carry near-zero volume once the skill restructure lands. Local notes should be rare; the channel exists for the exceptional case, not for routine use.

## Chunking strategy

Default: **one chunk per logical unit, never split mid-thought**:

- Description → one chunk (split on paragraph boundaries with overlap only if > 4K tokens).
- Each upstream comment → one chunk.
- Each `## Heading` in local `findings.md` → one chunk.

Fixed-token chunking is rejected. Ticket comments are arguments; splitting an argument across chunks dilutes the signal. A 200-line comment is still one logical unit because the conclusion at the end depends on the setup at the beginning.

### Code blocks: signal, not text

Diffs and code blocks are extracted but **not embedded as raw text**.

1. Strip code fences from the chunk text before embedding.
2. Parse the removed blocks for `path/to/file.ext`, function names, and module/package references.
3. Store as structured items in `code_refs`:
   ```json
   [{"file":"kmazarin/sched.go","func":"runqGet","module":"kmazarin"}, ...]
   ```
4. Synthesize a brief English sentence describing the references and append it to the to-be-embedded text:
   > *"This comment references function `runqGet` in `kmazarin/sched.go`."*
5. Embed the resulting text.

Line numbers are deliberately discarded — they go stale on the next commit. File, function, and module identifiers are stable enough to be worth keeping.

The result: semantic queries like *"tickets about the scheduler"* can still find comments that mentioned `runqGet`, *without* the embedding being polluted by literal diff syntax (`---`, `+++`, `@@`) that confuses transformer models trained on natural language.

### Cross-ticket reference extraction

Parse each chunk for ticket-ID patterns:

- `MAZ-\d+`, `PLTF-\d+` (and similar prefixed forms) — Linear / JIRA.
- `#\d+` or `owner/repo#\d+` — GitHub.

Store as normalized canonical IDs in `ticket_refs JSONB`.

Use cases:
- `WHERE ticket_refs @> '["MAZ-15"]'` — "find tickets that mention MAZ-15."
- Cross-reference signal in retrieval (a ticket that mentions another the user is currently working on is likely relevant).

## Embedding & retrieval

### Default models

- **Encoder / first-stage retrieval:** `BAAI/bge-m3`. 1024-dim dense embeddings, with optional sparse and multi-vector outputs from the same model. Enables a principled future path to hybrid retrieval (dense + lexical) without bolting two systems together.
- **Reranker:** `BAAI/bge-reranker-v2-m3`. Matched-pair design with the encoder; ~100 ms per (query, document) pair on CPU.

Both Apache-2.0 licensed. Both run entirely locally — no network calls during retrieval.

### Prompts (asymmetric)

bge-m3 is trained with explicit query/passage prompts. Use the prompts documented by the model; do not invent.

### Retrieval pipeline

For a query *Q*:

1. **Stage 1 — dense retrieval (fast, broad).** Encode *Q*. Cosine-distance query against `embedding`, with optional `WHERE` filters from the caller. Return top-100 candidates.

2. **Stage 2 — rerank (slow, accurate).** Score each `(Q, candidate.text)` pair with the cross-encoder. Sort by score; return top-K (default *K* = 10).

Stage 1 alone is the standard "vector DB" experience. It is mediocre for "find an argument" queries — too many topically similar but irrelevant hits. Stage 2 is the single highest-leverage quality lever in the system and is the reason the default `rerank=true`.

### Optional hybrid retrieval (deferred)

bge-m3 also emits sparse vectors. pgvector doesn't natively store sparse vectors, but a `sparse_embedding JSONB` column with a weighted-fusion score at retrieval time is the obvious extension. **Deferred** until quality on dense+rerank proves insufficient. Mentioned here only so the schema reservation isn't surprising later.

## Query API

REST surface, all on `127.0.0.1`, no auth.

### `POST /search`

```json
{
  "query": "...",
  "k": 10,
  "filters": {
    "source":     ["linear", "github"],   // optional, default all
    "provenance": ["upstream"],            // optional, default all
    "kind":       ["comment"],             // optional, default all
    "ticket_id":  "MAZ-43"                 // optional
  },
  "rerank": true                            // default true
}
```

Response: top-K chunks with their text, full metadata, and relevance score.

### `GET /hotspots?file=<path>`

Pure SQL, no RAG involved. Returns counts of tickets that reference the given file via `code_refs`, with the most-recent N tickets identified. The hotspot question — *"which files attract the most ticket history?"* — is also exposed via `GET /hotspots/top?limit=N`.

### `POST /invalidate` (upstream)

```json
{ "source": "linear", "ticket_id": "MAZ-43" }
```

Forces immediate re-fetch of one ticket from its upstream system. Used when the harvester cadence is too slow for the user's needs (just edited a comment they want to find right now). Touches only `provenance='upstream'` rows.

### `POST /local/sync`

```json
{
  "source":      "linear",
  "ticket_id":   "MAZ-43",
  "findings_md": "<full current contents of findings.md as a string>"
}
```

Called by ticket skills after writing `findings.md`. The RAG parses the body, splits on `## Heading` sections, embeds each, and atomically replaces all `provenance='local'` rows for that ticket. An empty body clears the ticket's local rows.

### `GET /healthz`

Standard liveness/readiness.

### `GET /stats`

Row counts per `source` × `provenance` × `kind`; index sizes; last harvester run times per source. Operational visibility.

## MCP interface

Thin wrapper. User-facing endpoints map 1:1 to MCP tools:

- `ticket_search(query, **filters)` → `POST /search`
- `ticket_hotspots(file)` → `GET /hotspots`
- `ticket_invalidate(source, ticket_id)` → `POST /invalidate`

`POST /local/sync` is **not** exposed via MCP. It is called directly over HTTP by the ticket skills as part of their write flow; there is no user reason to invoke it through Claude.

Returns plain JSON. The skills (`/slopstop:search`, plus any future `/slopstop:hotspots`) decide how to render results to the user.

The MCP wrapper itself is stateless.

## Lifecycle & deletion semantics

| Event | Effect on index |
|---|---|
| Upstream comment added | Picked up on next harvester pass. Optionally forced via `/invalidate`. |
| Upstream comment edited | Picked up on next pass; old row gone, new row inserted. |
| Upstream comment deleted | Row gone on next pass. **No tombstone, ever.** |
| Upstream ticket deleted | All rows for `ticket_id` deleted on next pass. |
| Local `findings.md` section added | Pushed by the next ticket-skill invocation that writes the file. |
| Local `findings.md` section removed | Same — the skill POSTs the full current file contents; the RAG re-syncs atomically. |
| Local `findings.md` file deleted | Cleared on the next skill-driven push for that ticket with an empty `findings_md` body, or via direct `POST /local/sync` with `"findings_md": ""`. |
| GitHub issue transferred between repos | New `ticket_id`; old one either 404s next sync (then deleted) or stays stale until the next full sweep. Acceptable. |

**Already-retrieved chunks in Claude transcripts:** if Claude pulled a chunk before its source was deleted, the chunk lives on in the conversation transcript. The RAG does not attempt retroactive scrubbing — that is outside its domain and not feasible.

## Operational concerns

### Container

- Base: `pgvector/pgvector:pg16` (or current). Adds Python 3.12, FastAPI, the harvesters, and the two model files (encoder + reranker), baked at image-build time.
- Image size: substantial (~3 GB with models). Acceptable for a developer tool.
- Storage: one named volume for the Postgres data dir.
- Resources: 8 GB RAM is sufficient for 10K-ticket corpora; the models occupy ~1 GB resident.

### Startup

```bash
docker run -d --name ticket-rag \
  -p 127.0.0.1:7777:7777 \
  -v ticket-rag-data:/var/lib/postgresql/data \
  slopstop-rag:latest
```

127.0.0.1 binding is non-negotiable. No port published to `0.0.0.0`.

### Skills' relationship to the RAG

The RAG is **optional**. Skills must work whether or not it is running.

- `/slopstop:search` probes `http://127.0.0.1:7777/healthz` first. On non-200, it prints *"RAG service not running; start with `docker start ticket-rag` or skip this query."* and stops.
- Ticket skills that push to `POST /local/sync` (`:document`, `:archive`, `:pause`, `:update`) treat a connection failure as a one-line warning, not a hard error. The skill's primary work completes; the local index stays slightly stale until the next push succeeds. Ticket skills never fail because the RAG is down.
- Future skills that benefit from retrieval (e.g. a "find related tickets when starting work" hint inside `/slopstop:start`) must include the same graceful-degradation pattern: optional capability, never required.

### `.project-conf.toml` integration

The plugin-wide configuration file is `.project-conf.toml` (TOML format), replacing the legacy single-word `.project-prefix`. RAG-related fields are optional and namespaced under `[rag]`:

```toml
system = "github"
key    = "iansmith/slopstop"

[status_labels]
in_progress = "status:in-progress"
in_review   = "status:in-review"

[rag]
endpoint     = "http://127.0.0.1:7777"   # optional override; default 127.0.0.1:7777
corpus_scope = "github"                  # optional; default = same as `system`
```

Not all fields exist for first cut; the `[rag]` namespace is reserved.

**Migration policy:** the two existing legacy projects (mazzy/MAZ on Linear and lyos/PLTF on JIRA) are migrated by hand as one-off operations. No auto-migration logic is built into the skills — new code expects the new format only. Any future project is set up via `ticket-gh-init` (or analogous skill) which writes the new format directly.

### Embedding-model upgrades

Re-embedding ~100K rows with a new model takes minutes on a modest CPU.

1. Compute the new embedding in a second column (`embedding_v2 vector(N)`).
2. Build the new HNSW index.
3. Switch retrieval to read `embedding_v2`.
4. Drop the old column and index.

Mentioned only because someone will eventually want to upgrade.

## Open questions

- **Cross-corpus default scope.** When called from a MAZ-prefixed cwd, does `/slopstop:search` default to filtering on `source='linear'`, or search all corpora? Lean: project-scoped default, with an explicit override flag (`--all-sources`).
- **Reaction signals from GitHub.** 👍 / 👎 / 🎉 / 😕 on comments could weight retrieval (a heavily-reacted comment is plausibly an "argument worth finding"). Defer until query patterns are clearer.
- **Image and attachment content.** Ticket comments sometimes contain screenshots or pasted images. First cut: ignored. Worth revisiting if dropped content turns out to be material.
- **Per-author retrieval.** *"Find arguments Ian made about caching."* Possible via `WHERE author = ?` filter; no special index needed. Will fall out naturally.
- **Embedding-model selection.** bge-m3 is the recommendation as of 2026-05-24 based on MTEB plus its multi-output capability. The field moves fast; the encoder is treated as replaceable.

## Dependencies

- Postgres ≥ 16, pgvector ≥ 0.7
- Python ≥ 3.12
- FastAPI, uvicorn
- sentence-transformers (encoder + reranker)
- psycopg 3.x
- httpx (harvester HTTP client)
- click (harvester CLI)

The bulk of the dependency tree is the ML stack. Nothing exotic.

## Initial milestones

In approximate build order:

1. **Container shell.** Postgres + pgvector + a "hello world" FastAPI + model files baked in. No real endpoints yet.
2. **Schema + manual ingest.** `ticket_chunks` created on container init. A CLI tool to ingest a single ticket from a JSON file. Validates the embedding pipeline end-to-end.
3. **First harvester: GitHub.** Cleanest API, and `iansmith/slopstop` is the dogfood target.
4. **`/search` endpoint.** Dense retrieval first; reranker added immediately after.
5. **MCP wrapper + `/slopstop:search` skill.** End-to-end usable.
6. **Local file watcher.** Indexes `findings.md` after the `:pause` / `:update` restructure lands. (The restructure is a hard prerequisite; without it, local-channel content is the wrong stuff.)
7. **Linear harvester.**
8. **JIRA harvester.**
9. **`/hotspots` endpoint + `/slopstop:hotspots` skill.**

A reasonable initial cut ships after step 5 — working RAG over one corpus, dogfoodable on this repo. Step 6 onward is incremental coverage.

## Prerequisites and adjacent work

- **`.project-conf.toml` format** (plugin-wide rename): the legacy single-word `.project-prefix` is replaced by a structured TOML file at the same path. Touched by every `ticket-*` skill. **No auto-migration code** — the two existing legacy projects (mazzy/MAZ, lyos/PLTF) are migrated by hand; new code expects the new format only.
- **`ticket-gh-init`** (new skill): bootstraps GitHub-backed projects. Prints an explainer of what it is about to change (labels in the GH repo, `.project-conf.toml` written locally), asks a single question (3-state vs. 4-state workflow — the 4-state version adds *in review*, meaning an external approval is required before transitioning to *done*), then performs the changes idempotently.
- **`:pause` / `:update` restructure** (separate spec): redirects substantive prose to `findings.md` so the local channel indexes the right material.
- **`ticket-doc-sync`** ([issue #1](https://github.com/iansmith/slopstop/issues/1)): independent of the RAG, but adjacent — both projects need GitHub backing in place.

None of these block the design; some block delivery of specific milestones.
