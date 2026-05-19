---
description: End-to-end "ship it" for the active ticket. Use /ticket-plugin:merge to merge the PR, transition the ticket to Done on Linear/JIRA, delete the merged branch, and archive the local tracking dir — all in one go. Confirms once before any destructive remote operation. Refuses safely on dirty trees, unpushed commits, draft PRs, or merge conflicts. Auto-detects ticket system.
disable-model-invocation: true
---

# /ticket-plugin:merge

Merge the active ticket's PR, mark the ticket Done on the ticket system, delete the corresponding branch if cleanly merged, then archive the local tracking dir.

End-to-end "ship it" path. Irreversible. Confirms before each remote operation.

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

### Fetch current state (to pick the Done transition later and check we're not already done)

**JIRA:** Get cloudId via `mcp__atlassian__getAccessibleAtlassianResources` (cache). `mcp__atlassian__getJiraIssue($TICKET, cloudId, fields=["status","description"])`. Read `status.statusCategory.key`.

**Linear:** `mcp__linear-server__get_issue($TICKET)`. Read `state.type`.

If already terminal (JIRA `done` / Linear `completed`|`canceled`): note it. The merge can still proceed; the transition step becomes a no-op.

## Step 3 — Confirm with the user

Show the full plan and get explicit approval. This is the only confirmation prompt — all four remote actions happen on `yes`.

> About to merge $TICKET and ship it:
>
> 1. **Merge** PR #$PR (`$BRANCH` → `$baseRefName`) with strategy `$STRATEGY` via `gh pr merge`. (`--delete-branch` flag included; GitHub auto-deletes the remote branch if the merge succeeds.)
> 2. **Transition** $TICKET on $SYSTEM from `<current state>` to a terminal `done` state.
> 3. **Switch to `$baseRefName`, pull the merge from origin, push it to any other remotes** (mirrors / forks / upstream — if `git remote` lists anything besides `origin`), then **delete the local branch** `$BRANCH` (`gh pr view` already confirmed `state: MERGED`).
> 4. **Archive** local tracking (inlines the `/ticket-plugin:archive` body — pushes final task plan and findings comment to $SYSTEM, then `mv ~/.claude/ticket-active/$TICKET → ~/.claude/ticket-archive/$TICKET`, clear `CURRENT-$PREFIX`).
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

## Step 5 — Transition the ticket to Done

Skip this step if the ticket was already terminal in Step 2's fetch (use the same state for the confirmation message).

**JIRA:**
- `mcp__atlassian__getTransitionsForJiraIssue($TICKET, cloudId)`.
- Filter to transitions whose target has `statusCategory.key === "done"`.
- From that filtered set, exclude any whose target name matches `/won.?t do|cancel|reject|abandon|invalid|duplicate/i` — those are terminal-but-negative (work was not completed). We want positive completion.
- From what's left, pick the transition by name match, in order: `/^done$/i` (exact) → `/done|closed|resolved|merged|shipped|complete|fixed/i` (partial positive) → first.
- `mcp__atlassian__transitionJiraIssue($TICKET, cloudId, transitionId)`.
- If no positive-completion `done`-category transition exists (after exclusion), print `"Couldn't find a Done transition on $TICKET — transition manually on JIRA after this command finishes."` and continue to Step 6. (The user may have a non-standard workflow; we'd rather they fix it by hand than land in 'Won't Do' or 'Canceled' by accident.)

**Linear:**
- `mcp__linear-server__list_issue_statuses` for the issue's team.
- Filter to entries with `type === "completed"`. (Linear's `type === "canceled"` is already excluded by this filter — that's the negative-completion bucket on Linear.)
- From the filtered set, additionally exclude any whose name matches `/won.?t do|cancel|reject|abandon|invalid|duplicate/i`. (Defensive — `type === "completed"` *should* already exclude these, but teams sometimes misconfigure workflow types, so we also gate on the name.)
- From what's left, pick the state by name match, in order: `/^done$/i` (exact) → `/done|merged|shipped|complete|fixed|closed|resolved/i` (partial positive) → first.
- `mcp__linear-server__save_issue` with the issue id and `stateId = <chosen state id>`.
- If no positive-completion `completed`-type state exists (after exclusion), print `"Couldn't find a Done state on $TICKET — transition manually on Linear after this command finishes."` and continue to Step 6.

On any transition error: print the error and continue to Step 6. The PR is already merged; not fatal.

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
3. If `findings.md` has content beyond the template scaffold (any `## ` heading or prose past the placeholder), post it as a comment titled `## Findings (from local tracking)`. JIRA `mcp__atlassian__addCommentToJiraIssue` / Linear `mcp__linear-server__save_comment`.
4. `mv ~/.claude/ticket-active/$TICKET → ~/.claude/ticket-archive/$TICKET` (rename to `~/.claude/ticket-archive/$TICKET-<timestamp>` on collision).
5. `: > ~/.claude/ticket-active/CURRENT-$PREFIX`.

`progress.md` is intentionally NOT pushed.

## Step 8 — Confirm

```
Shipped $TICKET.

PR:      #$PR merged ($STRATEGY, $MERGE_COMMIT) into $baseRefName
Ticket:  $TICKET transitioned to '<new state name>' on $SYSTEM ( or "already terminal" )
Remotes: $baseRefName pushed to <list of non-origin remotes> ( or "origin only" )
Branch:  local $BRANCH deleted; remote feature branch deleted by gh pr merge
Local:   archived to ~/.claude/ticket-archive/$TICKET/
Push:    description updated + findings comment posted ( or "description updated" / "skipped" )
```

## Rules

- Confirms ONCE in Step 3 before any destructive remote action. After that, run to completion or fail loudly.
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
