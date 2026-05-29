# Multi-ticket support — Design Document

**Status:** Draft, 2026-05-24.

## Summary

Multiple tickets can be in-flight in a single project at the same time. The currently selected ticket for any given working directory is determined unambiguously by the **git branch name** — the branch convention IS the selection mechanism. In-flight tickets carry a sub-state (`active` or `blocked`) and an optional parent linkage to another ticket. A low-ceremony entry path (`:exp`) lets the user create an experimental ticket and branch in one step, sidestepping the planning ceremony. The merge skill surfaces parent/subtask relationships as informational notes without blocking.

## Motivation

The single-ticket model (`CURRENT-$PREFIX` pointer) breaks down in two everyday scenarios:

1. **Blocked-then-switch.** Mid-stream on MAZ-4, blocked by someone else's fix. Want to start MAZ-5 without losing MAZ-4's state, and resume MAZ-4 when the blocker clears.
2. **Concurrent sessions on one ticket system.** Two Claude Code sessions in different working trees (worktrees or clones), each on a different ticket in the same project.

Both reduce to "more than one ticket can be in-flight at once," with the question of which one each session is operating on.

## Goals

- Allow multiple tickets to be in-flight simultaneously per project.
- Determine the currently selected ticket from the git branch — no separate session state, no prompts.
- Make ticket switching equivalent to checking out a different branch.
- Capture an explicit "blocked" sub-state with a recorded reason.
- Capture parent/subtask linkage at start time, and surface it as informational notes at merge time.
- Provide a single-command path (`:exp`) for low-ceremony experimental work that still produces a real ticket.

## Non-goals

- Cross-project linkage. Parent/subtask relationships are within one ticket system.
- Enforcement of subtask completion before parent close. Notes only; never block.
- Silent upstream writes to *other* tickets. When a skill's primary action is on `$TICKET` but it also modifies a *different* ticket as a side effect (e.g. establishing a sub-issue link on the parent), the side-effect write requires explicit confirmation. Direct writes to `$TICKET` as part of the skill's main job — state transitions, comments, description edits — are not gated; the skill invocation is the consent.
- Resolving file-system races between two Claude sessions in the same cwd. That remains the user's responsibility.

## State model

Three distinct concepts, previously collapsed into the singleton `CURRENT-$PREFIX`:

| Concept | Scope | Encoded by |
|---|---|---|
| **In-flight** | Per project | Directory exists under `ticket-active/$TICKET/` |
| **Selected** | Per working tree | Git branch name |
| **Active vs. blocked** | Per in-flight ticket | `state.toml` inside the ticket dir |

Plus, optionally on each ticket:

| Concept | Scope | Encoded by |
|---|---|---|
| **Parent linkage** | Per in-flight ticket | `parent` field in `state.toml` (mirrors upstream where available) |

"Archived" remains the terminal state — ticket directory moves to `ticket-archive/`, no longer in-flight.

## Branch IS the selection

### Convention

Every ticket branch contains its ticket ID. Standard prefixes:

| Prefix | Origin | Example |
|---|---|---|
| `feat/` | `:start` (standard work) | `feat/MAZ-43` |
| `fix/` | `:start` (bugs) | `fix/MAZ-43-followup` |
| `exp/` | `:exp` (experimental) | `exp/MAZ-44` |

The prefix is informational. The selection lookup parses the ticket ID anywhere in the branch name.

### Selection lookup

Every `ticket-*` skill that needs to know the active ticket runs the same lookup:

```
1. Read .project-conf.toml. $PREFIX = `prefix` if set, else `key`. (Linear/JIRA use `key` because it is already a short identifier like MAZ/LOU/PLTF; GitHub-backed projects set `prefix` explicitly because `key` is `owner/repo` and unsafe for branch names / filesystem paths.)
2. $BRANCH = git branch --show-current
3. Extract $PREFIX-\d+ from $BRANCH.
   - No match → stop with error:
       "Branch '$BRANCH' does not encode a $PREFIX ticket ID.
        Check out a ticket branch first, or run :start / :exp to create one."
   - Match → $TICKET
4. Verify ~/.claude/ticket-active/$TICKET/ exists.
   - Missing → stop with error:
       "$TICKET is not in-flight. Run :start $TICKET first."
5. Use $TICKET as the active ticket.
```

This replaces the old `CURRENT-$PREFIX` read entirely. There is no per-session state file. There is no `:select` skill.

Switching tickets = `git checkout <other-branch>`. The next ticket-skill invocation naturally picks up the new selection.

### Consequences

- **Selection-vs-branch mismatch is impossible by construction.** The branch *is* the selection.
- **Detached HEAD and `main`/`master`** produce the no-match error and stop the skill. You cannot do ticket work without a ticket branch — this is intentional, given that the cost of lost work is too high (per design discussion).
- **Multiple in-flight tickets** require multiple branches, which require multiple worktrees (or sequential checkouts in the same cwd). Worktrees are the natural answer for true parallelism.
- **Selection state is zero-cost.** It's recovered from `git branch --show-current` on every skill entry — no file to keep in sync.

## `state.toml`

Each in-flight ticket may carry a `state.toml` inside its directory. **Absence of the file = active, no parent.**

```toml
# Sub-state of the ticket. Defaults: 'active', no blocker.
state         = "active"                          # 'active' | 'blocked'
blocked_on    = "waiting on Bob's fix in mazzy#42" # required if state = 'blocked'
blocked_since = "2026-05-24T15:00:00Z"             # required if state = 'blocked'

# Optional linkage to a parent ticket in the same project.
parent        = "MAZ-3"                            # ticket ID
```

### When `state.toml` is written

- `:start` — if a parent is detected (Linear/JIRA) or specified (`--parent`, all backends): write `parent`. Otherwise leave the file absent.
- `:pause` — auto-prompt for blocker reason. If user gives a non-empty / non-`none` reason: write `state = "blocked"`, `blocked_on`, `blocked_since`. Otherwise leave `state.toml` absent (or only the `parent` field if set previously).
- `:start` of an already-existing in-flight ticket (resume): no change — preserve existing `state.toml`.
- `:archive` — file is removed along with the rest of the ticket dir on archival.

### What's *not* in `state.toml`

- `subtasks` — children are queried live from upstream when needed. Storing them locally would create a stale-cache problem.
- `selected` / `current` — selection lives in the git branch, not the file.
- Any field that can be derived from elsewhere (git status, ticket-system API).

## Parent / subtask linkage

### Capture (at `:start`)

```
:start MAZ-43             # auto-detect parent for Linear/JIRA, none for GH
:start MAZ-43 --parent MAZ-3   # explicit
```

All three backends expose a native parent / sub-issue relationship:

- **Linear:** `parentId` on Issue (queryable via `mcp__linear-server__get_issue` and writable via `save_issue`).
- **JIRA:** parent / sub-task hierarchy (issue links + parent field).
- **GitHub:** native sub-issues, GA in 2024. REST `/repos/{owner}/{repo}/issues/{N}/sub_issues`; GraphQL `subIssues` on `Issue`; `gh sub-issue` CLI extension. **No label hacks, no task-list body editing** — the relationship is a first-class field.

**Pre-validation (when `--parent $P` is given).** Before any of the linkage cases below:

- Verify `$P` exists upstream. If not, stop with `"--parent $P not found in <system>."`. Cheap (one API call); prevents typos that would create dead links.
- If `$P`'s upstream state is the terminal *done* state, print a one-line warning:
  > *"Note: parent ticket $P is already in 'Done' state. Subtasks are typically completed before the parent is closed."*

  Do not block — the user already typed the flag and is the authority. The warning is the call-out, not a gate.

Logic (uniform across backends):

1. `:start` queries upstream for `$TICKET`'s parent.
2. **No `--parent` given:** if upstream returns a parent, use it. If not, no parent is recorded.
3. **`--parent $P` given, matches upstream:** no-op upstream; proceed.
4. **`--parent $P` given, no upstream parent yet:** prompt for explicit confirmation:
   > *"Establish `$TICKET` as a sub-issue of `$P`? This will modify `$P` in the ticket system. [y/N]"*

   On `y`: call the upstream linkage API (Linear `save_issue` with `parentId`; JIRA equivalent; GH `POST /sub_issues`). On `n`: skip the upstream write but still record `parent = "$P"` in local `state.toml` (so the merge-time note still surfaces it).
5. **`--parent $P` given, conflicts with upstream:** stop with an error showing both values.

**Design rule:** writes to a ticket *other than* the one the skill was invoked on require explicit user confirmation. Direct writes to the invoked ticket (state transitions, comments, description edits performed by `:start` / `:document` / `:archive` / `:merge`) are not gated — the skill name is the consent. Side-effect writes to other tickets, like creating a parent linkage on `$P`, are gated.

When a parent is captured (locally or upstream):
- `parent = "$PARENT"` is written to `state.toml`.
- A one-line note is prepended to `task_plan.md`:
  ```markdown
  > **Parent:** $PARENT — *<parent title at time of :start>*
  ```
- The note is human-readable context; the structured field in `state.toml` is authoritative.

### Surface at merge (and archive)

`:merge` and `:archive` query upstream and emit informational notes before performing their primary action. Notes are advisory — they never block.

**Note 1: Parent linkage.**

```
1. Read state.toml → parent.
2. If parent is set:
   a. Query upstream for parent's current state.
   b. Emit:
        "Note: parent ticket $PARENT is in state '$PARENT_STATE'."
   c. If $PARENT_STATE != done:
      - Query upstream for parent's children (excluding $TICKET).
      - Filter to those not in done state.
      - Emit:
        "  Sibling subtasks not yet done: $LIST_WITH_STATES"
```

**Note 2: This ticket's subtasks.**

```
1. Query upstream for $TICKET's children.
2. If any exist:
   a. Emit:
        "Note: this ticket has subtasks: $LIST_WITH_STATES"
```

Example output for a `:merge`:

```
Note: parent ticket MAZ-3 is in state 'In Progress'.
  Sibling subtasks not yet done: MAZ-5 (In Progress), MAZ-7 (Backlog)

Note: this ticket has subtasks: MAZ-44 (In Review), MAZ-45 (Done)

Proceeding with merge transition…
```

### Why notes, not blocks

Strict enforcement ("can't merge if parent isn't done") would push the user into the wrong corner often — partial work on a subtask is routinely shipped before the parent is fully done, by design. The note exists to make sure the user *notices* the parent's state, not to gate on it.

## Skill behavior

### `:start [--parent <ID>] $TICKET`

1. Read `.project-conf.toml` → `$PREFIX`.
2. Verify `$TICKET` is a valid `$PREFIX-\d+` form.
3. Check current branch:
   - If on a branch encoding a *different* ticket: stop with `"currently on '$BRANCH'; run :pause first, then :start $TICKET."`
   - If on a branch encoding `$TICKET`: resume mode — skip branch creation.
   - Otherwise: create branch `feat/$TICKET` from current HEAD and check out.
4. Create `ticket-active/$TICKET/` if absent, with template `task_plan.md`, `findings.md`, `progress.md`.
5. **Parent capture.** (See *Parent / subtask linkage → Capture* for the full rules.)
   - Query upstream for `$TICKET`'s parent (sub-issue relationship — all three backends support this natively).
   - If `--parent $P` is given and no upstream parent exists: prompt for confirmation before writing the linkage upstream. On decline, record `parent` in local `state.toml` only.
   - On conflict between `--parent` and the existing upstream parent: stop with an error.
   - If a parent is established (locally or upstream): write `state.toml` with `parent = "$PARENT"`, and prepend `> **Parent:** $PARENT — *<title>*` to `task_plan.md`.
6. Transition `$TICKET` to In Progress upstream.
7. Run the standard `:plan` flow.
8. Confirm.

Argument-less form (`:start` with no `$TICKET`) is removed — the new model requires explicit ticket identification at start time.

### `:exp "<description>"`

(Detailed in `design/ticket-rag.md` and earlier discussion.) Creates a ticket in the configured system, branches `exp/$TICKET`, skips `:plan`. Accepts an optional `--parent $P` flag; when given, follows the same pre-validation + confirmation flow as `:start --parent`. Auto-detection of a parent does not apply (the experiment ticket is freshly created and has no pre-existing upstream linkage).

### `:pause`

1. Selection lookup → `$TICKET`.
2. Append operational-only block to `progress.md` (after the `:pause` / `:update` restructure: branch / HEAD / cwd / dirty count / last-completed one-line / next-step one-line).
3. **Auto-prompt:** *"What's blocking you on `$TICKET`? (Enter 'none' if just stopping.)"*
   - Answer `none` (case-insensitive) or empty: leave `state.toml` unchanged.
   - Any other text: write/update `state.toml` with `state = "blocked"`, `blocked_on = <text>`, `blocked_since = $TS`.
4. **No `CURRENT` clear.** The ticket stays in-flight; selection is naturally lost when the user checks out a different branch.

Note: `:pause` no longer modifies any selection state because there is none to modify. The branch is the selection.

### `:update`

Selection lookup → `$TICKET`. Otherwise unchanged from current behavior (after the restructure that separates operational from substantive prose).

### `:document` and `:archive`

Selection lookup → `$TICKET`. Push upstream as today.

`:archive` additionally emits the parent / subtask notes (same logic as `:merge`) before performing the transition to done.

### `:merge`

1. Selection lookup → `$TICKET`.
2. Read `state.toml` → `$PARENT`.
3. Emit parent / subtask notes (see Parent / subtask linkage section).
4. Perform the merge state transition upstream.
5. The notes are stdout only; never written to ticket-system comments or local files.

### `:tickets` (new, dashboard)

Lists every in-flight ticket for the current project with a row per ticket. Columns:

| Column | Source |
|---|---|
| Ticket ID | dir name in `ticket-active/` |
| Selected? | does current branch's parsed ticket match this row? |
| State | `state.toml`: `active` / `blocked` |
| Blocker | `state.toml` `blocked_on`, if blocked |
| Parent | `state.toml` `parent`, if set |
| Branch | `git branch --list '*$TICKET*'` first match |
| Uncommitted | `git status --porcelain` against the ticket's branch (if the branch has uncommitted changes in any working tree) |
| Last touched | max mtime across `task_plan.md`, `findings.md`, `progress.md` |
| Open PR | `gh pr list --head <branch>` — number + state + draft? |
| CodeRabbit run? | count of `coderabbitai[bot]` reviews on the PR |
| CodeRabbit responded? | user comment or push timestamp > most recent CodeRabbit review timestamp — one-bit signal, may render as `responded` / `pending` |

The "responded" check is a proxy (it doesn't verify per-comment engagement). It's a useful binary signal at the dashboard level; deeper inspection requires opening the PR.

Output format: aligned columns, one row per ticket. Optional `--json` for machine consumption.

**Performance.** `O(N)` API calls per invocation (one `gh pr list` per ticket, plus parent / subtask queries). Latency is accepted in the first cut — no caching, no parallel fan-out. Optimization waits until in-flight ticket counts make it warranted.

### `:block` (new)

```
:block "<reason>"
```

Marks the currently selected ticket as blocked with a reason. Equivalent to `:pause` followed by re-selection, but without the progress.md write and without losing the current selection (you stay on the branch). Writes/updates `state.toml`.

### Removed / not added

- **`:select`** — not added. Switching tickets is `git checkout <branch>`.
- **`:current`** — not added. The branch already shows the current ticket.

## `.project-conf.toml` reservations

The new format may carry multi-ticket-related fields. Reserved namespaces:

```toml
[branch_prefixes]
feature = "feat"          # default
fix     = "fix"           # default
exp     = "exp"           # default

[exp]
label         = "experiment"   # optional; applied to experiment tickets
branch_prefix = "exp"          # default; mirror of [branch_prefixes].exp

# (Other reserved namespaces — [rag], [status_labels] — covered in adjacent docs.)
```

First cut may ignore `[branch_prefixes]` and hardcode the defaults. The namespace is reserved for projects that want different prefixes.

## Migration

No auto-migration code in skills (same discipline as `.project-conf.toml`).

For each existing legacy project (mazzy/MAZ, lyos/PLTF), a one-off manual migration:

1. Confirm `.project-conf.toml` is in place (separate migration).
2. For each ticket directory currently under `ticket-active/`:
   - If a feature branch already exists: keep it; just remove `CURRENT-$PREFIX`.
   - If no branch: create `feat/$TICKET` from a sensible base.
3. Delete `CURRENT-$PREFIX`.

For Linear / JIRA projects, parent linkages will be picked up on the next `:start` or by a one-off "rehydrate state.toml" script that iterates in-flight tickets and queries upstream.

## Interaction with RAG

The RAG indexes by `ticket_id`. Multiple in-flight tickets simply mean more `provenance='local'` content scattered across nearby IDs. No schema or ingestion changes.

`/slopstop:search` does **not** filter by the selected ticket by default — semantic search should find context across nearby tickets, including ones the session isn't actively working. The selected ticket is incidental to search relevance.

Parent / subtask relationships are not indexed by the RAG directly; they're consulted live by `:merge` / `:archive` / `:tickets`. The `ticket_refs JSONB` column in the RAG schema captures cross-ticket references found in chunk text incidentally, which may include parent / subtask IDs as a by-product.

## Prerequisites

- **`.project-conf.toml` format** (separate, plugin-wide rename) — required first.
- **`ticket-gh-init`** — required for any new GH-backed project (creates state labels).
- **`:pause` / `:update` restructure** — independent but logically grouped; both this and the restructure touch the same skills, so do them in one pass per skill if convenient.

None of these block the design; they block delivery of specific milestones.

## Initial milestones

1. Branch-IS-selection lookup helper, used by every `ticket-*` skill (replaces all `CURRENT-$PREFIX` reads).
2. `state.toml` schema + writers in `:start` and `:pause`.
3. `:start` updates — branch creation, parent capture (Linear/JIRA auto-detect), `--parent` flag (all backends).
4. `:merge` and `:archive` parent / subtask notes.
5. `:exp` (covered in adjacent doc).
6. `:tickets` dashboard.
7. `:block` (small; can ship with or after dashboard).

Step 1 is the keystone — once branch-as-selection is wired, the rest is additive.
