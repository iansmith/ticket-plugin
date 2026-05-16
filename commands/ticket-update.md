# /ticket-update

Snapshot mid-session progress to the active ticket's tracking files. The ticket stays active — `CURRENT-<PREFIX>` is NOT cleared. Local-only — never calls JIRA or Linear.

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or modify `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments

None. The active ticket is whatever `~/.claude/ticket-active/CURRENT-$PREFIX` contains.

## Pre-flight

- `$TICKET` = contents of `~/.claude/ticket-active/CURRENT-$PREFIX`. If empty or missing: print `"No active $PREFIX ticket to update."` and stop.
- If `~/.claude/ticket-active/$TICKET/` doesn't exist: print error and stop.

## Capture (run git calls in parallel)

- `$BRANCH` = `git branch --show-current`
- `$DIRTY` = `git status --porcelain` (note count of modified files)
- `$HEAD` = `git log -1 --format="%h %s"`
- `$PWD` = `pwd`
- `$TS` = `date -u +"%Y-%m-%d %H:%M UTC"`

## Append to `progress.md`

```markdown

## Update $TS

**Branch:** $BRANCH (HEAD: $HEAD)
**cwd:** $PWD
**Working tree:** clean | dirty: N files modified

### Completed since last snapshot
<bullets, one line each, of meaningful work done since the last pause/update entry>

### Current state
<one sentence: what is true right now — just finished, or actively in progress>

### Next step
<single concrete next action, in case context is lost from here>
```

Fill every section from conversation context. Don't ask the user.

## Also update (only if changed this session)

- `task_plan.md` — if phases were started, completed, invalidated, or newly scoped. Edit Plan checkboxes/notes. Skip cosmetic rewrites.
- `findings.md` — if new investigation results uncovered. Add a `## <topic>` section. Don't duplicate `task_plan.md` or `progress.md`.

## Confirm

```
Updated tracking for $TICKET.
Wrote: <files actually modified>
Ticket is still active. Pause with /ticket-pause when done.
```

## Rules

- Do NOT clear or modify `CURRENT-$PREFIX`. The ticket stays active.
- Do NOT touch git.
- Do NOT call JIRA or Linear.
- Do NOT touch the auto-memory system (`~/.claude/projects/.../memory/`). Different system.
