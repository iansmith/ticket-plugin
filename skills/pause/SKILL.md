---
description: Snapshot the active ticket's state to its tracking files and clear it as active. Use /tickets:pause when interrupted, before switching to other work. Local-only — never calls JIRA or Linear.
disable-model-invocation: true
---

# /tickets:pause

Snapshot the active ticket's state to its tracking files and clear `CURRENT-<PREFIX>`. Local-only — never calls JIRA or Linear.

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or clear `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments

None. The active ticket is whatever `~/.claude/ticket-active/CURRENT-$PREFIX` contains.

## Pre-flight

- `$TICKET` = contents of `~/.claude/ticket-active/CURRENT-$PREFIX`. If empty or missing: print `"No active $PREFIX ticket to pause."` and stop.
- If `~/.claude/ticket-active/$TICKET/` doesn't exist: state corruption — print error and stop without writing anything.

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

## Clear CURRENT (empty, don't delete)

`: > ~/.claude/ticket-active/CURRENT-$PREFIX`

## Confirm

```
Paused $TICKET.
Captured: <files actually written>
Resume with: /tickets:start $TICKET
```

## Rules

- Local-only. Never call JIRA or Linear.
- Never touch git. If working tree is dirty, just record the fact.
- Partial pause beats no pause. If "next step" or "last completed" is genuinely unknown, capture what you can.
