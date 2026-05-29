# Privacy Policy

**TL;DR — this plugin collects nothing about you or your usage.** No telemetry. No analytics. No remote endpoints owned by the author. No opt-in / opt-out — there's literally no infrastructure to opt into.

## What the plugin is, technically

`slopstop` is a set of markdown skill files that instruct Claude Code's slash commands. The plugin author runs no servers, hosts no services, and has no infrastructure that could collect data about your usage even in principle. The full source is at [https://github.com/iansmith/slopstop](https://github.com/iansmith/slopstop) — you can read every line of it before you install.

## What stays on your machine

All tracking files are written to your local filesystem only:

- Active tickets: `~/.claude/ticket-active/<TICKET>/{task_plan,findings,progress}.md`
- Archived tickets: `~/.claude/ticket-archive/<TICKET>/...`
- Per-prefix active pointer: `~/.claude/ticket-active/CURRENT-<PREFIX>`
- Per-project prefix marker: `.project-prefix` in your repo (you create this; the plugin only reads it)

The plugin never transmits any of these files anywhere. You can read them, edit them, back them up, or `rm` them at any time.

## What goes off your machine via other tools

The plugin doesn't make API calls of its own — but the commands it ships *do* invoke other parts of your Claude Code session. Two flows leave your machine, and you should know about them:

1. **Claude Code → Anthropic.** Like any Claude Code conversation, when you invoke a slash command, the conversation contents (including the tracking-file text that gets loaded into context) are sent to Anthropic's Claude API for inference. Governed by [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy), not this plugin's.

2. **Linear MCP / Atlassian MCP → Linear / Atlassian.** When you run `/slopstop:start` or `/slopstop:archive`, the skill calls into whichever ticket-system MCP you have installed. Those MCPs make API requests directly to Linear or Atlassian to fetch tickets, transition state, update descriptions, and post comments. Governed by Linear's and Atlassian's privacy policies, not this plugin's. The plugin author has no visibility into those calls.

The only data this plugin ever pushes to your ticket system is the content you authored: your `task_plan.md` (as the ticket's new description) and your `findings.md` (as a comment). `progress.md` is intentionally never pushed.

## No telemetry, no analytics

There is no usage counter, no install ping, no error reporting, no version-check call, no anything that phones home. The plugin author cannot tell who has installed it, who is running it, or what tickets they're working on.

## Changes to this policy

If this policy ever changes — for example, if the plugin grows a feature that does need to phone home — it will be announced in [CHANGELOG.md](CHANGELOG.md) and the version will be bumped to clearly signal the change. The current version is the one tagged in the repo.

## Questions

Open an issue at [https://github.com/iansmith/slopstop/issues](https://github.com/iansmith/slopstop/issues).
