# `:pause` and `:update` restructure — Design Document

**Status:** Draft, 2026-05-24.

## Summary

`progress.md` is policy-blocked from upstream push (per `:document`: *"per-session diary is too noisy for the durable record"*). But `:pause` and `:update` currently write substantive reasoning — mental context, open questions, decisions, dead-ends — into `progress.md`, where it is trapped locally.

This document restructures both skills so:

- `progress.md` keeps **only operational state** (reproducible from `git`, `pwd`, `date`).
- Substantive prose moves to `findings.md`, which `:document` already pushes upstream as a ticket comment at archive time.

No new upstream-write code paths. The fix uses existing chokepoints.

## Problem

The `ticket-*` skills audit identified two skills writing substantive reasoning into `progress.md`:

- **`:pause`** — appends `Last completed`, `Next step`, `Open questions`, and `Mental context worth preserving` (3-5 bullets of hypotheses, decisions, dead-ends).
- **`:update`** — appends `Completed since last snapshot`, `Current state`, `Next step`.

`progress.md` is explicitly excluded from upstream push by `:document`. Result: the most reasoning-dense output from `:pause` is also the most thoroughly trapped locally — the opposite of the traceability policy.

## Goal

Re-charter the two files to match their actual purpose without creating new upstream-write paths. `:document` remains the sole chokepoint to Linear / JIRA / GitHub.

- **`progress.md`** = operational state only. Per-session, noisy, never durable. Fully reproducible from `git status`, `git log`, `pwd`, `date`, and short-term memory.
- **`findings.md`** = substantive prose. Already pushed upstream by `:document` as a ticket comment at archive time. Already correctly named.

## Non-goals

- Adding any new upstream-write path. `:document` stays the sole writer to Linear / JIRA / GH.
- Backfilling existing `progress.md` files. The change applies to new invocations only.
- Restructuring `:document`, `:archive`, `:plan`, `:start`, `:merge`, or `:pr`. They are downstream of this change but unaffected.

## Scope

Two SKILL.md files only:
- `slopstop/skills/pause/SKILL.md`
- `slopstop/skills/update/SKILL.md`

## Restructured `:pause`

### A) `progress.md` — operational only

```markdown
## Pause $TS
**Branch:** $BRANCH (HEAD: $HEAD)
**cwd:** $PWD
**Working tree:** clean | dirty: N files modified
**Last completed:** <one-line>
**Next step:** <one-line, filename:line if applicable, or "unclear — review last commit">
```

One line per field. No prose subsections. Reproducible from `git` + short-term memory.

### B) `findings.md` — substantive prose (conditional)

Append a *content-titled* section (noun-phrase title, not session-titled) **only if substantive new context emerged**:

```markdown
## <Short noun-phrase describing the finding / decision / question>
<2–5 sentences. Hypothesis, decision rationale, dead-end and why, or open question.>
```

Skip entirely if nothing substantive emerged. The discipline rule, embedded in the skill body:

> *"If it would still matter to a future engineer who never saw this session, it's a finding — write it. Otherwise it's diary — leave it out."*

### C) Blocker prompt (auto)

Per the [multi-ticket design](multi-ticket.md), `:pause` always prompts:

> *"What's blocking you on `$TICKET`? (Enter 'none' if just stopping.)"*

- Answer `none` (case-insensitive) or empty: leave `state.toml` unchanged.
- Other text: write `state = "blocked"`, `blocked_on = <text>`, `blocked_since = $TS` to `state.toml`.

### D) Local-sync push to RAG

After file writes, POST the current `findings.md` body to the RAG service at `/local/sync` (per the [RAG service design](ticket-rag.md)). On connection failure, print a one-line warning and continue — never block the pause.

## Restructured `:update`

### A) `progress.md` — operational only

```markdown
## Update $TS
**Branch:** $BRANCH (HEAD: $HEAD)
**Working tree:** clean | dirty: N files modified
**Completed since last snapshot:** <one-line>
**Current state:** <one sentence>
**Next step:** <one-line>
```

### B) `findings.md` — substantive prose (conditional)

Same conditional + content-titled rule as `:pause`.

### C) Local-sync push to RAG

Same as `:pause`.

## Rules to add to both SKILL.md files

In the `## Rules` section:

- Do NOT write reasoning, hypotheses, decisions, or open questions to `progress.md`. Those go to `findings.md` as content-titled sections.
- If unsure whether something is a finding or a diary entry, prefer findings — `:document` can decide what to push at archive time; trapped diary is unrecoverable.

## Acceptance criteria

1. A typical `:pause` invocation writes ~6 lines to `progress.md` and zero-or-more content-titled sections to `findings.md`.
2. A `:pause` that surfaced no new reasoning produces a `progress.md` entry and **no** `findings.md` change.
3. Reading `progress.md` standalone tells you *where the work was paused*. Reading `findings.md` standalone tells you *what was learned*.
4. `:document`'s existing push logic requires no change — new findings ride the existing path.
5. Neither skill calls Linear / JIRA directly. The local-sync POST is to the RAG service only, and tolerates failures gracefully.

## Migration

No backfill. New invocations follow the new template; existing entries in `progress.md` files stay as-is. The next `:archive` pushes whatever `findings.md` currently holds — the right behavior.

If a project has existing `progress.md` files with reasoning trapped in them, the user can manually move those sections into `findings.md` before the next archive. No automation provided.

## Prerequisites

- [Multi-ticket design](multi-ticket.md) — `:pause` no longer clears `CURRENT-$PREFIX` (the file no longer exists); selection lives in the git branch. The blocker prompt rule comes from this doc.
- [RAG service design](ticket-rag.md) — the `/local/sync` push relies on the RAG endpoint; calls degrade gracefully.

Neither prerequisite blocks this restructure. The restructure can ship in isolation; without the RAG running, the `/local/sync` call simply warns and returns.
