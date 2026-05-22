# ticket-plugin

A Claude Code plugin that wraps the full lifecycle of a Linear or JIRA ticket — investigate, plan, work, PR, review, merge — with per-ticket tracking files that sync back to the ticket on close. Optional parallel-agent fanout in git worktrees when the work decomposes.

---

## The workflow

When you pick up a ticket, three things should land on disk: a plan you can act on, a place for investigation notes you'd otherwise re-derive next session, and a session log so a fresh Claude Code session can resume exactly where you left off. Any of that work should land back on the ticket itself when it closes — without manual copy-pasting.

The seven slash commands are the loop:

```
   /ticket-plugin:start <KEY>
            │
            ▼
   /ticket-plugin:plan [constraint]      ←─── optional but recommended
            │  ┌──────────────────────────────────────────┐
            │  │  Phase 0: red tests for desired behavior │
            │  │  Phase A: investigate                    │
            │  │  Phase B: write detailed plan            │
            │  │  Phase C-G: optional agent fanout in     │
            │  │             worktrees + auto-merge       │
            │  └──────────────────────────────────────────┘
            ▼
        ┌── (work happens) ──┐
        │                    │
   /ticket-plugin:update     /ticket-plugin:pause       (interrupted)
        │                                  │
        │                                  │      /ticket-plugin:start <KEY>
        │                                  └────┬────────────────────────────┘
        ▼                                       ▼
   /ticket-plugin:pr
            │  ┌─────────────────────────────────────────┐
            │  │  simplify → tests → commit → push → PR  │
            │  │  → CodeRabbit poll → categorize         │
            │  └─────────────────────────────────────────┘
            ▼
        (review iteration)
            │
            ▼
   /ticket-plugin:merge
            │  ┌──────────────────────────────────────────────┐
            │  │  gh pr merge → advance ticket one state      │
            │  │  (e.g. In Progress → In Review, not Done) → │
            │  │  push baseRef to all remotes → delete branch │
            │  │  → recommend whether to run :archive now     │
            │  └──────────────────────────────────────────────┘
            ▼
        (code shipped — wait here if ticket landed in
         an intermediate state like "In Review")
            │
            ▼
   /ticket-plugin:archive    ←─── once ticket is in a terminal Done-type state
            │  ┌──────────────────────────────────────────────┐
            │  │  push task_plan as ticket description + DoD  │
            │  │  comment + findings comment → mv tracking    │
            │  │  to ticket-archive/ → clear CURRENT-PREFIX   │
            │  └──────────────────────────────────────────────┘
            ▼
          done
```

A few properties of the workflow that matter:

- **Per-ticket context isolation.** Each ticket gets its own `task_plan.md`, `findings.md`, `progress.md` at `~/.claude/ticket-active/<TICKET>/`. When you're on `MAZ-26`, only `MAZ-26`'s notes load — not the dozen others you've touched recently.
- **Parallel project work.** `.project-prefix` files plus per-prefix `CURRENT-<PREFIX>` pointers let you have a Linear ticket active in one repo and a JIRA ticket active in another at the same time, in separate Claude sessions.
- **Durable record back to the ticket.** When you run `/ticket-plugin:archive` (after the ticket has reached a terminal state on the ticket system), the final task plan becomes the ticket's description, a timestamped DoD-confirmation comment walks each Definition-of-Done item with evidence, and the findings become a separate comment. The ticket itself becomes a record of what was actually done, not just a title and a merged PR diff. `/ticket-plugin:merge` does NOT do this — it ships the code and tells you whether to run `:archive` now or wait for QA.

---

## Tools you'll need

This plugin is a **wrapper around a ticket-system MCP and a GitHub backend** — it has no built-in API client of its own. Before installing, check what you have. Several of these tools are uncommon enough that you may need to install them.

### Required

- **Claude Code** with the plugin manager available (`/plugin` command). On Claude Desktop, see the "manual install" path below.
- **A ticket-system MCP** — one of:
  - **Linear plugin** from Anthropic's marketplace:
    ```
    /plugin marketplace add anthropics/claude-plugins-official
    /plugin install linear@claude-plugins-official
    ```
    The skills expect tools under `mcp__linear-server__*`.
  - **Atlassian (JIRA + Confluence) plugin** from the same marketplace:
    ```
    /plugin install atlassian@claude-plugins-official
    ```
    The skills expect tools under `mcp__atlassian__*`.
- **A `.project-prefix` file in each project's working directory.** Contains the ticket prefix for that project (`MAZ`, `PLTF`, `LOU`, …). The skills only ever operate on tickets matching the cwd's prefix.

### Required for `/ticket-plugin:pr` and `/ticket-plugin:merge`

- **A GitHub backend** — one of:
  - **Anthropic's GitHub plugin** (preferred when available, since the skills prefer MCP tools over CLI):
    ```
    /plugin install github@claude-plugins-official
    ```
    The skills look for `mcp__github__*` tools.
  - **The `gh` CLI** ([github.com/cli/cli](https://github.com/cli/cli)). The skills look in `/usr/local/bin/gh`, `~/.local/bin/gh`, `/opt/homebrew/bin/gh`, then `$PATH`. `gh auth status` must succeed.

### Optional but recommended

- **Claude Code's bundled `simplify` skill.** `/ticket-plugin:pr` invokes it on uncommitted changes before committing — runs a reuse/quality/efficiency pass. If you don't have it, `:pr` warns and asks before continuing.
- **[CodeRabbit](https://www.coderabbit.ai/)** installed on the repo. Free for open source. `/ticket-plugin:pr` polls for CodeRabbit's review comments after opening the PR. If you don't use CodeRabbit, pass `--no-poll` to skip the wait (or the skill times out after 15 minutes and continues).
- **A test command** the skills can invoke automatically. `/ticket-plugin:plan` Phase 0 and `/ticket-plugin:pr`'s pre-commit gate both want one. They auto-detect from common project files (`Taskfile.yml`, `package.json`, `Makefile`, `Cargo.toml`, `go.mod`, `pyproject.toml`) and ask the user once if detection fails — the answer is cached in `task_plan.md`.

---

## Install

Two install paths depending on which Anthropic app you use.

### Claude Code (CLI) — recommended

```
/plugin marketplace add iansmith/ticket-plugin
/plugin install ticket-plugin@ticket-plugin
```

After install, commands are namespaced: `/ticket-plugin:start`, `/ticket-plugin:plan`, etc.

(The repo, the marketplace it hosts, and the plugin inside it all share the name `ticket-plugin` — hence the doubled-up install command.)

### Claude Desktop — manual install (band-aid until Claude Desktop supports plugins)

> Claude Desktop currently has no `/plugin` manager and no built-in mechanism for installing third-party plugins from a marketplace — only Claude Code (CLI) does. Claude Desktop *does* load standalone slash commands from `~/.claude/commands/`, so this installer is a stopgap that drops the seven commands there directly, bypassing the marketplace entirely. This is a band-aid, not a long-term solution — when Claude Desktop ships plugin install support, this section becomes obsolete and Claude Desktop users will use the marketplace install above.

```bash
curl -fsSL https://raw.githubusercontent.com/iansmith/ticket-plugin/master/install-for-claude-desktop.sh | bash
```

After install, the commands appear as `/ticket-start`, `/ticket-plan`, etc. (un-namespaced).

To pin to a specific tagged version: `TICKET_PLUGIN_REF=v1.1.0 bash <(curl -fsSL https://raw.githubusercontent.com/iansmith/ticket-plugin/v1.1.0/install-for-claude-desktop.sh)`.

To uninstall: `rm ~/.claude/commands/ticket-{start,plan,pause,update,archive,pr,merge}.md`.

---

## Setup — `.project-prefix`

Every project where you'll run these commands needs a `.project-prefix` file at the repo root:

```bash
echo MAZ > .project-prefix    # Linear team MAZ
echo PLTF > .project-prefix   # JIRA project PLTF
echo LOU > .project-prefix    # whatever your prefix is
```

The plugin reads this on every invocation. **It only operates on tickets whose key matches the cwd's `.project-prefix`** — so a session in `~/mazzy/` (prefix `MAZ`) can never accidentally touch a `PLTF-*` ticket, even if another project has one active.

---

## The commands

### `/ticket-plugin:start <KEY>` — start or resume a ticket

```
/ticket-plugin:start MAZ-26
```

Two modes, decided automatically:

- **Fresh-start** (no local tracking dir for this ticket): fetches the ticket from Linear/JIRA, transitions it to In Progress, **creates a feature branch named `<type>/<TICKET>`** (e.g. `fix/MAZ-26`, `feat/MAZ-26`) — `<type>` is a Conventional-Commits-style prefix chosen interactively, with a heuristic suggestion when one can be inferred from the ticket's labels or title; a `skip` option opts out of branch creation entirely. If cwd is already on a non-default branch, the skill warns and asks whether to base the new branch off the default branch (typical, clean stack off trunk) or off the current branch (stacking on a feature branch). Then seeds `task_plan.md`, `findings.md`, `progress.md` at `~/.claude/ticket-active/MAZ-26/`.
- **Resume** (tracking dir already exists): reads the tracking files, prints a summary of where you left off, appends a `## Session <ts>` header to `progress.md`. No ticket-system call, no git.

If a different ticket of the same prefix is already active, `/ticket-plugin:start` runs `/ticket-plugin:update` on the old one first (captures its state) before switching. No "are you sure" prompt — same project, same `.project-prefix`, automatic switch.

`/ticket-plugin:start` on the *already-active* ticket is a no-op aside from the resume summary.

### `/ticket-plugin:plan [constraint]` — investigate and plan

```
/ticket-plugin:plan
/ticket-plugin:plan focus on the database layer only
```

Replaces `task_plan.md`'s empty `## Plan` section with a thorough plan grounded in real codebase investigation. The optional textual constraint scopes both investigation and the plan **literally** — out-of-scope work is excluded even if the ticket implies it.

Internally:

1. **Phase 0 — Red tests first.** Identifies the project's test command (auto-detect or ask once, cache in `task_plan.md`). Writes failing tests for the **expected** behavior the ticket describes — not for the current implementation. Runs them; expects them to fail. If they pass instead, surfaces it (the bug may already be fixed, or the tests aren't exercising the right behavior). Commits the red tests as a separate `[$TICKET] Phase 0: red tests` commit.
2. **Phase A — Investigation.** Uses the `Explore` subagent (when available) to map relevant modules, entry points, dependencies, constraints, and risks. Writes structured findings to `findings.md`.
3. **Phase B — Plan drafting.** Each work item gets `Files`, `Depends on`, `Parallel-safe with`, detailed sub-steps, and a `Done when` criterion (preferably "test X turns green" from Phase 0). Includes an explicit parallelism analysis.
4. **Phase C — Decision.** If fewer than 2 items are parallel-safe → print "serial execution" and stop. Otherwise continue.
5. **Phase D-G (parallel path only).** Pre-conditions (clean tree, base SHA, agent count cap), per-agent prompts, confirm-and-launch, monitor every 15 minutes with auto-stop on hard-stuck agents (60+ min no commits AND repeating errors), auto-merge with confirmation in dependency order.

The plan is always saved to disk before agents launch, so an abort at any stage leaves you with a usable plan.

### `/ticket-plugin:update` — mid-session checkpoint

```
/ticket-plugin:update
```

Appends a `## Update <ts>` section to `progress.md` capturing: branch, HEAD, working-tree state, completed-since-last-snapshot, current state, next step. Pure local, no MCP calls. The ticket stays active.

Use this when you've made meaningful progress and want context to survive even if the Claude session unexpectedly ends.

### `/ticket-plugin:pause` — interrupted

```
/ticket-plugin:pause
```

Like `/ticket-plugin:update`, but with two differences: the section header is `## Pause` (richer template — captures last completed, next step, open questions, mental context), and it **clears** `CURRENT-<PREFIX>`. The ticket stays alive; it's just not the active ticket anymore. Resume by running `/ticket-plugin:start <KEY>` again later.

### `/ticket-plugin:pr` — open a pull request

```
/ticket-plugin:pr
/ticket-plugin:pr --base develop
/ticket-plugin:pr --no-simplify --no-test
```

End-to-end PR creation:

1. **Simplify.** Invokes Claude Code's `simplify` skill on uncommitted changes. If simplify made changes, surfaces them for user confirmation before committing.
2. **Pre-commit tests.** Auto-detects or asks for the test command, runs it. On failure, refuses to commit by default (offers `fix` / `commit anyway` / `abort`).
3. **Commit.** Stages everything, generates a ticket-anchored commit message (`[$TICKET] <summary>` with body from `task_plan.md`'s Plan section), commits with the standard Co-Authored-By trailer. Never `--no-verify`.
4. **Push.** `git push -u origin $BRANCH` (or regular push if upstream exists). Never `--force`.
5. **Open PR.** Uses GitHub MCP if installed, else `gh` CLI. Body pulls Summary / Test plan from `task_plan.md`.
6. **CodeRabbit trigger.** If the PR's base isn't the repo default, posts `@coderabbitai review` to wake it up.
7. **Poll CodeRabbit.** Every 60s for up to 15 minutes. Substantive signal is non-zero inline comments OR a finalized review (`CHANGES_REQUESTED` / `APPROVED`). Walkthrough/acknowledgement comments don't end the poll early.
8. **Categorize.** Each inline comment is verified against the actual code (CodeRabbit hallucinates), then classified: 🔴 Should fix (bug/security/correctness), 🟡 Could fix (style/idiom/refactor with ROI), ⚪ Skip (premise wrong / contradicts convention / pure nit). Stops after presenting — never auto-applies.

### `/ticket-plugin:archive` — close the ticket loop manually

```
/ticket-plugin:archive
```

When you've already moved the ticket to a terminal state on the ticket system yourself: pushes the final `task_plan.md` to the ticket as its new description (with the original description preserved as an appendix), posts `findings.md` as a comment (skipped if template-empty), then `mv`s the local tracking dir to `~/.claude/ticket-archive/`.

Refuses to run if the ticket isn't already in a terminal state. The user controls the transition; this command syncs.

### `/ticket-plugin:merge` — ship the code

```
/ticket-plugin:merge
/ticket-plugin:merge --pr 123 --strategy squash
```

When the PR is review-approved and CI is green: merges the PR via `gh pr merge` (default strategy: squash), **advances the ticket by one state in its workflow** (NOT auto-Done — same-bucket transitions like "In Progress" → "In Review" are preferred over jumping to Done so the team's review / QA gates aren't skipped), propagates the merged-onto branch to all configured remotes, and deletes the local feature branch. The proposed next state is shown in the confirmation prompt before anything irreversible happens.

**`:merge` does NOT archive.** It leaves `~/.claude/ticket-active/$TICKET/` in place and `CURRENT-$PREFIX` still pointing at the ticket. The summary at the end recommends whether to run `/ticket-plugin:archive` now (✅ ticket landed in a terminal Done-type state) or to wait (⚠️ ticket landed in an intermediate state like "In Review" where QA still needs to verify). This separation exists because `:archive` pushes the final task plan as the ticket description and posts a timestamped DoD-confirmation comment — both premature on a workflow where "In Review" is a real gate, not paperwork.

> **`:merge` vs `:archive`** — they're properly separate steps in the lifecycle:
> - `:merge` ships the **code**: PR merged, ticket advanced one state, branch cleaned up. Local tracking left intact; `CURRENT-$PREFIX` still points at the ticket.
> - `:archive` ships the **record**: pushes the final plan as the ticket description, posts the DoD-confirmation + findings comments, moves the local tracking dir to `ticket-archive/`, and clears `CURRENT-$PREFIX`. Refuses unless the ticket is already in a terminal state on the ticket system.
>
> For most teams you'll run `:merge`, wait for QA / review / sign-off to move the ticket to a Done-type state, then run `:archive`. For workflows where In Progress → Done has no intermediate state, you can run `:archive` immediately after `:merge`. The Step 7 recommendation tells you which case applies.

Confirms once before any destructive remote action. Refuses safely on dirty trees, unpushed commits, draft PRs, merge conflicts, or mismatched head refs. Never uses `gh pr merge --admin` to bypass branch protection.

---

## A scenario — fixing a wrong-subscriber webhook bug

Walking through all seven commands on a fictional ticket. Output is illustrative — eliding for space where the full thing would be too long.

### The setup

You're a backend engineer on a webhooks team. A Linear ticket lands in your queue:

> **MAZ-99 — Webhooks deliver to wrong subscriber after subscription renewal**
>
> Reports from customers: when they renew a subscription, the next webhook(s) sometimes deliver to the *previous* subscriber's destination (the one whose subscription the renewal replaced). Started after the v2.4 release. Repro: create sub A pointed at endpoint E1, renew it (which canonically updates the endpoint to E2), send a webhook, observe it landing at E1 instead of E2.

Open Claude Code in `~/webhooks-service`, which has `.project-prefix` set to `MAZ`.

### 1. Start the ticket

```
> /ticket-plugin:start MAZ-99

Branch type for MAZ-99?
  Suggested: fix  (from label 'bug')
  Choices:   fix | feat | chore | docs | refactor | perf | test | ci | build | deploy | revert | <custom> | skip

> fix

Created branch 'fix/MAZ-99' off 'origin/master'.

Started MAZ-99 — tracking at ~/.claude/ticket-active/MAZ-99/. Linear → In Progress. On 'fix/MAZ-99' (created off 'origin/master').
```

`~/.claude/ticket-active/MAZ-99/task_plan.md` now exists:

```markdown
# MAZ-99 — Webhooks deliver to wrong subscriber after subscription renewal

**Ticket system:** Linear
**State:** In Progress
**Assignee:** Ian Smith
**Priority:** High
**Labels:** webhooks, data-integrity
**Ticket URL:** https://linear.app/example/issue/MAZ-99
**Started:** 2026-05-19

## Original description (snapshot at start)

Reports from customers: when they renew a subscription, the next webhook(s)
sometimes deliver to the previous subscriber's destination… [eliding]

## Plan

_(fill in as you scope the work)_
```

### 2. Plan the work

```
> /ticket-plugin:plan focus on the dispatcher + subscriber registry; tests are in tests/

Phase 0 — Red tests first (TDD)

  Test command auto-detected: `task test` (from Taskfile.yml). Cached in task_plan.md.

  Expected behaviors from MAZ-99:
    1. Webhook destination is resolved at delivery time, not queue time.
    2. After subscription renewal, queued webhooks deliver to the renewed endpoint.

  Wrote tests/webhook_renewal_test.go with two new tests:
    - test_webhook_destination_resolved_at_delivery_time
    - test_webhook_delivers_to_renewed_endpoint_after_subscription_renewal

  Running `task test`...

  test_webhook_destination_resolved_at_delivery_time            FAIL
  test_webhook_delivers_to_renewed_endpoint_after_subscription_renewal  FAIL

  RED state established. Committing as separate commit.

  [a1b2c3d] Phase 0: red tests for renewed-endpoint delivery

Phase A — Investigation

  Delegating to Explore subagent (scoped to internal/webhook/* and tests/)...
  [Explore runs; ~45 seconds]

  Findings appended to findings.md. Highlights:
    - Dispatcher caches *subscriber struct ref* at queue time (internal/webhook/dispatcher.go:142).
    - Subscriber struct is replaced (not mutated in place) on renewal (subscribers.go:88).
    - Result: queued webhooks hold a pointer to a stale struct.

Phase B — Plan drafted

  ## Plan
  
  **Constraint:** focus on the dispatcher + subscriber registry; tests are in tests/

  ### Work items
  1. Resolve subscriber at delivery time, not queue time
     - Files: internal/webhook/dispatcher.go
     - Depends on: none
     - Parallel-safe with: 2 (different file)
     - Detailed steps:
       a. Change Dispatcher.enqueue() to store subscriber_id (string) instead of *Subscriber
       b. Add Dispatcher.resolveAtDelivery() that looks up the current Subscriber by ID
       c. Update Dispatcher.deliver() to call resolveAtDelivery() before sending
     - Done when: test_webhook_destination_resolved_at_delivery_time turns green
  
  2. Emit invalidation event on subscription renewal
     - Files: internal/webhook/subscribers.go
     - Depends on: none
     - Parallel-safe with: 1 (different file)
     - Detailed steps:
       a. Add Subscribers.Renew(id) method that updates and emits Renewed event
       b. Update all renewal call sites to use Renew() instead of direct replacement
     - Done when: test_webhook_delivers_to_renewed_endpoint_after_subscription_renewal turns green
  
  ### Parallelism analysis
  - Items eligible for parallel: 1, 2 (different files, no shared mutable state)
  - Sequential: none
  - Recommended: parallel: 2 agents covering items 1 and 2; integration trivial

Phase C — Parallel path (2 agents)

Phase D — Pre-conditions
  Working tree: clean ✓
  Base branch: maz-99-renewal-fix @ a1b2c3d ✓

Plan ready: MAZ-99 — 2 work items, both parallel via 2 agents.

Launch agents now? (yes / save-only / abort)
```

Reply `yes`:

```
Launched 2 agents in background:

  agent-1: ~/.claude/worktrees/maz-99-agent-1/  branch: maz-99/agent-1-dispatcher  task: t_abc
  agent-2: ~/.claude/worktrees/maz-99-agent-2/  branch: maz-99/agent-2-subscribers task: t_def

Monitoring every 15 minutes...
```

After ~22 minutes, the monitor reports:

```
[15min check]  agent-1 commits=3 last_commit=2m ago  agent-2 commits=2 last_commit=4m ago
[completion]   agent-1 done (4 commits, 17 minutes)
[completion]   agent-2 done (3 commits, 22 minutes)

MAZ-99 — agent fanout complete.

  agent-1 (dispatcher):    status: done    commits: 4   branch: maz-99/agent-1-dispatcher
  agent-2 (subscribers):   status: done    commits: 3   branch: maz-99/agent-2-subscribers

Auto-merge agents' work back into maz-99-renewal-fix? (merge all / merge specific / skip / abort)
```

`merge all`:

```
Merging maz-99/agent-1-dispatcher into maz-99-renewal-fix... clean.
Merging maz-99/agent-2-subscribers into maz-99-renewal-fix... clean.

Merged 2 agent branches. New HEAD: e5f6789 [MAZ-99] merge agent-2: subscribers.go renewal events
```

### 3. Mid-session checkpoint

You've decided to add a small README change to the package. Before context might compact:

```
> /ticket-plugin:update

Updated tracking for MAZ-99.
Wrote: progress.md, task_plan.md (Plan updated with "README cleanup" sub-item)
Ticket is still active. Pause with /ticket-plugin:pause when done.
```

### 4. Pause (interrupted)

A meeting pulls you away. Quick pause:

```
> /ticket-plugin:pause

Paused MAZ-99.
Captured: progress.md
Resume with: /ticket-plugin:start MAZ-99
```

### 5. Resume the next morning

```
> /ticket-plugin:start MAZ-99

Resuming MAZ-99

  Last paused: 2026-05-19 21:14 UTC
  Branch when paused: maz-99-renewal-fix
  Last completed: merged agent branches, added README note
  Next step: run full test suite, open PR
  Open questions: none
```

### 6. Open the PR

```
> /ticket-plugin:pr

Step 1 — Simplify pass
  Invoking simplify... no changes needed. Working tree unchanged.

Step 2 — Run relevant tests
  Test command: task test (cached in task_plan.md)
  Running...
  
  test_webhook_destination_resolved_at_delivery_time             PASS  ✓
  test_webhook_delivers_to_renewed_endpoint_after_subscription_renewal  PASS  ✓
  ... 87 other tests PASS
  
  All 89 tests passed. Continuing to commit.

Step 3 — Commit
  [b9c8d7e] [MAZ-99] Resolve subscriber at delivery time + emit renewal events

Step 4 — Push
  Pushed maz-99-renewal-fix to origin.

Step 5 — Create PR
  Backend: MCP (github plugin)
  PR created: https://github.com/example/webhooks-service/pull/247 (target: master)

Step 6 — Poll CodeRabbit
  Waiting for CodeRabbit (1/15)...
  Waiting for CodeRabbit (2/15)...
  CodeRabbit feedback received: 4 inline comments, 1 finalized review

Step 7 — Categorize

CodeRabbit review of PR #247 — 4 inline comments, 1 review (CHANGES_REQUESTED)

🔴 Should fix (1):

  📄 internal/webhook/dispatcher.go:158
     CodeRabbit: "resolveAtDelivery() returns nil if the subscriber was deleted between queue
                  and delivery; the caller dereferences it without a nil check, which will
                  panic..."
     Verdict:    Add nil-check; on nil, log + drop the webhook with reason "subscriber deleted".
     Why:        Verified — line 162 dereferences sub.Endpoint without a guard. Real failure
                 mode if a subscription is canceled after a webhook is queued. The phase-0
                 tests don't cover deletion, only renewal.

🟡 Could fix (2):

  📄 internal/webhook/subscribers.go:103
     CodeRabbit: "Renewed event is emitted before the lock is released; downstream listeners
                  may observe inconsistent state if they re-read..."
     Verdict:    Defer event emission until after the lock is released.
     Why:        Minor ordering issue; downstream listeners are best-effort caches that
                 re-resolve on each delivery anyway. Worth doing for cleanliness, not blocking.

  📄 internal/webhook/dispatcher.go:142
     CodeRabbit: "Storing subscriber_id as string when it's a UUID elsewhere — consider using
                  the uuid.UUID type for type safety."
     Verdict:    Switch to uuid.UUID for consistency.
     Why:        Codebase uses uuid.UUID elsewhere for subscriber IDs (verified by grep). Small
                 refactor, worth doing.

⚪ Skip (1):

  📄 tests/webhook_renewal_test.go:34
     CodeRabbit: "Consider extracting the subscriber-setup boilerplate into a helper for
                  reuse..."
     Verdict:    Skip
     Why:        Stylistic nit, no functional benefit. The two tests have ~6 lines of shared
                 setup; not worth the indirection.

Walkthrough summary:
  The PR addresses MAZ-99 by storing subscriber IDs (rather than struct refs) at queue time
  and resolving at delivery. Adds a Renewed event for subscription renewal. Approach looks
  sound; CodeRabbit flagged one runtime risk (subscriber deletion race) and a few minor
  cleanups.

PR: https://github.com/example/webhooks-service/pull/247
```

You decide to apply the 🔴 Should-fix (nil check) and one of the 🟡 Could-fix items (UUID type). You make the edits manually, then re-run `/ticket-plugin:pr` — the second invocation skips simplify (clean), runs tests (still green), commits the fixup, pushes, and re-polls. CodeRabbit returns APPROVED this time.

### 7. Ship the code

The team reviewer ACKs. CI is green. Time to merge:

```
> /ticket-plugin:merge

About to merge MAZ-99 and ship the code:
  1. Merge PR #247 (maz-99-renewal-fix → master) with strategy squash
  2. Advance MAZ-99 on Linear by one state: In Progress → In Review
     (one step forward, NOT auto-Done — your team's QA / deploy gate
     between In Review and Done is preserved. Say no if this isn't right.)
  3. Switch to master, pull, push master to any non-origin remotes, delete local maz-99-renewal-fix

Local tracking (ticket-active/MAZ-99/) and the ticket description are NOT touched.
After the merge, the summary will tell you whether to run /ticket-plugin:archive
now or wait.

Proceed? (yes / no / merge-only)

> yes

Shipped MAZ-99.

PR:      #247 merged (squash, abc1234) into master
Ticket:  MAZ-99 advanced from 'In Progress' to 'In Review' on Linear
Remotes: master pushed to: origin (up to date via gh pr merge), mirror
Branch:  local maz-99-renewal-fix deleted; remote feature branch deleted by gh pr merge
Local:   ticket-active/MAZ-99/ untouched; CURRENT-MAZ still points to MAZ-99

Next step:
  ⚠️ Ticket is now in 'In Review', which is NOT a terminal/Done state on this
     workflow. Do NOT run /ticket-plugin:archive yet — the task plan would land
     on the ticket while QA/review is still in progress, and the local tracking
     dir would move out of ticket-active/ prematurely. Wait until the ticket
     reaches a Done-type state (typically after QA sign-off), then run
     /ticket-plugin:archive.
```

MAZ-99 sits in "In Review" on Linear. QA picks it up, verifies the fix against the renewed-endpoint scenario, and moves it to "Done" themselves. You get a Slack ping from the QA lead. Now it's time to close the loop locally.

### 8. Archive — close the loop

```
> /ticket-plugin:archive

About to archive MAZ-99 (currently in 'Done'):
  1. Update Linear description with final task plan (original desc preserved as appendix)
  2. Post a Definition of Done — Confirmation comment walking each DoD item with evidence
  3. Post a Findings comment with the contents of findings.md
  4. mv ~/.claude/ticket-active/MAZ-99/ → ~/.claude/ticket-archive/MAZ-99/
  5. Clear ~/.claude/ticket-active/CURRENT-MAZ

Proceed? (yes / no / skip-push)

> yes

Archived MAZ-99 (was 'Done' on Linear).

Push: description updated + DoD-confirmation comment + findings comment posted
Local: archived to ~/.claude/ticket-archive/MAZ-99/
```

The Linear ticket now has:
- The completed task plan as its description (with the original description preserved as an appendix)
- A timestamped "Definition of Done — Confirmation" comment with ✅/⚠️ evidence per DoD item
- A "Findings" comment containing the investigation notes
- State: Done

Three weeks later when someone re-reads MAZ-99 to understand what changed, they see real engineering context — not just a title and a merged PR diff.

---

## Tracking files — what's in them

Each ticket directory contains three markdown files:

- **`task_plan.md`** — the durable plan. Starts seeded with the ticket's original description; `/ticket-plugin:plan` fills in the **Plan** section. This is what gets pushed back to the ticket's description on archive.
- **`findings.md`** — investigation results: root causes, codebase facts, constraints, dead-ends ruled out. Pushed as a comment on archive (unless template-empty).
- **`progress.md`** — per-session diary with `## Session`, `## Update`, and `## Pause` entries. **Never** pushed to the ticket system — too noisy for the durable record. Lives locally; the commit history + the findings comment + the description tell the durable story.

---

## Design choices

- **`/ticket-plugin:archive` and `:merge` refuse to mark a ticket Done unless it's already terminal on the ticket system.** The user controls the transition; the command syncs. No "Claude marked my ticket Done without telling me" failure mode. (`:merge` itself does the transition as part of its flow — but only after explicit confirmation.)
- **Per-prefix CURRENT pointer.** `CURRENT-MAZ`, `CURRENT-PLTF`, etc. are independent files. Parallel sessions on different project families don't conflict.
- **The plugin never touches git destructively.** No `--force`, no `--no-verify`, no `--admin`. It commits and merges with confirmation; the user resolves anything that requires those flags manually.
- **JIRA + Linear are first-class.** Detection is automatic. If both MCPs are configured in the same session, the command asks rather than guessing.
- **Tracking files live outside the repo** (`~/.claude/ticket-active/<TICKET>/`). They survive `cd` between repos and aren't tied to any branch.

---

## Storage layout

```
~/.claude/
  ticket-active/
    CURRENT-MAZ           ← active MAZ ticket key, or empty
    CURRENT-PLTF          ← active PLTF ticket key, or empty
    MAZ-26/
      task_plan.md
      findings.md
      progress.md
      .agents.json        ← only present during /ticket-plugin:plan agent fanout
    PLTF-2180/
      ...
  ticket-archive/
    MAZ-23/
      ...
```

`CURRENT-<PREFIX>` files are created and cleared by the plugin. `<TICKET>/` directories are created by `/ticket-plugin:start` and moved to `ticket-archive/` by `/ticket-plugin:archive` (or `:merge`).

---

## Compatibility & troubleshooting

The skills track tool names from Anthropic's marketplace MCPs as of release time. If your installed MCP is a different distribution (community fork, older version) with a different namespace, detection may report `"No ticket-system MCP found"` even though an MCP is installed. Open an issue with the actual namespace and we'll add the alias.

Currently expected tool namespaces:

- **Linear:** `mcp__linear-server__*` (specifically `get_issue`, `save_issue`, `save_comment`, `list_issue_statuses`).
- **Atlassian (JIRA):** `mcp__atlassian__*` (specifically `getJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue`, `getAccessibleAtlassianResources`, `getTransitionsForJiraIssue`, `transitionJiraIssue`).
- **GitHub:** `mcp__github__*` (PR create/comment/view tools) — falls back to `gh` CLI if not present.

---

## License

MIT — see [LICENSE](LICENSE).

## Privacy

This plugin collects nothing about you or your usage — no telemetry, no analytics, no remote endpoints owned by the author. See [PRIVACY.md](PRIVACY.md) for the full statement, including a transparency note about what other tools (the Claude API, the Linear / Atlassian MCPs, GitHub, CodeRabbit) your slash-command invocations naturally hit.

## Author

Ian Smith ([@iansmith](https://github.com/iansmith))
