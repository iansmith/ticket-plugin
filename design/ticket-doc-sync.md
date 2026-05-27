# `ticket-doc-sync` — Design Document

**Status:** Draft, 2026-05-24.

## Summary

A skill that mirrors the `design/` directory's markdown files to the project's ticket-system documentation store, on demand. One-way push: the committed `design/` files remain the source of truth; the doc-store copy is a convenience view, refreshed wholesale on each sync. Supersedes the spec in [iansmith/ticket-plugin#1](https://github.com/iansmith/ticket-plugin/issues/1).

## Goals

- Single-command sync: `/ticket-plugin:doc-sync` reads `design/` and pushes to whatever doc store the project's `.project-conf.toml` system maps to.
- Idempotent: re-running with no `design/` changes is a no-op.
- Orphan-pruning: pages in the doc store without a corresponding `design/` file are deleted on each sync.
- Backend-agnostic at the user level: same command across backends; behavior varies by `system`.

## Non-goals

- Two-way sync. Editing the wiki page never propagates back to `design/`.
- Asset / binary uploads. Embedded images stay as external-URL references in the markdown.
- Confluence / JIRA support in the first cut.
- Auto-invocation on `design/` change. User-triggered only.

## Per-system targets

| System | Doc store | Status |
|---|---|---|
| GitHub | Repo wiki (`<owner>/<repo>.wiki.git`) | **In scope** |
| Linear | Linear Docs (`mcp__linear-server__save_document`) | **In scope** (second) |
| JIRA | Confluence | **Deferred** — explicit error |

## Resolved decisions

| Question | Resolution |
|---|---|
| Orphans in the doc store | **Prune.** Pages without a `design/` counterpart are deleted on each sync. |
| Frontmatter title override | **Optional `title:` (and `slug:`) in YAML frontmatter.** Default is filename-derived. |
| Images / assets | **External URLs only.** Binaries are never uploaded; embedded `![alt](url)` references pass through unchanged. |
| Per-file vs. consolidated on Linear | **Per-file across all backends.** One source file → one upstream page or document. |

## Frontmatter (optional)

A `design/*.md` file may begin with YAML frontmatter:

```yaml
---
title: My preferred page title
slug:  my-preferred-slug
---
```

- `title` overrides the auto-derived title (filename minus `.md`).
- `slug` overrides the auto-derived slug (lowercased filename, only meaningful on backends that distinguish title from URL — GH wiki being the main one).

Both fields are optional. Frontmatter is stripped before push — the doc-store copy contains body only.

## Pre-flight

Before any system-specific logic:

1. **`.project-conf.toml` present in cwd.** Stop if missing.
2. **`design/` directory present in cwd.** Stop if missing.
3. **Dirty-design check (informational).** Run `git status --porcelain -- design/`. If non-empty, print:
   > *"design/ has uncommitted changes. The sync will push your current working-tree state, not the last committed state. If you intended to push the committed version, stash or commit first."*

   Do not block — continue with the sync. The check exists to surface the "I just edited a doc, did I mean to push this version?" case.

System-specific pre-flight (auth, wiki initialization) lives in the per-system sections below.

### Note on Claude invocations

Do not invoke this skill in the same tool-use turn as `Edit` or `Write` operations targeting files under `design/`. The sync reads each source file at one moment while concurrent edits modify them; the pushed content becomes a mid-edit snapshot rather than the intended final state. This was observed in development: a parallel sync + edit batch pushed a wiki page in a half-stale form. Complete all `design/` edits in one turn, then invoke sync in a subsequent turn.

The dirty-design pre-flight catches the *committed-state* version of this concern (uncommitted edits exist on disk); the in-turn race is invisible to the pre-flight because edits and the sync both read/write within the same instant of process state.

## Per-system mechanics

### GitHub wiki

A GH wiki is itself a git repo at `<owner>/<repo>.wiki.git`. Flow:

1. Clone the wiki repo to a temp directory. **If clone fails with "Repository not found", stop with instructions for the user to initialize the wiki via the web UI.** GitHub requires the first wiki page to be created through the UI before `git push` to `.wiki.git` will work; an empty wiki (feature enabled but no pages) cannot be populated by push alone. The skill does not attempt to work around this — it surfaces the limitation cleanly and waits for the user to do the one-time UI step.
2. For each `design/*.md`: parse frontmatter, compute the page filename (`<slug>.md`), strip frontmatter, write the body to `$TMP/<slug>.md`.
3. For each `*.md` in `$TMP/` that doesn't correspond to a current `design/` source: delete it. (Orphan prune.)
4. `git add -A && git commit -m "doc-sync from <source-sha>" && git push origin master`. Skip the commit if there are no staged changes.
5. Remove the temp directory.

The commit message captures the source repo's HEAD SHA so the wiki history is traceable to a specific source commit.

### Linear Docs

Linear Docs are accessible via `mcp__linear-server__save_document` (create/update), `mcp__linear-server__list_documents` (enumerate), and the corresponding delete tool. Flow:

1. List existing docs in the project / team identified by `$KEY`.
2. For each `design/*.md`: parse frontmatter, compute the document title, strip frontmatter, then:
   - If an upstream doc with the matching title exists: update its body via `save_document`.
   - Otherwise: create via `save_document`.
3. For each upstream doc whose title doesn't match any current `design/` source: delete it. (Orphan prune.)

Title matching is exact. Project / team scoping defaults to whatever `$KEY` resolves to; refinement via a `[doc_sync]` namespace in `.project-conf.toml` is reserved but not required for first cut.

### JIRA / Confluence

Out of scope. The skill stops with `"Confluence sync not yet supported."` for `system = "jira"`.

## Errors

| Condition | Behavior |
|---|---|
| `.project-conf.toml` missing | Stop with standard missing-config message. |
| `design/` directory missing | Stop with `"No design/ directory found in cwd."` |
| `design/` has uncommitted changes | Informational warning only; sync continues. The user's working-tree state is what gets pushed. |
| `gh auth status` fails (GH) | Stop with auth instructions. |
| Wiki not initialized upstream (GH) | Stop with instructions to visit `https://github.com/<owner>/<repo>/wiki` and create the first page via the web UI. GitHub does not support git-push-only initialization. One-time user action. |
| Linear MCP unavailable | Stop with `"Linear MCP not available."` |
| `system = "jira"` | Stop with `"Confluence sync not yet supported."` |
| Network / API error mid-sync | Stop and report which file failed. Partial state may exist; re-running is safe (idempotent). |

## Acceptance

1. On a GH-backed project, `/ticket-plugin:doc-sync` mirrors `design/*.md` to the repo wiki. Re-running with no `design/` changes produces no commit.
2. Adding a new `design/foo.md` and re-running creates the wiki page.
3. Deleting `design/bar.md` and re-running deletes the corresponding wiki page (orphan prune).
4. A frontmatter `title:` correctly overrides the default page title.
5. Embedded image references (`![alt](https://...)`) render correctly in the wiki.
6. On a Linear-backed project, the same flow works against Linear Docs.

## Out-of-scope refinements (future work)

- Subdirectories inside `design/` (e.g. `design/notes/foo.md`). First cut handles only flat `*.md` files at the top of `design/`.
- `README.md` → `Home.md` special-casing on GH wiki. Frontmatter `slug: Home` handles this if needed; no built-in remap.
- Two-way edit propagation. Explicitly not supported.

## Prerequisites

- [`.project-conf.toml`](project-conf-toml.md) — the skill reads `system` and `key` from this file.

No dependency on the multi-ticket or RAG designs.
