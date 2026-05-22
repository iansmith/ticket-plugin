---
description: Start or resume work on a Linear or JIRA ticket. Use /ticket-plugin:start <KEY> (e.g. /ticket-plugin:start MAZ-26). Fresh-starts a new ticket (fetches it, transitions to In Progress, asks for a Conventional-Commits-style branch type and creates a feature branch like fix/MAZ-26 or feat/MAZ-26 — with a heuristic suggestion from labels/title and the choice between branching off the default branch vs the current branch when cwd is on a feature branch, plus a "skip" option to opt out of branch creation entirely — then seeds tracking files), or resumes an existing one. Auto-detects ticket system.
disable-model-invocation: true
---

# /ticket-plugin:start

Start or resume work on a ticket.

**On fresh-start:** transitions the ticket to In Progress, creates a feature branch named `<type>/<TICKET-ID>` (e.g. `fix/MAZ-99`, `feat/MAZ-99`) — `<type>` is a Conventional-Commits-style prefix chosen interactively, with a heuristic suggestion when one can be inferred from the ticket's labels or title; a `skip` option opts out of branch creation entirely — and seeds tracking files at `~/.claude/ticket-active/<TICKET>/`. If cwd is on a feature branch (not the repo default), the skill warns and asks whether to branch off the default branch or off the current branch.

**On resume:** reads the tracking dir, prints a summary, appends a session header. No ticket-system call, no git.

Auto-detects ticket system (JIRA via Atlassian MCP, or Linear via Linear MCP).

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
2. Read `~/.claude/ticket-active/CURRENT-$PREFIX`. Call its contents `$ACTIVE` (empty if the file is empty or missing). If `$ACTIVE` is non-empty AND `$ACTIVE != $ARGUMENTS`, inline the body of `/ticket-plugin:update` (don't actually invoke it as a slash command) to capture state on `$ACTIVE` before switching. (`/ticket-plugin:update` operates on `$ACTIVE` because that's what `CURRENT-$PREFIX` still points to.) Then continue.

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
- Both → ask: `"Both JIRA and Linear MCP are configured this session. Which should /ticket-plugin:start use? (jira / linear)"`
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

### Step 4 — Decide branch type and base ref

The branch will be named `<type>/$ARGUMENTS` (e.g. `fix/MAZ-99`, `feat/MAZ-99`). `<type>` is a Conventional-Commits-style prefix: `fix`, `feat`, `chore`, `docs`, `refactor`, `perf`, `test`, `ci`, `build`, `deploy`, `revert`. Custom values are allowed if they pass `git check-ref-format --branch "<type>/$ARGUMENTS"`.

#### 4a. Suggest a default type from the ticket data

Try to infer `<type>` from the ticket's labels and title (case-insensitive). First label match wins over title match. If multiple labels match different types, prefer in this order: `fix > feat > refactor > perf > docs > chore > test`.

| Signal | Match → suggest |
|---|---|
| Label | `bug`, `regression`, `hotfix`, `defect` → `fix` |
| Label | `feature`, `enhancement`, `story` → `feat` |
| Label | `chore`, `maintenance`, `cleanup`, `tech-debt`, `tech debt` → `chore` |
| Label | `docs`, `documentation` → `docs` |
| Label | `refactor`, `refactoring` → `refactor` |
| Label | `perf`, `performance` → `perf` |
| Label | `test`, `testing`, `qa` → `test` |
| Title | starts with `Fix `, `Bug:`, `Regression:`, or contains ` bug ` → `fix` |
| Title | starts with `Add `, `Implement `, `Build `, `Create `, `New ` → `feat` |
| Title | starts with `Refactor `, `Cleanup `, `Rename ` → `refactor` |
| Title | contains `documentation`, `README`, or `docs` (whole-word) → `docs` |

If no signal matches, no suggestion.

#### 4b. Ask the user for the type

**With a suggestion:**

```
Branch type for $ARGUMENTS?
  Suggested: <type>  (from label '<label-name>' / title heuristic)
  Choices:   fix | feat | chore | docs | refactor | perf | test | ci | build | deploy | revert | <custom> | skip
```

**Without a suggestion:**

```
Branch type for $ARGUMENTS? (no signal from labels or title)
  Choices: fix | feat | chore | docs | refactor | perf | test | ci | build | deploy | revert | <custom> | skip
```

User responses:
- One of the listed types → use it.
- A custom string → validate via `git check-ref-format --branch "<type>/$ARGUMENTS"`. On failure, refuse with `"Invalid branch type — '<input>' produces an invalid git branch name."` and re-ask.
- `skip` → set `$NEW_BRANCH = null`. Step 5 becomes a no-op; the user is opting out of branch creation entirely and will manage git themselves.

Set `$TYPE` from the response, then `$NEW_BRANCH = "$TYPE/$ARGUMENTS"`.

#### 4c. Determine the base ref

Skip this entire sub-step if `$NEW_BRANCH == null` (user picked `skip` in 4b).

- `$CURRENT_BRANCH = git branch --show-current`.
- `$DEFAULT_BRANCH = gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`. On `gh` failure, fall back to `git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`. If that also fails, ask: `"Couldn't auto-detect the default branch. Enter the default branch name (e.g. main, master, trunk):"`.

**If `$CURRENT_BRANCH == $DEFAULT_BRANCH`** (cwd is on the default branch) — no prompt. Set `$BASE_REF = "origin/$DEFAULT_BRANCH"` (always-fresh remote ref, avoids stacking on a stale local copy).

**If `$CURRENT_BRANCH != $DEFAULT_BRANCH`** (cwd is on a feature branch) — warn and ask:

```
You're currently on '$CURRENT_BRANCH', not '$DEFAULT_BRANCH'.
<if working tree is dirty:>     Working tree has uncommitted changes — they'll be carried onto the new branch either way.
<if $CURRENT_BRANCH has commits ahead of origin/$DEFAULT_BRANCH:> '$CURRENT_BRANCH' has N commits ahead of origin/$DEFAULT_BRANCH.

Where should '$NEW_BRANCH' be based?
  - $DEFAULT_BRANCH    (typical — clean stack off trunk)
  - $CURRENT_BRANCH    (stack the new work on top of '$CURRENT_BRANCH')

(default / current)
```

- `default` → `$BASE_REF = "origin/$DEFAULT_BRANCH"` (after `git fetch origin $DEFAULT_BRANCH`).
- `current` → `$BASE_REF = $CURRENT_BRANCH` (local ref; no fetch needed).

### Step 5 — Create the branch

Skip entirely if `$NEW_BRANCH == null` (user picked `skip` in Step 4b). Set `$BRANCH_OUTCOME = "skipped — user picked 'skip'"` and continue to Step 6.

#### 5a. If the branch already exists, switch to it instead of creating

- **Local:** `git rev-parse --verify "refs/heads/$NEW_BRANCH" 2>/dev/null` succeeds → `git switch "$NEW_BRANCH"`. Set `$BRANCH_OUTCOME = "switched to existing local branch '$NEW_BRANCH'"`. Skip 5b.
- **Remote only:** `git ls-remote --heads origin "$NEW_BRANCH"` returns a line → `git fetch origin "$NEW_BRANCH"`, then `git switch --track "origin/$NEW_BRANCH"`. Set `$BRANCH_OUTCOME = "tracked existing remote branch 'origin/$NEW_BRANCH'"`. Skip 5b.

#### 5b. Create fresh off `$BASE_REF`

- If `$BASE_REF` starts with `origin/`: `git fetch origin "<ref-after-origin/>"` first to ensure a current local view of the base.
- `git switch -c "$NEW_BRANCH" "$BASE_REF"`.
- Set `$BRANCH_OUTCOME = "created '$NEW_BRANCH' off '$BASE_REF'"`.

On any git failure (invalid base, ref-format edge case, conflicts the working tree introduces): print git's stderr verbatim and stop. **Do not seed the tracking dir** (Step 6) — leaving nothing partial means the user can fix the underlying git issue and re-run `/ticket-plugin:start` cleanly. The ticket is already transitioned to In Progress on the remote system, which is fine: re-run hits the "already In Progress" branch in Step 3a (idempotent).

### Step 6 — Seed the tracking dir

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
  Branch: <git branch --show-current after Step 5> (cwd: <pwd>) — $BRANCH_OUTCOME
  Transition: <"none — already In Progress" | "<old state> → In Progress" | "no transition available — change manually">
  ```
- Write `$ARGUMENTS` to `~/.claude/ticket-active/CURRENT-$PREFIX`.
- Print: `"Started $ARGUMENTS — tracking at ~/.claude/ticket-active/$ARGUMENTS/. <transition summary>. <branch summary>."` where `<branch summary>` is one of: `"On '$NEW_BRANCH' (created off '$BASE_REF')"` | `"On '$NEW_BRANCH' (existing branch)"` | `"Branch creation skipped — you're on '<git branch --show-current>'"`.

## Rules

- Fresh-start DOES transition the ticket to In Progress. Resume does NOT touch ticket-system state.
- **Fresh-start creates a feature branch named `<type>/$ARGUMENTS`** (e.g. `fix/MAZ-99`) unless the user picks `skip` in Step 4b. `<type>` is always chosen by the user — the skill may offer a heuristic suggestion from labels/title, but the user can override with any of the Conventional-Commits prefixes, a custom token that passes `git check-ref-format`, or `skip`.
- **Resume does NOT touch git.** It reads tracking files and writes a session header. If `progress.md` records a different branch than `git branch --show-current`, mention it but don't switch.
- **When cwd is already on a non-default branch at fresh-start time**, the skill warns and asks whether to base the new branch off the repo's default branch (clean stack off trunk — typical) or off the current branch (stacking on a feature branch). It never silently uses the current branch as base — too easy to accidentally stack on someone's WIP.
- Branch creation never uses `git push --force`, `git reset --hard`, or `git branch -D`. If `git switch -c` fails, the failure surfaces verbatim and the user resolves it manually.
- Tracking lives at `~/.claude/ticket-active/$ARGUMENTS/`, NOT in any repo. Survives `cd` between repos.
- Failure handling:
  - Ticket-system detection fails (neither MCP): error, don't seed, don't touch CURRENT, don't touch git.
  - Ticket fetch fails: error, don't seed, don't touch git.
  - Transition fails after a successful fetch: report, continue to branch creation + seeding. Note in `progress.md`.
  - Branch creation fails (Step 5): report git's stderr verbatim. **Don't seed the tracking dir** — re-running `/ticket-plugin:start` after the user fixes the git issue picks up cleanly (transition is idempotent via Step 3a).
  - Disk write fails (Step 6): report and stop. Branch has already been created/switched-to; that's fine — re-running on a clean disk re-seeds cleanly.
