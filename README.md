# ticket-plugin

A Claude Code plugin that keeps a durable per-ticket plan, findings, and progress log for every ticket you work on — without bloating the ticket itself or polluting the repo. Auto-detects [Linear](https://linear.app/) or [JIRA](https://www.atlassian.com/software/jira) as your ticket system.

The aim: fewer wasted tokens re-explaining context between sessions, and a clean ticket-system record of what was actually done by the time you close the ticket.

## Why this exists

Three concrete problems this plugin solves, with the framing for each.

**Context isolation per ticket.** Each ticket gets its own `task_plan.md`, `findings.md`, and `progress.md` at `~/.claude/ticket-active/<TICKET>/`. When you're working on `MAZ-26`, only `MAZ-26`'s notes load into Claude's context — not the dozen other tickets you've touched recently. That keeps token cost down and stops unrelated discoveries from leaking into your reasoning.

**Parallel project work.** The per-project `.project-prefix` file and per-prefix `CURRENT-<PREFIX>` pointers let you keep a Linear ticket active in one repo and a JIRA ticket active in another at the same time, in separate Claude sessions, without either side accidentally writing to the other's state. `cd`-ing into a repo automatically scopes the plugin to that project's prefix.

**Durable record for the next reader.** When you `/ticket-plugin:archive` a completed ticket, the final task plan is pushed back to the ticket as its new description (with the original description preserved as an appendix section), and the findings are posted as a comment. So the person who originally filed the ticket — or whoever comes back to it months later trying to understand what shipped — sees a real account of what was actually done, not just a title and a merged PR diff.

## What it does

Six slash commands form a complete loop around a ticket. After install, they live under the plugin's namespace (`/ticket-plugin:<name>`):

| Command | What it does | Touches ticket system? |
|---|---|---|
| `/ticket-plugin:start <KEY>` | Fresh-start: fetch the ticket, transition it to **In Progress**, seed `task_plan.md`, `findings.md`, `progress.md`. Resume: read tracking files, print summary, append a Session header. | Yes (fresh-start only) |
| `/ticket-plugin:update` | Snapshot mid-session progress to `progress.md`. The ticket stays active. Local-only. | No |
| `/ticket-plugin:pause` | Snapshot state and clear the active-ticket pointer. Local-only. | No |
| `/ticket-plugin:pr` | Open a pull request for the active ticket's branch. Runs Claude Code's `simplify` skill on uncommitted changes first, then generates a ticket-anchored commit message, pushes, opens the PR via GitHub MCP or `gh` CLI, triggers CodeRabbit if the base isn't the repo default, polls for CodeRabbit feedback up to 15 minutes, and categorizes the suggestions (🔴 should fix / 🟡 could fix / ⚪ skip) for action. Never auto-applies suggestions. | Indirectly (PR is on GitHub) |
| `/ticket-plugin:archive` | Push final task plan back to the ticket as its description, post `findings.md` as a comment, and move the local folder to archive. Requires the ticket to **already** be in a terminal state on the ticket system — the user transitions, this command syncs. | Yes |
| `/ticket-plugin:merge` | End-to-end ship-it: merge the PR via `gh pr merge`, transition the ticket to Done, delete the merged branch, and archive the local tracking dir. Confirms once before any destructive remote operation. Refuses safely on dirty trees, unpushed commits, draft PRs, or merge conflicts. | Yes |

Tracking files live at `~/.claude/ticket-active/<TICKET>/` while the ticket is active, then move to `~/.claude/ticket-archive/<TICKET>/`. They survive `cd` between repos.

## Prerequisites

This plugin is a **wrapper around a ticket-system MCP** — it has no built-in Linear or JIRA API client of its own. Before installing, make sure you have:

1. **Claude Code** with the plugin manager available (the `/plugin` command).

2. **One of these MCPs installed in your Claude Code session.** You need at least one — install whichever matches the ticket system your team uses (or both, if you work across both):

   ### Linear

   For tickets in [Linear](https://linear.app/). Install Anthropic's official Linear plugin from the [Anthropic plugins marketplace](https://github.com/anthropics/claude-plugins-official):

   ```
   /plugin marketplace add anthropics/claude-plugins-official
   /plugin install linear@claude-plugins-official
   ```

   The ticket-plugin's skills expect tools under the `mcp__linear-server__*` namespace.

   ### Atlassian (JIRA)

   For tickets in [JIRA](https://www.atlassian.com/software/jira). Install Anthropic's official Atlassian plugin (which wraps [atlassian/atlassian-mcp-server](https://github.com/atlassian/atlassian-mcp-server)):

   ```
   /plugin marketplace add anthropics/claude-plugins-official
   /plugin install atlassian@claude-plugins-official
   ```

   The ticket-plugin's skills expect tools under the `mcp__atlassian__*` namespace.

   ### Detection behavior

   The plugin auto-detects which MCP is configured at run-time, on every invocation:

   - **Only one configured** → used automatically.
   - **Both configured in the same session** → the skill asks which to use rather than guessing.
   - **Neither configured** → the skill stops with a clear error before touching any local state.

3. **A `.project-prefix` file** in each project's working directory (see Setup below).

### Compatibility note

The skill files reference tool names from the Linear and Atlassian MCPs as they ship from the Anthropic marketplace. If you install a different distribution (community fork, older version) and the tool namespace differs, the skill may fail with `"No ticket-system MCP found"` even though an MCP is installed — that means the `mcp__*` prefix isn't one of the two the plugin recognizes. Open an issue with the actual namespace and we'll add the alias.

## Install

Two install paths depending on which Anthropic app you use. They produce slightly different slash-command names but the same underlying behavior.

### Claude Code (CLI) — recommended

```
/plugin marketplace add iansmith/ticket-plugin
/plugin install ticket-plugin@ticket-plugin
```

After install, the commands are plugin-namespaced: `/ticket-plugin:start`, `/ticket-plugin:pause`, `/ticket-plugin:update`, `/ticket-plugin:archive`.

(The repo, the marketplace it hosts, and the plugin inside it all share the name `ticket-plugin` — hence the doubled-up install command.)

### Claude Desktop — manual install (band-aid until Claude Desktop supports plugins)

> **Why this exists as a separate path:** Claude Desktop currently has no `/plugin` manager and no built-in mechanism for installing third-party plugins from a marketplace — only Claude Code (CLI) does. Claude Desktop *does* load standalone slash commands from `~/.claude/commands/`, so this installer is a stopgap that drops the four ticket commands there directly, bypassing the marketplace entirely.
>
> This is a band-aid, not a long-term solution. We have no real choice but to do it this way until Claude Desktop ships plugin install support — when that lands, this section becomes obsolete and Claude Desktop users will use the marketplace install above like everyone else. Until then, the trade-offs you accept by using this path:
>
> - No auto-updates — you re-run the installer to get new versions.
> - No appearance in any plugin manager UI — the commands just exist in your `~/.claude/commands/`.
> - No managed install scope (user vs. project vs. local) — everything is user-scoped, shared across all projects.
> - The slash commands are un-namespaced (`/ticket-start` instead of `/ticket-plugin:start`) — slightly nicer to type, but inconsistent with the CLI install.

Install:

```bash
curl -fsSL https://raw.githubusercontent.com/iansmith/ticket-plugin/master/install-for-claude-desktop.sh | bash
```

After install, the commands appear as `/ticket-start`, `/ticket-pause`, `/ticket-update`, `/ticket-archive`. Restart Claude Desktop if they don't show up in autocomplete.

To pin to a specific tagged version:

```bash
TICKET_PLUGIN_REF=v1.0.0 bash <(curl -fsSL https://raw.githubusercontent.com/iansmith/ticket-plugin/v1.0.0/install-for-claude-desktop.sh)
```

To update later, re-run the installer (it overwrites). To uninstall, remove `~/.claude/commands/ticket-{start,pause,update,archive}.md`.

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
$ /ticket-plugin:start MAZ-26                # fresh-start: transitions to In Progress, seeds tracking dir

# ... work happens ...

$ /ticket-plugin:update                      # mid-session checkpoint to progress.md

# ... more work ...

$ /ticket-plugin:pause                       # interrupted; capture state, clear active pointer

# ... later, possibly in a fresh Claude Code session ...

$ /ticket-plugin:start MAZ-26                # resume: reads tracking files, prints summary, re-activates

# ... finish the work ...
# Open a PR for review (commits uncommitted work, pushes, opens the PR,
# polls CodeRabbit, categorizes the review):
$ /ticket-plugin:pr

# ... iterate on review feedback ...

# When the PR is approved and CI is green, ship it:
$ /ticket-plugin:merge                       # merges PR + transitions ticket to Done + deletes branch + archives locally
```

The `:merge` command rolls "transition the ticket to Done + `/ticket-plugin:archive`" together with the GitHub PR merge and branch cleanup. If you'd rather drive each step yourself, the manual equivalent is:

```
# ... finish the work, merge the PR yourself, transition the ticket to Done on Linear/JIRA yourself ...
$ /ticket-plugin:archive                     # pushes task_plan → ticket description, findings → comment, archives locally
```

`:merge` is the same end-state as the manual flow, just with one confirmation prompt rather than three. It confirms once before doing any of the irreversible work and refuses safely on dirty trees, unpushed commits, draft PRs, or merge conflicts.

### Switching tickets within the same project

If a different ticket of the same prefix is already active, `/ticket-plugin:start <NEW-KEY>` automatically runs `/ticket-plugin:update` on the old one before switching — capturing where you left off without manual ceremony. No "are you sure" prompt.

### `/ticket-plugin:start` on the currently active ticket

No-op aside from the normal resume summary. Same as picking up where you left off.

## Tracking files — what's in them

Each ticket directory contains three markdown files:

- **`task_plan.md`** — the durable plan. Starts seeded with the ticket's original description; you fill in the **Plan** section as you scope work. This is what gets pushed back to the ticket's description on `/ticket-plugin:archive`.
- **`findings.md`** — investigation results: root causes, codebase facts, constraints, dead-ends ruled out. Pushed as a comment on `/ticket-plugin:archive` (unless it's template-empty).
- **`progress.md`** — per-session diary with `## Session`, `## Update`, and `## Pause` entries. **Never** pushed to the ticket system — too noisy for the durable record. Lives locally; commit history + the comment + the description are what the ticket carries.

## Design choices

A few decisions worth knowing about:

- **`/ticket-plugin:archive` refuses to archive unless the ticket is already in a terminal state on the ticket system.** The user transitions the ticket; this command syncs back. No "Claude marked my ticket done without telling me" failure mode.
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

`CURRENT-<PREFIX>` files are created and cleared by the plugin. `<TICKET>/` directories are created by `/ticket-plugin:start` and moved to `ticket-archive/` by `/ticket-plugin:archive`.

## License

MIT — see [LICENSE](LICENSE).

## Privacy

This plugin collects nothing about you or your usage — no telemetry, no analytics, no remote endpoints owned by the author. See [PRIVACY.md](PRIVACY.md) for the full statement, including a transparency note about what other tools (the Claude API, the Linear / Atlassian MCPs) your slash-command invocations naturally hit.

## Author

Ian Smith ([@iansmith](https://github.com/iansmith))
