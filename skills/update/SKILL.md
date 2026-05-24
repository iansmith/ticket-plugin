---
description: Mid-session checkpoint to the active ticket's progress.md. Use /ticket-plugin:update to snapshot what's been done so far without clearing the active pointer. The ticket stays active. Local-only — never calls JIRA or Linear.
disable-model-invocation: true
---

# /ticket-plugin:update

Snapshot mid-session progress to the active ticket's tracking files. The ticket stays active — `CURRENT-<PREFIX>` is NOT cleared. Local-only — never calls JIRA or Linear.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. Never read, write, or modify `CURRENT-*` files for any other prefix.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /ticket-plugin:gh-init (for GitHub) or create the file manually with system + key."`

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
Ticket is still active. Pause with /ticket-plugin:pause when done.
```

## Rules

- Do NOT clear or modify `CURRENT-$PREFIX`. The ticket stays active.
- Do NOT touch git.
- Do NOT call JIRA or Linear.
- Do NOT touch the auto-memory system (`~/.claude/projects/.../memory/`). Different system.
