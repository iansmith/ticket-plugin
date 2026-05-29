---
description: Replace the active ticket's empty Plan section with a thorough, parallelism-aware plan grounded in real codebase investigation, starting with a Phase 0 that writes RED tests for the expected behavior. Also drafts a client-readable Definition of Done (plain-language observable outcomes) that ends up at the top of the ticket description on archive. Use /slopstop:plan [constraint] — the optional textual constraint scopes BOTH the investigation and the resulting plan literally. Phase 0's red tests anchor each work item's "Done when" criteria. The skill confirms before destructive actions (commit before fanout, agent launch, auto-merge); auto-stops hard-stuck agents (60+ min no commits AND repeating errors); never auto-merges without your explicit yes.
disable-model-invocation: true
---

# /slopstop:plan

Replace `task_plan.md`'s empty `## Plan` section with a thorough plan grounded in actual codebase investigation. Phase 0 writes red tests for the expected behavior FIRST, so the plan's "Done when" criteria are objective (a named test turning green) rather than prose-assertion. When the plan has parallel-safe work items, optionally fan them out across subagents in git worktrees and orchestrate them.

Three explicit confirmation gates: before committing on the user's behalf (if tree is dirty), before launching agents, before auto-merging.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. The branch-IS-selection parser only matches `$PREFIX-\d+`, so a branch encoding a different project's prefix correctly fails the no-match check.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /slopstop:gh-init (for GitHub) or create the file manually with system + key."`

## Arguments

`$ARGUMENTS` is an optional textual constraint. **It scopes both the investigation AND the resulting plan, literally.** Examples:

- `focus on the database layer only` → investigate only DB files; plan only DB work even if the ticket implies UI changes.
- `minimize changes to existing tests` → research test setup but don't include test-rewrite items.
- `prefer Go-idiomatic solutions` → frame the plan around Go conventions; verify against neighboring code first.
- `must use the existing config system` → investigate config; require plan items to extend, not replace it.

The constraint is **literal** — out-of-scope work is excluded from the plan even if the ticket text suggests it. The skill records the constraint at the top of the Plan section so a future reader knows what was deliberately excluded.

If `$ARGUMENTS` is empty, the plan covers everything implied by the ticket's description.

The active ticket is parsed from `git branch --show-current` (see Pre-flight). If empty: `"No active $PREFIX ticket to plan. Run /slopstop:start first."` and stop.

## Pre-flight (run in parallel)

- **Resolve active ticket from branch.** Parse `$TICKET` from the current git branch:
  - `$BRANCH = $(git branch --show-current)`
  - Find the first match of `$PREFIX-\d+` in `$BRANCH` (case-insensitive on `$PREFIX`; canonical-case the result).
  - No match → stop with `"Branch '$BRANCH' does not encode a $PREFIX ticket ID. Check out a ticket branch first, or run :start / :exp to create one."`
  - Match → `$TICKET` (e.g. `MAZ-43`, `BILL-2`).
- **In-flight check.** Verify `~/.claude/ticket-active/$TICKET/` exists. If not: stop with `"$TICKET is not in-flight. Run :start $TICKET first."`
- Verify `~/.claude/ticket-active/$TICKET/task_plan.md` exists. If not: state corruption — stop.
- `$BRANCH` = `git branch --show-current`. If on the main/master branch: refuse with `"Refusing to plan agent fanout from the main branch. Switch to a feature branch first."`
- `$BASE_SHA` = `git rev-parse HEAD` (the exact fork point if we end up launching agents).
- `$TICKET_TITLE` = first heading line of `task_plan.md`, stripped of the `# $TICKET — ` prefix.

Check if `task_plan.md`'s `## Plan` section already has content (anything beyond the seeded `_(fill in as you scope the work)_` placeholder):

- **Empty/seeded** — proceed silently.
- **Non-empty** — ask the user:
  > `## Plan` already has content. Replace, augment (append below the existing plan), or abort?

  On `abort`: stop. No state changed.

## Step 0 — Red tests first (TDD)

**Before** any investigation or planning, write failing tests for the **behavior the ticket says we want** — not for the current implementation. This is TDD's RED phase: tests are written based on the expected post-fix behavior and should fail on the current code.

Doing this first prevents the common failure mode of writing tests that just describe whatever the existing code happens to do.

### 0a. Identify the test command for the project

Look in `task_plan.md` for a `**Test command:**` line. If present, use it. Otherwise auto-detect from the cwd:

| Indicator | Test command |
|---|---|
| `Taskfile.yml` with a `test:` task | `task test` |
| `Makefile` with a `test:` target | `make test` |
| `package.json` with a `"test"` script + `pnpm-lock.yaml` | `pnpm test` |
| `package.json` with a `"test"` script + `yarn.lock` | `yarn test` |
| `package.json` with a `"test"` script (else) | `npm test` |
| `Cargo.toml` | `cargo test` |
| `go.mod` | `go test ./...` |
| `pyproject.toml` with pytest config | `pytest` |

If none match (or multiple plausibly do), ask the user once: `"What's the test command for this project? (paste it, or 'skip' to skip Phase 0)"`. On a real answer, **cache it** by writing a `**Test command:** <cmd>` line into `task_plan.md` (top of the file's frontmatter block, before `## Original description`). On `skip`: warn and continue to Step 1 without Phase 0.

### 0b. Identify expected behaviors from the ticket

Read `task_plan.md`'s `## Original description` carefully. List the behaviors the ticket claims should hold — these are what the red tests must exercise. Common shapes:

- "X should return Y when Z" → test asserting the return value
- "X should not happen after Y" → test asserting the state after Y
- "X must be Z-safe" → test exercising the unsafe path (race, concurrent access, large input, etc.)

If `$ARGUMENTS` constrains the scope, only include behaviors that fall within the constraint.

### 0c. Write the red tests

Find where existing tests live (use the project's conventions — `tests/`, `*_test.go`, `__tests__/`, etc.). Add new tests describing the expected behavior. Each test should:

- Have a clear name like `test_<expected_behavior>` (or whatever the project convention is).
- Use the existing test framework and fixtures — don't introduce new ones for Phase 0.
- Actually exercise the behavior (set up state, perform the action, assert the outcome). No stubs, no skipped tests.

Record the test file path(s) and test names — they're referenced in the plan in Step 2 as the verification criteria for work items.

### 0d. Run the tests; report results

Run the test command from 0a. One of three outcomes:

- **All new tests fail (RED state)** — expected. Print:
  ```
  Phase 0: <N> red tests written and failing as expected. RED state established.
    <test 1 name>  FAIL
    <test 2 name>  FAIL
    ...

  Proceeding to investigation.
  ```
  Continue to Step 1.

- **Some or all new tests pass** — surprising. Surface to the user:
  ```
  Phase 0: <N> of <M> new tests PASS on the current code.

    <test name>  PASS  (expected to fail; bug may not be present or test is wrong)
    ...

  Either the ticket's reported bug is already fixed, or the tests aren't exercising the right behavior. What would you like to do?

    - revise:        I'll re-read the ticket and rewrite the passing tests to actually exercise the buggy behavior.
    - continue:      Proceed anyway (you've decided the tests are correct and the ticket is questionable).
    - abort:         Stop here. Plan not generated.
  ```
  Act on the user's choice before continuing.

- **Tests don't run** (compile errors, missing dependencies, test framework not found, etc.) — stop:
  ```
  Phase 0: tests don't run cleanly.

  <captured error output>

  Fix the test harness, or revise the tests, and re-run /slopstop:plan.
  ```

### 0e. Commit the red tests

Once Phase 0's tests are in their RED state, commit them as a separate commit *before* moving on. This locks in the behavioral specification and makes the rest of the plan's "Done when" criteria objective (`<test X> turns green`).

```
git add <test-files-from-0c>
git commit -m "[$TICKET] Phase 0: red tests for <one-line summary of behaviors>" \
           -m "These tests describe the expected post-fix behavior. They fail on current code." \
           -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If the working tree had unrelated uncommitted changes before Phase 0 ran, do NOT include them in this commit — only stage the red-test files explicitly by path.

## Step 1 — Investigation

Goal: understand the codebase as it relates to the ticket's outcome, scoped by `$ARGUMENTS`. Writes findings to `findings.md`. Phase 0's red tests anchor what "done" means — investigation should keep them in mind.

### 1a. Read existing context

- `task_plan.md`'s `## Original description (snapshot at start)` section — that's the ticket scope as captured by `/slopstop:start`.
- `findings.md` — any prior investigation. Read but don't duplicate it.
- (Optional) Re-fetch the ticket fresh from Linear/JIRA for the current description, in case it was edited since start. Skip if the original description is recent enough.

### 1b. Apply the constraint

If `$ARGUMENTS` is non-empty, treat it as a hard scope. Areas explicitly excluded MUST NOT be investigated, even if they look relevant. Note the constraint in the investigation header so the next reader understands what's out of scope.

### 1c. Map the relevant code

Use the `Explore` subagent for the heavy lifting (keeps the orchestrator's context clean):

```
Agent(
  subagent_type: "Explore",
  description: "Investigate $TICKET",
  prompt: "<derived from ticket + constraint — see template below>"
)
```

Explore prompt template:

```
Investigate the codebase for ticket $TICKET ($TICKET_TITLE).

Ticket description:
<paste from task_plan.md's Original description>

Constraint on this investigation: <$ARGUMENTS or "none">

Find and report:
1. Relevant modules and file boundaries
2. Entry points (functions / types that any change would start from)
3. Dependencies (what the relevant code depends on; what depends on it)
4. Existing patterns to honor (conventions, public API contracts, etc.)
5. Risks (anti-patterns to avoid, fragile areas, places where changes ripple unexpectedly)

Stay within the constraint. Do not investigate areas the constraint excludes, even if they look interesting.

Report in structured markdown with the five headings above.
```

If the `Explore` subagent is unavailable, fall back to inline investigation: use `Grep`, `Glob`, and targeted `Read` calls on the same five questions. The output structure stays the same; only the mechanism changes.

### 1d. Write findings

Append to `findings.md`:

```markdown
## Investigation <UTC timestamp>

**Constraint:** $ARGUMENTS (or "none — full ticket scope")

### Relevant modules
<list with brief description of each>

### Entry points
<list of file:function:line where any change would start>

### Dependencies
<what depends on what; particularly call out cross-module dependencies that affect parallelism analysis>

### Constraints to honor
<existing patterns, public APIs, conventions visible in neighboring code>

### Risks
<fragile areas, tricky logic, places ripple unexpectedly>
```

## Step 2 — Draft the Definition of Done and the technical plan

Two related artifacts get written to `task_plan.md`: a client-readable **Definition of Done** (Step 2a, new in this section position) followed by the detailed technical **Plan** (Step 2b, the existing plan structure). Both come from the same source — the ticket description + Phase 0's red tests + Phase 1's investigation — but they speak to different audiences.

### 2a. Draft the Definition of Done (client-readable)

Audience: the person who filed the ticket (often a non-engineer client) and anyone reading the ticket later trying to figure out "was this actually done?". This section is **plain language, observable outcomes**, not implementation criteria.

Write it ABOVE the `## Original description` section so it appears at the top of the ticket description after `:archive` pushes the body. Format:

```markdown
## Definition of Done

This ticket will be considered complete when ALL of the following are true and observable:

1. **<plain-language outcome — what changes from the client's perspective>**
   How to verify: <a concrete check the client can do without reading code — e.g., "create subscription A, renew it pointing at endpoint B, send a test webhook, observe it lands at B not A">

2. **<plain-language outcome>**
   How to verify: <observable check>

...

If any of these aren't true at delivery, the ticket isn't done.
```

Guidelines:

- Items describe **what the client will observe**, not what the engineer will build. ("Renewed-subscription webhooks deliver to the renewed endpoint" — yes. "Dispatcher resolves subscriber at delivery time" — no, that's implementation.)
- Each item has a `How to verify:` that a non-engineer could execute. Reference UIs, dashboards, observable behavior, error messages. Don't reference test names or code symbols.
- **Avoid jargon.** No mentions of test fixtures, MCP tools, internal class names. The DoD is the part of `task_plan.md` that's literally written for the client to read.
- 2–5 items is typical. More usually means the ticket is too big and should be split.
- The DoD's items map 1:1 (or many-to-one) to Phase 0's red tests internally — the red tests are *how the engineer verifies the DoD*. But the DoD itself doesn't mention the tests.

If `$ARGUMENTS` excludes certain behavior from scope, the DoD must reflect that — explicitly list any in-scope ticket behaviors the constraint dropped, so the client doesn't expect them.

### 2b. Draft the technical Plan

Write the plan into `task_plan.md`'s `## Plan` section (replacing or augmenting per the pre-flight decision). The plan must be detailed enough that a separate Claude session could pick up an item and execute it without re-reading the codebase.

Format:

```markdown
## Plan

**Constraint:** $ARGUMENTS (or "none — full ticket scope")

### Work items

1. <descriptive name>
   - **Files:** <files this item creates, modifies, or deletes>
   - **Depends on:** <ids of items that must complete first, or "none" if independent>
   - **Parallel-safe with:** <ids it can run alongside without conflict; explain why (e.g. "different module, no shared mutable state")>
   - **Detailed steps:**
     a. <concrete sub-step>
     b. <concrete sub-step>
     c. ...
   - **Done when:** <verification criteria — preferably one or more of the red tests from Phase 0 turning green, e.g. "test_webhook_delivers_to_current_subscriber_after_renewal turns green" + any additional assertion like "existing test suite still passes">

2. <next item, same shape>
   ...

### Parallelism analysis

- **Items eligible for parallel execution:** <e.g. "1, 2, 4 — each touches an isolated module">
- **Sequential dependencies:** <e.g. "3 → after 1 (uses its output); 5 → after 2 and 4 (integration step)">
- **Recommended execution:** <"serial" | "parallel: N agents covering items [list]; serial integration after">
```

Two-item items with overlapping files are NOT parallel-safe even if they're logically independent — concurrent edits to the same file cause conflicts. The "Parallel-safe with" field must reflect actual file-level disjointness, not just logical independence.

## Step 3 — Decide: serial or parallel?

Look at the parallelism analysis from Step 2:

- **Fewer than 2 items are parallel-safe with each other** → serial path. Print:
  ```
  Serial execution — no agents needed.
  Plan written to ~/.claude/ticket-active/$TICKET/task_plan.md.
  Run /slopstop:update as you go to checkpoint progress; /slopstop:pr when ready.
  ```
  Stop.

- **2 or more items are parallel-safe** → continue to Step 4 (parallel path).

## Step 4 — Pre-conditions for parallel fanout

Before doing anything that requires worktrees, three hard gates:

### 4a. Clean working tree

`git status --porcelain`. If non-empty:

```
There are <N> uncommitted files. Agents need a clean starting point because they fork from your current branch.

Options:
  - commit:  create a single "[$TICKET] WIP checkpoint before parallel fanout" commit, then proceed.
  - stash:   git stash; you can git stash pop after agents finish.
  - abort:   stop and let you decide how to handle the uncommitted work.
```

On `commit`: `git add -A && git commit -m "[$TICKET] WIP checkpoint before parallel fanout"` with the standard Co-Authored-By trailer. Re-capture `$BASE_SHA` after the commit. Continue.
On `stash`: `git stash push -m "$TICKET pre-fanout"`. Continue. Print at the end a reminder to `git stash pop`.
On `abort`: stop.

### 4b. Confirm the fork point

```
Agents will fork from $BRANCH @ $BASE_SHA in isolated worktrees.

Is this the right base? (yes / abort)
```

On `abort`: stop.

### 4c. Agent count cap

If the parallelism analysis suggests more than 4 parallel agents:

```
Plan has K parallel items. More than 4 agents in parallel is hard to monitor effectively.

Options:
  - merge:   combine some items into bigger units so the count is ≤4 (you specify which to merge).
  - proceed: run all K agents anyway.
  - abort:   stop and replan.
```

## Step 5 — Draft per-agent prompts

For each parallel item, draft a self-contained prompt. Template (fill in the bracketed values):

```
You are agent <agent-id> working on ticket $TICKET ($TICKET_TITLE).

# Your slice of the work

<verbatim copy of the Step-2 work item: name, Files, Detailed steps, Done when>

# Context from investigation

<the subset of findings.md sections that matter for your slice — relevant modules, the entry points and constraints touching your files, any risks>

# Hard constraints — read these before anything else

1. You are running in an isolated git worktree at <worktree path>, on branch <agent branch>.
   You MUST NOT touch files outside this worktree. No exceptions.
2. You forked from $BRANCH at SHA $BASE_SHA. Do not merge other branches into your worktree, do not rebase, and do not push to origin.
3. Commit frequently to <agent branch> as you complete sub-steps. Aim for 3–10 commits across your work. Small commits make it easier to recover from off-track work.
4. Each commit message starts with `[$TICKET]`. End with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
5. Do not open PRs. Do not run /slopstop commands. The orchestrator handles integration after all agents finish.
6. If you finish your slice early, do NOT take on additional work. Report completion and stop.
7. If you get stuck and cannot make progress, commit what you have, report what blocked you, and stop. Do not loop on a dead end.

# Verification

<the "Done when" criteria from Step 2>

# Reporting

Report concisely on each major step. The orchestrator checks in every ~15 minutes and may auto-stop you if you appear hard-stuck (60+ minutes without commits AND repeating error output).
```

## Step 6 — Confirm and launch

Present the full plan + per-agent decomposition. **One confirmation** for the entire fanout:

```
Plan ready: $TICKET — <N> work items total, <K> running in parallel via agents.

All agents fork from $BRANCH @ $BASE_SHA into isolated worktrees.

Per-agent decomposition:
  1. <name>: <one-line summary>
     Files: <list>
     Done when: <criteria>
  2. <name>: ...
  ...

Launch agents now? (yes / save-only / abort)
  - yes:       create worktrees, launch agents in background, monitor every 15 minutes.
  - save-only: plan is saved in task_plan.md; you execute manually (use this if you want to review further or run agents yourself).
  - abort:     no launch; plan is still saved.
```

On `save-only` or `abort`: stop with appropriate message.
On `yes`: continue to Step 7.

## Step 7 — Launch agents

For each parallel item, spawn an agent in the background with a worktree:

```
Agent(
  subagent_type: "general-purpose",
  isolation: "worktree",
  run_in_background: true,
  description: "Agent <id> on $TICKET",
  prompt: <the full per-agent prompt from Step 5>
)
```

Capture each agent's task ID and resolved worktree path (from the Agent tool's spawn response). Record state in `~/.claude/ticket-active/$TICKET/.agents.json`:

```json
[
  {
    "id": "agent-1",
    "task_id": "<agent task id>",
    "worktree": "<resolved path>",
    "branch": "<branch name>",
    "items": ["1"],
    "status": "running",
    "started_at": "<UTC timestamp>",
    "last_check_at": null,
    "last_commit_at": null,
    "commits": 0,
    "stop_reason": null
  },
  ...
]
```

Print:

```
Launched <K> agents in background:

  agent-1: <worktree path>  branch: <branch>  task: <task-id>
  agent-2: ...

Monitoring every 15 minutes. Status updates appear here as agents progress.
Hard-stuck agents (60+ min no commits AND repeating errors) auto-stop.
```

## Step 8 — Monitor (15-minute cadence; auto-stop hard-stuck)

Run a background monitor that emits one status line per agent per tick. Use the `Monitor` tool with `persistent: true` and a polling script:

```bash
TICKET=$TICKET
STATE=~/.claude/ticket-active/$TICKET/.agents.json
BASE_SHA=$BASE_SHA
HARD_STUCK_MIN=60     # minutes without commits AND repeating errors
TICK=900              # 15 min in seconds

while true; do
  now=$(date -u +%s)
  for agent_id in $(jq -r '.[] | select(.status=="running") | .id' "$STATE"); do
    worktree=$(jq -r --arg id "$agent_id" '.[] | select(.id==$id) | .worktree' "$STATE")
    branch=$(jq -r --arg id "$agent_id" '.[] | select(.id==$id) | .branch' "$STATE")
    task_id=$(jq -r --arg id "$agent_id" '.[] | select(.id==$id) | .task_id' "$STATE")
    started_at_epoch=$(date -u -d "$(jq -r --arg id "$agent_id" '.[] | select(.id==$id) | .started_at' "$STATE")" +%s 2>/dev/null || echo "$now")

    # Count commits the agent has made since fork point
    commits=$(git -C "$worktree" rev-list --count "$BASE_SHA..$branch" 2>/dev/null || echo 0)

    # Last commit timestamp (epoch); falls back to start time if no commits yet
    last_commit_epoch=$(git -C "$worktree" log -1 --format="%ct" "$branch" 2>/dev/null || echo "$started_at_epoch")
    minutes_since=$(( (now - last_commit_epoch) / 60 ))

    # Recent task output (last ~40 lines) via TaskOutput on the agent
    # The orchestrator should fetch this via the TaskOutput tool — outline only here
    # recent_output="<TaskOutput agent_id=$task_id lines=40>"

    # Detect repeating errors: same error line repeated >=3 times in the last 40 lines of output
    # repeating_errors=<count of repeated error pattern in recent_output>

    # Hard-stuck condition: BOTH must be true
    #   - minutes_since >= HARD_STUCK_MIN
    #   - repeating_errors >= 3
    # If either alone, surface a warning but DO NOT auto-stop.

    status_line="agent=$agent_id commits=$commits last_commit_min_ago=$minutes_since"

    if [ "$minutes_since" -ge "$HARD_STUCK_MIN" ]; then
      # Inspect recent_output for repeating errors before deciding to auto-stop
      # If hard-stuck: TaskStop on $task_id; update state; emit a clear notification
      status_line="$status_line [warn: no commits in ${minutes_since}min]"
    fi

    echo "$status_line"
  done

  sleep $TICK
done
```

The monitor emits one line per agent per tick. Each line becomes a notification in the chat. The user can interrupt with TaskStop on the monitor itself.

**Auto-stop logic** — applied during each tick when evaluating a single agent:

- **Both conditions must hold:**
  1. The agent has gone 60+ minutes without a commit (`minutes_since_last_commit >= 60`).
  2. The agent's recent output (last ~40 lines) contains the same error message repeated 3+ times.
- If both true: call `TaskStop` on the agent's task_id. Update its state to `stopped` with `stop_reason: "auto-stop: <X>min no commits + repeating error '<excerpt>'"`. Emit a clear chat notification.
- If only one condition holds: emit a `[warn: ...]` flag in the status line but DO NOT auto-stop. Surface the warning so the user can intervene if they want.

**Completion detection** — when the `Agent` tool emits its completion notification for a task (Claude Code does this automatically for background agents), the orchestrator updates that agent's state to `done` (or `errored` if the agent exited with an error) and stops monitoring it.

The monitor exits when all agents are in a terminal state (`done` | `stopped` | `errored`). Then continue to Step 9.

## Step 9 — Final report and auto-merge (with confirmation)

When all agents are in a terminal state, print the full report:

```
$TICKET — agent fanout complete.

agent-1 (<name>):   status: done       commits: 7   worktree: <path>   branch: <branch>
agent-2 (<name>):   status: done       commits: 4   worktree: <path>   branch: <branch>
agent-3 (<name>):   status: stopped    commits: 2   worktree: <path>   branch: <branch>
                       reason: auto-stop: 62min no commits + repeating "X not found" error
...
```

### 9a. Offer auto-merge

Build the merge order from the Plan's dependency graph (Step 2's "Depends on" fields):

```
Auto-merge agents' work back into $BRANCH?

Merge order (by dependencies):
  1. agent-1 (no deps)
  2. agent-2 (no deps)
  3. agent-4 (depends on agent-1)
  ...

For each: git merge --no-ff <agent-branch> -m "[$TICKET] merge <agent-id>: <summary>".
Stops on first conflict; you resolve manually from there.

  - merge all                → run merges in order
  - merge specific <list>    → merge only the listed agents (e.g. "merge specific 1,2,4")
  - skip                     → print the manual recipe and stop
  - abort                    → no merge
```

### 9b. Execute the merge (if user opts in)

For `merge all` or `merge specific <list>`:

1. `git switch $BRANCH` (back to the user's working branch).
2. For each selected agent branch in dependency order:
   - `git merge --no-ff <agent-branch> -m "[$TICKET] merge <agent-id>: <summary from agent's work>"`.
   - If conflict: stop the merge sequence. Print:
     ```
     Conflict merging <agent-branch>. Resolve and commit manually:

       <list of conflicted files>

     After resolving:
       git add <files>
       git commit
       <remaining merge commands to run>
     ```
   - If clean: continue to next.
3. After all selected merges land cleanly: print:
   ```
   Merged <J> agent branches into $BRANCH.
   New HEAD: <sha> <subject>

   You can clean up agent worktrees with:
     git worktree remove <worktree-path>
   (or leave them in place to inspect later).
   ```

For `skip`: print the manual recipe (the same git commands you'd otherwise run) and stop.
For `abort`: print "No merge performed. Agent branches preserved at <list of paths>." and stop.

## Step 10 — Final confirm

```
Plan + execution complete for $TICKET.

Plan:          <N> work items, <K> parallelized
Investigation: appended to findings.md
Agents:        <K launched, M completed, X auto-stopped, Y errored>
Integration:   <"auto-merged <J> branches, HEAD now at <sha>" | "manual integration left to you" | "no agents launched">

Next: /slopstop:pr to open a PR for review.
```

## Rules

- **Phase 0 is mandatory** unless the user explicitly says `skip` when asked for the test command. The "Done when" criteria in the Step-2 plan are anchored to red tests turning green — without them, the plan loses its objective verification.
- **`task_plan.md` ends up with two complementary artifacts**: the client-readable Definition of Done (Step 2a — plain language, observable outcomes; ends up at the top of the ticket description on archive) and the technical Plan (Step 2b — test-anchored work items; ends up below the DoD). The DoD is what the client reads; the Plan is what the engineer (or the next AI session) reads.
- **Phase 0 surprises matter**: if the red tests pass on current code, surface that to the user. Either the bug is already fixed (the ticket is stale), or the tests aren't exercising the right behavior. Either way, the user needs to know before proceeding.
- **Three confirmation gates**: Step 4 (clean tree + base SHA + agent count), Step 6 (launch agents), Step 9 (auto-merge). The user can abort at any of them.
- **Worktree isolation is the contract**: agents are told the constraint in their prompt, and `Agent(isolation: "worktree")` enforces it at the tool level. Both belt and suspenders.
- **Conservative auto-stop**: 60+ min no commits AND repeating errors. **Both** must be true. Single-condition signals flag but don't auto-stop — the user decides.
- **`$ARGUMENTS` is literal**: out-of-scope work is excluded from both research and plan, even if the ticket text implies it. The constraint is recorded at the top of the Plan section so a future reader knows what was deliberately left out.
- **No auto-merge without explicit yes** in Step 9. The skill builds the merge order from dependency analysis and runs it on confirmation, but stops cleanly on first conflict and never `--force`s.
- **Plan is always saved before agents launch** — even if Steps 4 / 6 abort or all agents fail, the plan is on disk so the user can pick it up manually.
- **Per-agent commits are a strong norm, not enforced by the tool**: agents are instructed to commit 3–10 times; low-commit agents are flagged in the monitor but not auto-stopped on commit count alone.

### Failure handling

- **Pre-flight fails** (no active ticket, on main branch, plan section conflict): stop with reason. No state changed.
- **Phase 0 test command unknown** (user said `skip`): warn and continue without Phase 0. Work-item "Done when" criteria fall back to prose assertions.
- **Phase 0 tests pass unexpectedly**: surface to user with `revise / continue / abort` prompt. Don't proceed silently.
- **Phase 0 tests don't run** (compile errors, missing deps): stop. User fixes the test harness and re-runs.
- **Phase 0 commit fails** (pre-commit hook): print hook output, stop. User fixes and re-runs.
- **Investigation `Explore` subagent unavailable**: fall back to inline `Grep`/`Glob`/`Read`. Same output structure.
- **Plan write fails** (disk error, etc.): stop. Plan must be persisted before anything else can happen.
- **Step 4a commit fails** (pre-commit hook): print hook output, abort the fanout flow. User fixes manually and re-runs. Never `--no-verify`.
- **Step 7 agent launch fails** (worktree creation, etc.): stop, mark any already-spawned agents as orphan in the state file, surface the error.
- **Monitor poll fails** (transient git/file error): retry on next tick. Don't crash the monitor.
- **Agent auto-stop**: log the reason in state, emit a notification, continue monitoring other agents.
- **Auto-merge conflict**: stop the merge sequence at the conflict, surface conflicted files and the remaining merge commands, leave any successfully-merged commits in place. The user resolves and continues manually.
