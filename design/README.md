---
title: Home
slug:  Home
---

# Design index

This directory contains design documents for the ticket-plugin project. Together they describe two related systems:

1. The `ticket-*` skill suite — Claude Code skills that manage work tickets on Linear, JIRA, or GitHub Issues.
2. A standalone RAG service that indexes ticket content for semantic search and exposes it to Claude via MCP.

The docs are written to be readable independently, but they cross-reference one another. This index gives the recommended reading order and a dependency map.

## Reading order

Read in this order on first pass. Each builds on concepts from earlier ones.

| # | Doc | Why read it now |
|---|---|---|
| 1 | [project-conf-toml.md](project-conf-toml.md) | The per-project config file format. Small and foundational — defines vocabulary (`system`, `key`, `[status_labels]`, `[rag]`, `[exp]`) used everywhere else. |
| 2 | [multi-ticket.md](multi-ticket.md) | The core ticket-workflow model. Branch-IS-selection, `state.toml`, `:exp`, parent/subtask linkage, the `:tickets` dashboard. Also defines `state.toml` inline (no separate doc for it). |
| 3 | [pause-update.md](pause-update.md) | Restructure of `:pause` and `:update` so `progress.md` stays operational-only and substantive prose flows to `findings.md`. Builds on multi-ticket's `state.toml` and blocker prompt. |
| 4 | [ticket-rag.md](ticket-rag.md) | The standalone RAG service: containerized Postgres + pgvector + FastAPI, three harvesters (Linear / JIRA / GH), skill-driven local push, MCP wrapper. Builds on project-conf-toml's `[rag]` namespace and multi-ticket's skill behavior. |
| 5 | [ticket-gh-init.md](ticket-gh-init.md) | Bootstrap skill for GitHub-backed projects. Standalone — could be read anywhere after #1. Listed here because in practice you only need it when standing up a new GH project. |
| 6 | [ticket-doc-sync.md](ticket-doc-sync.md) | Skill that mirrors `design/` to the project's doc store (GH wiki / Linear Docs). Standalone; depends only on `.project-conf.toml`. |

## Dependency map

Which docs depend on which (arrows point to prerequisites):

| Doc | Depends on |
|---|---|
| `project-conf-toml.md` | — |
| `multi-ticket.md` | `project-conf-toml.md` |
| `pause-update.md` | `multi-ticket.md`, `ticket-rag.md` |
| `ticket-rag.md` | `project-conf-toml.md`, `multi-ticket.md` |
| `ticket-gh-init.md` | `project-conf-toml.md` |
| `ticket-doc-sync.md` | `project-conf-toml.md` |

In ASCII:

```
                 project-conf-toml
                 │      │       │       │
        ┌────────┘      │       │       └──────────┐
        ▼               ▼       ▼                  ▼
   multi-ticket  ticket-gh-init  ticket-doc-sync   ticket-rag
        │                                            ▲
        └──┐                                ┌────────┘
           ▼                                │
       pause-update  ◀──────────────────────┘
```

## By purpose

Choose your starting point based on what you're trying to do.

**Implementing the ticket workflow** (what each skill does, how state moves):
`project-conf-toml.md` → `multi-ticket.md` → `pause-update.md` → `ticket-gh-init.md` → `ticket-doc-sync.md`

**Building the RAG service**:
`project-conf-toml.md` (for the `[rag]` namespace) → `multi-ticket.md` (for the skill-driven `/local/sync` pattern) → `ticket-rag.md`

**Just understanding the file formats**:
`project-conf-toml.md` for `.project-conf.toml`; `multi-ticket.md` (the `state.toml` section) for the per-ticket runtime file.

**Standing up a new GitHub-backed project**:
`project-conf-toml.md` → `ticket-gh-init.md`. That's enough to start. Multi-ticket / pause-update / RAG can come later.

## What's tracked elsewhere

- [iansmith/ticket-plugin#1](https://github.com/iansmith/ticket-plugin/issues/1) — original `ticket-doc-sync` spec. Superseded by [ticket-doc-sync.md](ticket-doc-sync.md); the issue stays open as the implementation tracking handle.

## Status

All six docs are first-draft as of 2026-05-24. None have open questions blocking implementation. Where decisions might have been open, they're resolved within the docs (with the resolution rationale captured in-line).

When the docs change, update the dependency table above if a new prerequisite relationship is introduced.
