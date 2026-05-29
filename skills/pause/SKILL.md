---
description: Snapshot the active ticket's state to its tracking files. The ticket stays in-flight (switching is git checkout). Use /slopstop:pause when interrupted, before switching to other work. Local-only — never calls JIRA or Linear.
disable-model-invocation: true
---

# /slopstop:pause

Snapshot the active ticket's state to its tracking files. The ticket stays in-flight; switching to another ticket is `git checkout <other-branch>`. Local-only — never calls JIRA or Linear.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. The branch-IS-selection parser only matches `$PREFIX-\d+`, so a branch encoding a different project's prefix correctly fails the no-match check.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /slopstop:gh-init (for GitHub) or create the file manually with system + key."`

## Arguments

None. The active ticket is parsed from `git branch --show-current` (see Pre-flight).

## Pre-flight

- **Resolve active ticket from branch.** Parse `$TICKET` from the current git branch:
  - `$BRANCH = $(git branch --show-current)`
  - Find the first match of `$PREFIX-\d+` in `$BRANCH` (case-insensitive on `$PREFIX`; canonical-case the result).
  - No match → stop with `"Branch '$BRANCH' does not encode a $PREFIX ticket ID. Check out a ticket branch first, or run :start / :exp to create one."`
  - Match → `$TICKET` (e.g. `MAZ-43`, `BILL-2`).
- **In-flight check.** Verify `~/.claude/ticket-active/$TICKET/` exists. If not: stop with `"$TICKET is not in-flight. Run :start $TICKET first."`

## Capture (run git calls in parallel)

- `$BRANCH` = `git branch --show-current`
- `$DIRTY` = `git status --porcelain` (note count of modified files)
- `$HEAD` = `git log -1 --format="%h %s"`
- `$PWD` = `pwd`
- `$TS` = `date -u +"%Y-%m-%d %H:%M UTC"`

## Append to `progress.md`

```markdown

## Pause $TS

**Branch:** $BRANCH (HEAD: $HEAD)
**cwd:** $PWD
**Working tree:** clean | dirty: N files modified

### Last completed
<one-line summary of the last meaningful unit of work this session>

### Next step
<single concrete next action — filename:line if applicable. If genuinely unknown, write "unclear — review last commit and decide" rather than inventing.>

### Open questions
<bullets, or "none">

### Mental context worth preserving
<non-obvious context the next session needs that isn't in code or git log: hypotheses, decisions, dead-ends. 3–5 bullets max.>
```

Fill every section from conversation context. Don't ask the user.

## Also update (only if changed this session)

- `task_plan.md` — if phases were scoped, completed, or invalidated. Edit the Plan section. Skip cosmetic rewrites.
- `findings.md` — if new investigation results uncovered. Add a `## <topic>` section. Don't duplicate `task_plan.md`.

## Confirm

```
Paused $TICKET.
Captured: <files actually written>
Resume with: /slopstop:start $TICKET
```

## Rules

- Local-only. Never call JIRA or Linear.
- Never touch git. If working tree is dirty, just record the fact.
- Partial pause beats no pause. If "next step" or "last completed" is genuinely unknown, capture what you can.
