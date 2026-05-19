---
description: End-to-end "ship it" for the active ticket. Use /ticket-plugin:merge to merge the PR, advance the ticket by one state in its workflow on Linear/JIRA (NOT auto-Done — same-bucket transitions like "In Progress" → "In Review" are preferred over jumps to Done so review/QA gates aren't skipped), delete the merged branch, and archive the local tracking dir — all in one go. Confirms once before any destructive remote operation; the confirmation prompt shows the specific computed next state so you know what you're agreeing to. Refuses safely on dirty trees, unpushed commits, draft PRs, or merge conflicts. Auto-detects ticket system.
disable-model-invocation: true
---

# /ticket-plugin:merge

Merge the active ticket's PR, advance the ticket by one state on the ticket system (not auto-Done — the workflow's "next" state, which is typically a review/QA step before Done), delete the corresponding branch if cleanly merged, then archive the local tracking dir.

End-to-end "ship it" path. Irreversible. Confirms before each remote operation, and the confirmation shows the specific computed next state so the user knows what they're agreeing to.

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or clear `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments

Optional `--pr <N>` to disambiguate when the current branch has more than one open PR. Optional `--strategy <squash|merge|rebase>` to override the default. Default strategy is `squash`.

The active ticket is whatever `~/.claude/ticket-active/CURRENT-$PREFIX` contains. If empty: `"No active $PREFIX ticket to merge."` and stop.

## Pre-flight

Run these in parallel:

- `$TICKET` = contents of `~/.claude/ticket-active/CURRENT-$PREFIX`. If empty or missing: stop.
- Verify `~/.claude/ticket-active/$TICKET/` exists. If not, state corruption — stop without writing anything.
- `$BRANCH` = `git branch --show-current`. If on the main branch (`main` or `master`): refuse with `"Refusing to merge: cwd is on the main branch, not a feature branch."`
- `$DIRTY` = `git status --porcelain`. If non-empty: refuse with `"Refusing: working tree has uncommitted changes. Commit or stash first."`
- `$AHEAD` = `git rev-list --count @{upstream}..HEAD` (or `0` if no upstream). If non-zero: refuse with `"Refusing: branch has N commits not pushed to origin. Push first."`
- `gh auth status`. If not authenticated: stop.

## Step 1 — Resolve the PR

If `--pr <N>` was given, use it directly. Otherwise:

```
gh pr list --head $BRANCH --state open --json number,title,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup --limit 5
```

- Zero open PRs on `$BRANCH`: refuse with `"No open PR found for branch $BRANCH. Create one first."`
- More than one: print the list and ask `"Multiple open PRs on $BRANCH; pass --pr <N> to choose."` and stop.
- Exactly one: that's `$PR`.

Then `gh pr view $PR --json number,title,headRefName,baseRefName,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,url`.

### Pre-merge gates (refuse-and-explain, no remote calls past this point)

Refuse with a clear reason if any:

- `state != OPEN` — `"PR #$PR is in state '$state', not OPEN."`
- `isDraft == true` — `"PR #$PR is a draft. Mark ready for review first."`
- `mergeable == CONFLICTING` — `"PR #$PR has merge conflicts. Resolve and re-push first."`
- `mergeable == UNKNOWN` — `"GitHub hasn't computed mergeability yet. Wait a few seconds and re-run."`
- `headRefName != $BRANCH` — `"PR #$PR's head ref is '$headRefName', not the current branch '$BRANCH'. Aborting to avoid merging the wrong PR."`

### Pre-merge soft warnings (mention, but allow proceeding via confirmation)

- `mergeStateStatus == BLOCKED` (e.g. required reviews not satisfied) — note it; the user may have a temporary admin-merge override planned.
- `mergeStateStatus == BEHIND` — note that base has new commits; user may want to rebase first.
- `reviewDecision == REVIEW_REQUIRED` or `CHANGES_REQUESTED` — note it.
- Any failing or pending status check in `statusCheckRollup` — list the failed/pending check names.

## Step 2 — Detect ticket system

Run two ToolSearches in parallel:

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__editJiraIssue,mcp__atlassian__getTransitionsForJiraIssue,mcp__atlassian__transitionJiraIssue,mcp__atlassian__addCommentToJiraIssue,mcp__atlassian__getAccessibleAtlassianResources", max_results=10)
ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__save_comment,mcp__linear-server__list_issue_statuses", max_results=8)
```

Set `$SYSTEM`:
- JIRA tools only → `JIRA`
- Linear tools only (`mcp__linear-server__*`) → `Linear`
- Both → ask: `"Both JIRA and Linear MCP are configured. Which is $TICKET on? (jira / linear)"`
- Neither → stop: `"No ticket-system MCP found. Configure Atlassian or Linear MCP and retry."`

### Fetch current state and compute the "advance one" target

The merge advances the ticket by **one** state in the workflow — not auto-Done. Plenty of teams have an intermediate review or QA state between In Progress and Done, and `gh pr merge` shouldn't skip past them. The computed target is shown in Step 3's confirmation prompt before anything irreversible happens, so if it's not what the user expects, they can abort.

**JIRA:**

- Get cloudId via `mcp__atlassian__getAccessibleAtlassianResources` (cache for the rest of the command).
- `mcp__atlassian__getJiraIssue($TICKET, cloudId, fields=["status","description"])`.
- Record `status.name` (current state name), `status.statusCategory.key` (current category: `new`, `indeterminate`, or `done`).
- `mcp__atlassian__getTransitionsForJiraIssue($TICKET, cloudId)` — available transitions from here.
- Compute `$NEXT_TRANSITION` in this order:
  1. **Exclude** transitions whose target name matches `/won.?t do|cancel|reject|abandon|invalid|duplicate/i` (negative completion).
  2. **Prefer same-category** transitions — ones whose target's `statusCategory.key` equals the current category. This is the "advance one slot within the same bucket" rule (e.g., from `indeterminate`, prefer another `indeterminate` like "In Review" rather than jumping to `done`).
  3. Within the same-category set, prefer target name matching `/review|qa|verify|test|pending|ready|merged|shipped/i` (forward-progress idioms).
  4. **If no same-category candidates exist** (workflow has no intermediate state), fall back to category-advancing transitions — i.e., `indeterminate` → `done`. Among those, prefer target name `/^done$/i` exactly, then `/done|closed|resolved|complete|fixed/i`.
  5. **If multiple still tie**, pick the first.
  6. **If nothing remains after exclusions**: `$NEXT_TRANSITION = null`. Note this for Step 3.

**Linear:**

- `mcp__linear-server__get_issue($TICKET)`.
- Record `state.name` (current), `state.type`, `state.position`.
- `mcp__linear-server__list_issue_statuses` for the issue's team — full set of states with their type + position.
- Compute `$NEXT_STATE` in this order:
  1. **Exclude** states with `type === "canceled"` and, defensively, states whose name matches `/won.?t do|cancel|reject|abandon|invalid|duplicate/i`.
  2. **Prefer same-type advance**: among states with `type === <current.type>` AND `position > current.position`, pick the one with the **smallest** position (the immediate next slot). E.g., from "In Progress" (`type: started`, `position: 2`), advance to "In Review" (`type: started`, `position: 3`).
  3. **If no same-type advance** exists, advance the type: pick the state with the **lowest position** among `type === "completed"` (the next bucket up). Apply the name preference `/^done$/i` then `/done|merged|shipped|complete|fixed|closed|resolved/i`.
  4. **If multiple still tie**, pick lowest position then first.
  5. **If nothing remains**: `$NEXT_STATE = null`. Note this for Step 3.

### Already-terminal handling

If the current state is already terminal (JIRA `statusCategory.key === "done"`, Linear `type ∈ {"completed", "canceled"}`): set `$NEXT_TRANSITION` / `$NEXT_STATE` to `null`. The merge can still proceed; the transition step becomes a clean no-op. Surface this in Step 3 as `"already terminal — no transition needed"`.

## Step 3 — Confirm with the user

Show the full plan and get explicit approval. This is the only confirmation prompt — all four remote actions happen on `yes`.

> About to merge $TICKET and ship it:
>
> 1. **Merge** PR #$PR (`$BRANCH` → `$baseRefName`) with strategy `$STRATEGY` via `gh pr merge`. (`--delete-branch` flag included; GitHub auto-deletes the remote branch if the merge succeeds.)
> 2. **Advance** $TICKET on $SYSTEM by one state: `<current state name>` → `<computed next state name>`. (Or `"<current> — already terminal, no transition"` / `"<current> — no forward transition available on this workflow"` if applicable.) This is one step forward, NOT auto-Done. If the workflow's next state isn't what you expected, say `no` and handle it manually.
> 3. **Switch to `$baseRefName`, pull the merge from origin, push it to any other remotes** (mirrors / forks / upstream — if `git remote` lists anything besides `origin`), then **delete the local branch** `$BRANCH` (`gh pr view` already confirmed `state: MERGED`).
> 4. **Archive** local tracking (inlines the `/ticket-plugin:archive` body — pushes final task plan as the description; if `task_plan.md` has a Definition of Done section, posts a timestamped DoD-confirmation comment walking each item with evidence from the merge/tests/progress; posts findings.md as a separate comment if non-empty; then `mv ~/.claude/ticket-active/$TICKET → ~/.claude/ticket-archive/$TICKET`, clear `CURRENT-$PREFIX`).
>
> <soft-warning summary if any: BLOCKED / BEHIND / failing checks / no review approval>
>
> Proceed? (yes / no / merge-only)

- `yes`: all four steps.
- `merge-only`: step 1 only — merge the PR, then stop. Do NOT touch the ticket system or local tracking.
- `no`: stop. No state changed.

If any soft warnings were present, append: `"Note the warnings above — confirming will proceed anyway."`

## Step 4 — Merge the PR

```
gh pr merge $PR --$STRATEGY --delete-branch --auto=false
```

(Explicitly NOT `--auto`; we want the merge to happen now or fail now.)

On failure:
- Print the error verbatim.
- Stop. Do not touch the ticket system. Do not touch local files. The branch is unchanged.

On success:
- `gh pr view $PR --json state,mergedAt,mergedBy,mergeCommit` → confirm `state == MERGED`. If not, treat as failure and stop.
- Capture `$MERGE_COMMIT` (the SHA of the merge/squash commit) for the confirmation message.

## Step 5 — Advance the ticket by one state

Step 2 already computed `$NEXT_TRANSITION` (JIRA) or `$NEXT_STATE` (Linear) — the next forward state in the workflow, with negative completions excluded. Step 3 already showed it to the user in the confirmation prompt. Step 5 just applies it.

**Skip Step 5 entirely** if:
- `$NEXT_TRANSITION` / `$NEXT_STATE` is `null` (already-terminal current state, or no forward transition available on this workflow). Note this in the Step 8 summary as `"already terminal — no transition"` or `"no forward transition available"` respectively.

**JIRA:**
- `mcp__atlassian__transitionJiraIssue($TICKET, cloudId, $NEXT_TRANSITION.id)`.

**Linear:**
- `mcp__linear-server__save_issue` with the issue id and `stateId = $NEXT_STATE.id`.

On any transition error: print the error and continue to Step 6. The PR is already merged; an inability to advance the ticket state isn't fatal. The user can transition manually after the fact.

> **Why advance one state and not auto-Done?** Most real workflows have intermediate states between "In Progress" and "Done" — typically a review or QA step the team uses to gate deployment. Auto-Done on PR merge skips those gates, which is wrong for most teams. Advance-one respects whatever shape the team's workflow happens to be. If your workflow has no intermediate state (just In Progress → Done), advance-one IS Done — because that's what your workflow's "next" actually is.

## Step 6 — Local branch cleanup + propagate the merge to other remotes

`gh pr merge --delete-branch` already handled the remote feature branch on origin. The local branch still exists, and any non-origin remotes (mirrors, upstream forks) still need the merged-onto branch pushed.

### 6a. Switch to the base and pull the merge

```
git fetch origin --prune
git switch $baseRefName
git pull --ff-only origin $baseRefName
```

### 6b. Push the merged-onto branch to all other remotes

`gh pr merge` only updated origin. If the repo has any other remotes configured (e.g. an `upstream` for a fork, a `mirror` for backup, an internal-vs-public pair), propagate `$baseRefName` to them now:

```
for remote in $(git remote); do
  [ "$remote" = "origin" ] && continue
  git push "$remote" "$baseRefName" || echo "  warning: push to $remote failed (continuing)"
done
```

This is best-effort — a failed push to a fork doesn't roll anything back. The merge already landed on origin (the source of truth); the warning surfaces so the user knows to fix the mirror manually. If `git remote` returns only `origin`, this loop is a no-op.

### 6c. Delete the local feature branch

The simple rule: "delete if the PR is logically merged." For squash/rebase merges the commits don't appear identical on the base, so `git branch -d` (safety check) would refuse. Use the merge confirmation we already have from Step 4:

- We have `state == MERGED` from `gh pr view` → the branch is logically merged regardless of strategy.
- `git branch -D $BRANCH` (force, since squash/rebase rewrites history).

If the working tree on the new base is dirty after pull (shouldn't happen — Step 6a just switched + pulled), refuse to delete the branch and report.

## Step 7 — Archive (inline `/ticket-plugin:archive` body)

Inline the `/ticket-plugin:archive` body — do NOT shell-invoke `/ticket-plugin:archive` (skills don't call other skills as commands). Re-use the cloudId / system from Step 2.

The ticket is now terminal (either transitioned in Step 5 or already was), so the terminal-state gate that `/ticket-plugin:archive` checks will pass.

Execute its Steps 4 (push) and 5 (archive) directly:

1. Build the new description: body of `task_plan.md` + `\n\n---\n\n## Original description (preserved)\n\n` + the existing description fetched in Step 2.
2. Update the ticket description: JIRA `mcp__atlassian__editJiraIssue` / Linear `mcp__linear-server__save_issue` with the new description. Do NOT touch state again.
3. **DoD-confirmation comment.** If `task_plan.md` has a `## Definition of Done` section (drafted by `/ticket-plugin:plan`), post a separate timestamped comment walking each DoD item with evidence — same format as `/ticket-plugin:archive` Step 4b. The merge context gives you strong evidence sources to cite per item: the merge commit `$MERGE_COMMIT`, the merged PR `$PR_URL`, the test results captured by the most recent `/ticket-plugin:pr` invocation, and any `## Update` entries in `progress.md` that documented verification. Use ✅ when evidence supports the item; use ⚠️ with a plain-language reason when it doesn't. Never fake a confirmation — surface the gap. Skip the comment entirely if `task_plan.md` has no `## Definition of Done` section.
4. If `findings.md` has content beyond the template scaffold (any `## ` heading or prose past the placeholder), post it as a separate comment titled `## Findings (from local tracking)`. JIRA `mcp__atlassian__addCommentToJiraIssue` / Linear `mcp__linear-server__save_comment`.
5. `mv ~/.claude/ticket-active/$TICKET → ~/.claude/ticket-archive/$TICKET` (rename to `~/.claude/ticket-archive/$TICKET-<timestamp>` on collision).
6. `: > ~/.claude/ticket-active/CURRENT-$PREFIX`.

`progress.md` is intentionally NOT pushed.

## Step 8 — Confirm

```
Shipped $TICKET.

PR:      #$PR merged ($STRATEGY, $MERGE_COMMIT) into $baseRefName
Ticket:  $TICKET advanced from '<old state>' to '<new state>' on $SYSTEM ( or "already terminal — no transition" / "no forward transition available" )
DoD:     <"confirmed — all N items ✅" | "confirmed with K warnings — N-K ✅, K ⚠️" | "no DoD section in task_plan.md, comment skipped">
Remotes: $baseRefName pushed to <list of non-origin remotes> ( or "origin only" )
Branch:  local $BRANCH deleted; remote feature branch deleted by gh pr merge
Local:   archived to ~/.claude/ticket-archive/$TICKET/
Push:    description updated + findings comment posted ( or "description updated" / "skipped" )
```

## Rules

- Confirms ONCE in Step 3 before any destructive remote action. After that, run to completion or fail loudly.
- **The ticket transition advances by ONE state in the workflow, not auto-Done.** Same-bucket transitions are preferred (e.g., "In Progress" → "In Review" over "In Progress" → "Done") so the team's review / QA gates aren't skipped. If the workflow has no intermediate state and the only forward option is Done, then Done is what happens — but that's because Done IS the next state, not because the skill assumed it. The proposed target is shown in Step 3's confirmation prompt; the user can say `no` if it isn't right.
- All-or-nothing on the PR merge (Step 4). If it fails, no other state changes.
- The ticket transition (Step 5) and archive push (Step 7's description update) are best-effort after the merge — surface failures but don't roll back. The PR is already merged; we can't un-ship.
- Branch deletion (Step 6) is the last destructive local action. Uses `gh pr view`'s authoritative `state: MERGED` rather than `git`'s commit-equivalence check, so squash and rebase merges work.
- Never run `git push --force`, `git reset --hard`, or skip pre-commit hooks. None of those are part of this flow.
- Never enable `--admin` on `gh pr merge` to bypass branch protection. If the merge is BLOCKED, surface the reason and ask the user to handle it.
- Failure handling per step:
  - **Pre-flight fails**: print reason and stop. No state changed.
  - **Step 1 (PR resolution) fails**: print reason and stop. No state changed.
  - **Step 4 (merge) fails**: print error, stop. No state changed.
  - **Step 5 (transition) fails**: print error, continue to Step 6. PR is merged.
  - **Step 6 (branch cleanup) fails** (e.g. uncommitted changes appeared): leave local branch in place, continue to Step 7. Report at the end.
  - **Step 7 (archive push) fails**: don't move the local dir. Report. The ticket is correctly in Done state remotely but lacks the final description push; user can re-run `/ticket-plugin:archive` later.
  - **Step 7 (local move) fails after push succeeded**: report. Re-run `/ticket-plugin:archive` later for the local cleanup.
