# ticket-plugin

A Claude Code plugin that keeps a durable per-ticket plan, findings, and progress log for every ticket you work on — without bloating the ticket itself or polluting the repo. Auto-detects [Linear](https://linear.app/) or [JIRA](https://www.atlassian.com/software/jira) as your ticket system.

The aim: fewer wasted tokens re-explaining context between sessions, and a clean ticket-system record of what was actually done by the time you close the ticket.

## What it does

Four slash commands form a complete loop around a ticket:

| Command | What it does | Touches ticket system? |
|---|---|---|
| `/ticket-start <KEY>` | Fresh-start: fetch the ticket, transition it to **In Progress**, seed `task_plan.md`, `findings.md`, `progress.md`. Resume: read tracking files, print summary, append a Session header. | Yes (fresh-start only) |
| `/ticket-update` | Snapshot mid-session progress to `progress.md`. The ticket stays active. Local-only. | No |
| `/ticket-pause` | Snapshot state and clear the active-ticket pointer. Local-only. | No |
| `/ticket-archive` | Push final task plan back to the ticket as its description, post `findings.md` as a comment, and move the local folder to archive. Requires the ticket to **already** be in a terminal state on the ticket system — the user transitions, this command syncs. | Yes |

Tracking files live at `~/.claude/ticket-active/<TICKET>/` while the ticket is active, then move to `~/.claude/ticket-archive/<TICKET>/`. They survive `cd` between repos.

## Prerequisites

- **Claude Code** with the plugin manager available (the `/plugin` command).
- One of:
  - **Linear MCP** for Linear tickets, *or*
  - **Atlassian MCP** for JIRA tickets.

  The plugin auto-detects which is configured at run-time. If both are configured in one session, it asks which to use.
- A `.project-prefix` file in each project's working directory (see Setup below).

## Install

```
/plugin marketplace add iansmith/ticket-plugin
/plugin install ticket-plugin@ticket-plugin
```

## Setup — `.project-prefix`

Every project where you'll run these commands needs a `.project-prefix` file at the repo root. It contains a single line — the ticket prefix for that project:

```bash
echo MAZ > .project-prefix    # Linear team MAZ
echo PLTF > .project-prefix   # JIRA project PLTF
echo LOU > .project-prefix    # whatever your prefix is
```

The plugin reads this file from your current working directory on every invocation. **It only operates on tickets whose key matches the cwd's `.project-prefix`** — so a session in `~/mazzy/` (prefix `MAZ`) can never accidentally touch a `PLTF-*` ticket, even if your other project has one active.

This also means you can have multiple tickets active at the same time across different projects (one MAZ ticket and one PLTF ticket, for example) — the per-prefix `CURRENT` pointer (`~/.claude/ticket-active/CURRENT-MAZ`, `CURRENT-PLTF`, etc.) keeps them independent.

## Usage

```
$ cd ~/mazzy                          # has .project-prefix containing MAZ
$ /ticket-start MAZ-26                # fresh-start: transitions to In Progress, seeds tracking dir

# ... work happens ...

$ /ticket-update                      # mid-session checkpoint to progress.md

# ... more work ...

$ /ticket-pause                       # interrupted; capture state, clear active pointer

# ... later, possibly in a fresh Claude Code session ...

$ /ticket-start MAZ-26                # resume: reads tracking files, prints summary, re-activates

# ... finish the work ...
# (transition MAZ-26 to Done on Linear yourself)

$ /ticket-archive                     # pushes task_plan → ticket description, findings → comment, archives locally
```

### Switching tickets within the same project

If a different ticket of the same prefix is already active, `/ticket-start <NEW-KEY>` automatically runs `/ticket-update` on the old one before switching — capturing where you left off without manual ceremony. No "are you sure" prompt.

### `/ticket-start` on the currently active ticket

No-op aside from the normal resume summary. Same as picking up where you left off.

## Tracking files — what's in them

Each ticket directory contains three markdown files:

- **`task_plan.md`** — the durable plan. Starts seeded with the ticket's original description; you fill in the **Plan** section as you scope work. This is what gets pushed back to the ticket's description on `/ticket-archive`.
- **`findings.md`** — investigation results: root causes, codebase facts, constraints, dead-ends ruled out. Pushed as a comment on `/ticket-archive` (unless it's template-empty).
- **`progress.md`** — per-session diary with `## Session`, `## Update`, and `## Pause` entries. **Never** pushed to the ticket system — too noisy for the durable record. Lives locally; commit history + the comment + the description are what the ticket carries.

## Design choices

A few decisions worth knowing about:

- **`/ticket-archive` refuses to archive unless the ticket is already in a terminal state on the ticket system.** The user transitions the ticket; this command syncs back. No "Claude marked my ticket done without telling me" failure mode.
- **Per-prefix CURRENT pointer.** `CURRENT-MAZ`, `CURRENT-PLTF`, etc. are independent files. Parallel sessions on different project families don't conflict.
- **The plugin never touches git.** It records branch / HEAD / dirty state for context, but stash/commit/branch is yours to manage.
- **JIRA + Linear are first-class.** Detection is automatic. If both MCPs are configured in one session, the command asks rather than guessing. GitHub Issues is not currently supported.
- **Tracking files live outside the repo** (`~/.claude/ticket-active/<TICKET>/`). They survive `cd` between repos and aren't tied to any branch.

## Storage layout

```
~/.claude/
  ticket-active/
    CURRENT-MAZ           ← contains the active MAZ ticket key, or empty
    CURRENT-PLTF          ← contains the active PLTF ticket key, or empty
    MAZ-26/
      task_plan.md
      findings.md
      progress.md
    PLTF-2180/
      ...
  ticket-archive/
    MAZ-23/
      ...
```

`CURRENT-<PREFIX>` files are created and cleared by the plugin. `<TICKET>/` directories are created by `/ticket-start` and moved to `ticket-archive/` by `/ticket-archive`.

## License

MIT — see [LICENSE](LICENSE).

## Author

Ian Smith ([@iansmith](https://github.com/iansmith))
