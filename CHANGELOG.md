# Changelog

All notable changes to this plugin will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-05-16

### Added

- Initial public release.
- Four slash commands invoked under the `ticket-plugin` plugin namespace:
  - `/ticket-plugin:start <KEY>` — fresh-start or resume work on a ticket. Fresh-start fetches the ticket, transitions it to **In Progress**, and seeds `task_plan.md`, `findings.md`, `progress.md`. Resume reads the tracking files and prints a summary.
  - `/ticket-plugin:update` — mid-session checkpoint to `progress.md`. The ticket stays active. Local-only.
  - `/ticket-plugin:pause` — snapshot state and clear the active-ticket pointer. Local-only.
  - `/ticket-plugin:archive` — push the final task plan back to the ticket as its description, post `findings.md` as a comment, and archive the local folder. Refuses unless the ticket is already in a terminal state on the ticket system.
- Auto-detection of ticket system (JIRA via Atlassian MCP, or Linear via Linear MCP). If both are configured in the same session, the skill asks rather than guessing.
- Per-project `.project-prefix` discipline: a single-line file in cwd names the ticket prefix (`MAZ`, `PLTF`, `LOU`, etc.) for that project. Skills only operate on tickets matching the cwd's prefix.
- Per-prefix `CURRENT-<PREFIX>` pointer (`~/.claude/ticket-active/CURRENT-MAZ`, etc.) lets parallel sessions on different projects work without interference.
- Tracking files live at `~/.claude/ticket-active/<TICKET>/` while active and move to `~/.claude/ticket-archive/<TICKET>/` on archive. Independent of any git repo.

### Also included

- `install-for-claude-desktop.sh` — bash installer for Claude Desktop users, since Claude Desktop doesn't yet support `/plugin install`. Drops the four commands into `~/.claude/commands/` as `/ticket-start`, `/ticket-pause`, `/ticket-update`, `/ticket-archive` (un-namespaced — Claude Desktop loads them as standalone slash commands). The installer strips the SKILL.md YAML frontmatter and rewrites cross-references from `/ticket-plugin:<name>` to `/ticket-<name>` to match the standalone invocation form.
- `PRIVACY.md` — explicit statement that the plugin collects nothing about the user or their usage, with a transparency note about what other tools (Anthropic's Claude API, the Linear / Atlassian MCPs) the slash-command invocations naturally hit.
- README "Why this exists" section that names the three concrete use cases: per-ticket context isolation, parallel project work via `.project-prefix`, and durable record back to the ticket on archive.

### Notes for downstream consumers

- This plugin requires either the official Anthropic Linear or Atlassian plugin (from the `anthropics/claude-plugins-official` marketplace) to be installed. It is a wrapper around those MCPs and has no built-in API client of its own.
- Skills follow the modern `skills/<name>/SKILL.md` layout (with `disable-model-invocation: true` — these are explicit slash commands, not model-invoked auto-skills).
