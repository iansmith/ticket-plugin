---
description: Start or resume work on a Linear or JIRA ticket. Use /tickets:start <KEY> (e.g. /tickets:start MAZ-26). Fresh-starts a new ticket (fetches it, transitions to In Progress, seeds tracking files), or resumes an existing one. Auto-detects ticket system.
disable-model-invocation: true
---

# /tickets:start

Start or resume work on a ticket. Tracking lives at `~/.claude/ticket-active/<TICKET>/`. Auto-detects ticket system (JIRA via Atlassian MCP, or Linear via Linear MCP).

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or modify `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments

`$ARGUMENTS` is a ticket key like `MAZ-26` or `PLTF-2180`. If empty, ask the user.

`$ARGUMENTS` must start with `$PREFIX-`. If not, refuse: `"$ARGUMENTS doesn't match this project's prefix ($PREFIX). cd to the right project first."`

## Two modes

- **Resume:** `~/.claude/ticket-active/$ARGUMENTS/` already exists with content → read state, summarize, hand back. **No ticket-system call. No transition.**
- **Fresh-start:** dir doesn't exist (or is empty) → detect ticket system, fetch the ticket, transition to In Progress, seed tracking files.

## Pre-flight

1. Validate `$ARGUMENTS` matches `^[A-Z]+-\d+$`. If not, ask for a valid ticket key and stop.
2. Read `~/.claude/ticket-active/CURRENT-$PREFIX`. Call its contents `$ACTIVE` (empty if the file is empty or missing). If `$ACTIVE` is non-empty AND `$ACTIVE != $ARGUMENTS`, inline the body of `/tickets:update` (don't actually invoke it as a slash command) to capture state on `$ACTIVE` before switching. (`/tickets:update` operates on `$ACTIVE` because that's what `CURRENT-$PREFIX` still points to.) Then continue.

Then fall through to Resume mode (if `~/.claude/ticket-active/$ARGUMENTS/` exists with content) or Fresh-start mode (if it doesn't).

## Resume mode (dir exists, non-empty)

- Read `~/.claude/ticket-active/$ARGUMENTS/{task_plan,findings,progress}.md`.
- Find the most recent `## Pause` or `## Session` header in `progress.md` for last-known state.
- Print:
  ```
  Resuming $ARGUMENTS

  Last paused: <date from progress.md, or "never">
  Branch when paused: <from progress.md>
  Last completed: <from progress.md>
  Next step: <from progress.md "Next" line, if present>
  Open questions: <from progress.md "Open" section, if present>
  ```
- Write `$ARGUMENTS` to `~/.claude/ticket-active/CURRENT-$PREFIX`.
- Append a `## Session <YYYY-MM-DD HH:MM>` header to `progress.md` with the line "Resumed".
- Stop. If `progress.md` records a different branch than `git branch --show-current`, mention it but don't switch — the user manages git.

## Fresh-start mode (dir doesn't exist or is empty)

### Step 1 — Detect ticket system

Run two ToolSearches in parallel (single message, two tool calls):

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__getAccessibleAtlassianResources,mcp__atlassian__getTransitionsForJiraIssue,mcp__atlassian__transitionJiraIssue", max_results=8)
ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__list_issue_statuses", max_results=8)
```

Set `$SYSTEM`:
- JIRA tools only → `JIRA`
- Linear tools only (`mcp__linear-server__*`) → `Linear`
- Both → ask: `"Both JIRA and Linear MCP are configured this session. Which should /tickets:start use? (jira / linear)"`
- Neither → stop: `"No ticket-system MCP found. Configure Atlassian or Linear MCP and retry."`

### Step 2 — Fetch the ticket

**JIRA:**
- Get cloudId via `mcp__atlassian__getAccessibleAtlassianResources` (cache for this command's lifetime).
- Fetch via `mcp__atlassian__getJiraIssue` with `issueIdOrKey=$ARGUMENTS, cloudId=<cached>, fields=["summary","description","status","assignee","priority","fixVersions","labels"]`.
- Read `status.statusCategory.key` ∈ `{"new", "indeterminate", "done"}`.

**Linear:**
- Fetch via `mcp__linear-server__get_issue` with `$ARGUMENTS`. Returns title, description, state, assignee, team, priority, labels, url.
- Read `state.type` ∈ `{"backlog", "unstarted", "started", "completed", "canceled"}` (the `type` field on the workflow state, not the state name).

### Step 3 — Transition to In Progress (if needed)

Three cases by *category* (JIRA `statusCategory.key`, Linear `state.type`):

**a. Already in progress** (JIRA `indeterminate`; Linear `started`) — skip transition. Note "already In Progress" in the confirmation.

**b. Pre-progress** (JIRA `new`; Linear `backlog` / `unstarted`) — transition:
- *JIRA:* `getTransitionsForJiraIssue` → pick a transition whose target has `statusCategory.key === "indeterminate"`. If multiple, prefer one whose name contains "progress" (case-insensitive); else first. Call `transitionJiraIssue` with that transition id. If no matching transition exists, print `"Couldn't find an In-Progress transition on $ARGUMENTS — transition manually on JIRA if needed."` and continue with seeding.
- *Linear:* Call `mcp__linear-server__list_issue_statuses` for the issue's team. Filter to entries with `type === "started"`. If multiple, prefer one whose name contains "progress"; else first. Call `mcp__linear-server__save_issue` with the issue id and `stateId = <chosen state id>`. If no `started`-type state exists, print warning and continue.

**c. Already done** (JIRA `done`; Linear `completed` / `canceled`) — ask before reopening:
- Print: `"Ticket $ARGUMENTS is in a terminal state ('<state name>'). Start work anyway? This will reopen it to In Progress. (yes / no)"`.
- `no` → stop. Don't create the tracking dir.
- `yes` → transition as in case (b).

### Step 4 — Seed the tracking dir

- Create `~/.claude/ticket-active/$ARGUMENTS/`.
- Write `task_plan.md`:
  ```markdown
  # $ARGUMENTS — <title from ticket>

  **Ticket system:** <JIRA | Linear>
  **State:** <current state name — use the new state if you transitioned>
  **Assignee:** <assignee or "unassigned">
  **Priority:** <priority label>
  **Labels / fixVersions:** <comma-joined>
  **Ticket URL:** <url>
  **Started:** <YYYY-MM-DD>

  ## Original description (snapshot at start)

  <description from ticket, verbatim>

  ## Plan

  _(fill in as you scope the work)_
  ```
- Write `findings.md`:
  ```markdown
  # $ARGUMENTS — Findings

  _(populated as investigation progresses)_
  ```
- Write `progress.md`:
  ```markdown
  # $ARGUMENTS — Progress

  ## Session <YYYY-MM-DD HH:MM>

  Started fresh from <JIRA | Linear> description.
  Branch at start: <git branch --show-current> (cwd: <pwd>)
  Transition: <"none — already In Progress" | "<old state> → In Progress" | "no transition available — change manually">
  ```
- Write `$ARGUMENTS` to `~/.claude/ticket-active/CURRENT-$PREFIX`.
- Print: `"Started $ARGUMENTS — tracking at ~/.claude/ticket-active/$ARGUMENTS/. <transition summary>."`

## Rules

- Fresh-start DOES transition the ticket to In Progress. Resume does NOT touch ticket-system state.
- Does NOT touch git. The user manages branches.
- Tracking lives at `~/.claude/ticket-active/$ARGUMENTS/`, NOT in any repo. Survives `cd` between repos.
- Failure handling:
  - Ticket-system detection fails (neither MCP): error, don't seed, don't touch CURRENT.
  - Ticket fetch fails: error, don't seed.
  - Transition fails after a successful fetch: report, but still seed the tracking dir. Note in `progress.md`.
  - Disk write fails: report and stop.
