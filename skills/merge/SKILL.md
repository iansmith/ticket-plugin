---
description: End-to-end "ship it" for the active ticket — code side only. Use /slopstop:merge to merge the PR, advance the ticket by one state in its workflow on Linear/JIRA (NOT auto-Done — same-bucket transitions like "In Progress" → "In Review" are preferred over jumps to Done so review/QA gates aren't skipped), and delete the merged branch. Does NOT archive local tracking or push the task plan back to the ticket — that's /slopstop:archive, which the user runs separately once the ticket actually reaches a terminal Done-type state (typically after QA). The end-of-run summary classifies the post-transition state and tells the user whether to run :archive now or wait. Confirms once before any destructive remote operation; the confirmation prompt shows the specific computed next state so you know what you're agreeing to. Refuses safely on dirty trees, unpushed commits, draft PRs, or merge conflicts. Auto-detects ticket system.
disable-model-invocation: true
---

# /slopstop:merge

Merge the active ticket's PR, advance the ticket by one state on the ticket system (not auto-Done — the workflow's "next" state, which is typically a review/QA step before Done), and delete the corresponding branch if cleanly merged. The local tracking dir (`~/.claude/ticket-active/$TICKET/`) and the ticket description are NOT touched — those belong to `/slopstop:archive`, which the user runs separately once the ticket has reached a terminal state.

End-to-end "ship it" path for the code side only. Irreversible. Confirms once before remote operations, and the confirmation shows the specific computed next state so the user knows what they're agreeing to. After completion, the summary classifies the post-transition state (terminal vs intermediate) and tells the user whether to run `/slopstop:archive` now or wait for QA.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. The branch-IS-selection parser only matches `$PREFIX-\d+`, so a branch encoding a different project's prefix correctly fails the no-match check.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /slopstop:gh-init (for GitHub) or create the file manually with system + key."`

## Arguments

Optional `--pr <N>` to disambiguate when the current branch has more than one open PR. Optional `--strategy <squash|merge|rebase>` to override the default. Default strategy is `merge` (real merge commit; preserves per-commit traceability for `git bisect`). Pass `--strategy squash` or `--strategy rebase` only when a specific PR genuinely benefits from collapsed history.

The active ticket is parsed from `git branch --show-current` (see Pre-flight). If empty: `"No active $PREFIX ticket to merge."` and stop.

## Pre-flight

Run these in parallel:

- **Resolve active ticket from branch.** Parse `$TICKET` from the current git branch:
  - `$BRANCH = $(git branch --show-current)`
  - Find the first match of `$PREFIX-\d+` in `$BRANCH` (case-insensitive on `$PREFIX`; canonical-case the result).
  - No match → stop with `"Branch '$BRANCH' does not encode a $PREFIX ticket ID. Check out a ticket branch first, or run :start / :exp to create one."`
  - Match → `$TICKET` (e.g. `MAZ-43`, `BILL-2`).
- **In-flight check.** Verify `~/.claude/ticket-active/$TICKET/` exists. If not: stop with `"$TICKET is not in-flight. Run :start $TICKET first."`
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

`.project-conf.toml`'s `system` field is authoritative for which backend to use; the ToolSearches resolve *how* to talk to it.

Run three ToolSearches in parallel:

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__editJiraIssue,mcp__atlassian__getTransitionsForJiraIssue,mcp__atlassian__transitionJiraIssue,mcp__atlassian__addCommentToJiraIssue,mcp__atlassian__getAccessibleAtlassianResources", max_results=10)
ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__save_comment,mcp__linear-server__list_issue_statuses", max_results=8)
ToolSearch(query="select:mcp__github__get_issue,mcp__github__add_issue_comment,mcp__github__update_issue,mcp__github__list_issue_comments", max_results=8)
```

Read `system` from `.project-conf.toml`. Set `$SYSTEM` (title-cased: `JIRA`, `Linear`, `GitHub`) and resolve the backend:

- **JIRA** — JIRA ToolSearch must be non-empty. If empty → stop: `"system='jira' in .project-conf.toml but no Atlassian MCP found. Configure it and retry."`
- **Linear** — Linear ToolSearch must be non-empty. If empty → stop: `"system='linear' in .project-conf.toml but no Linear MCP found. Configure it and retry."`
- **GitHub** — resolve `$GH_BACKEND`:
  - Canonical github ToolSearch non-empty → `$GH_BACKEND = "MCP"`, `$GH_MCP_NS = "mcp__github__"`.
  - Canonical empty → run fallback: `ToolSearch(query="select:mcp__plugin_github_github__get_me,mcp__plugin_github_github__add_issue_comment,mcp__plugin_github_github__issue_write", max_results=8)`. If non-empty → `$GH_BACKEND = "MCP"`, `$GH_MCP_NS = "mcp__plugin_github_github__"`.
  - Both empty → `$GH_BACKEND = "CLI"`. Find `gh` binary by trial path: `/usr/local/bin/gh`, `$HOME/.local/bin/gh`, `/opt/homebrew/bin/gh`, then `command -v gh`. Save as `$GH`. If none resolve, stop: `"Neither GitHub MCP nor 'gh' CLI found. Install one of: gh CLI (https://cli.github.com/) or the github plugin (/plugin install github@claude-plugins-official)."`. Verify auth: `$GH auth status` must succeed.

See `design/github-backend-primitives.md` for the full primitives + rationale.

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

**GitHub:**

Github has no introspectable workflow — the shape is declared in `.project-conf.toml`'s `[status_labels]`. No "preference logic" needed; the dispatch is hardcoded by workflow shape.

- Parse `$OWNER` / `$REPO` from `key`, `$N` from `$TICKET`.
- Read `$IN_PROGRESS_LABEL` and `$IN_REVIEW_LABEL` from `[status_labels].in_progress` and `[status_labels].in_review` via the snippet in `design/github-backend-primitives.md`. `$IN_PROGRESS_LABEL` is required (stop with `"system='github' requires [status_labels].in_progress in .project-conf.toml. Run /slopstop:gh-init or add it manually."` if missing). `$IN_REVIEW_LABEL` may be empty.
- Fetch current state:
  - MCP path: `${GH_MCP_NS}get_issue(owner=$OWNER, repo=$REPO, issueNumber=$N)` → read `state`, `labels`.
  - CLI path: `$GH issue view $N --json state,labels`.
- Record `$CURRENT_GH_STATE`: one of `OPEN-in-progress` (state OPEN AND `$IN_PROGRESS_LABEL` present), `OPEN-other` (state OPEN, label absent), `OPEN-in-review` (only relevant in 4-state, state OPEN AND `$IN_REVIEW_LABEL` present), `CLOSED`.
- Compute `$NEXT_GH_ACTION` based on workflow shape:
  - **3-state** (`$IN_REVIEW_LABEL` empty): from `OPEN-in-progress` → `{kind: "close-and-remove-label", remove: $IN_PROGRESS_LABEL}` (close issue + remove the label). From any other current state → leave `$NEXT_GH_ACTION = null` (already terminal or in a non-standard state; merge proceeds, transition step becomes a no-op).
  - **4-state** (`$IN_REVIEW_LABEL` set): from `OPEN-in-progress` → `{kind: "swap-labels", remove: $IN_PROGRESS_LABEL, add: $IN_REVIEW_LABEL}` (remove in-progress, add in-review; issue stays open). From `OPEN-in-review` or `CLOSED` → `$NEXT_GH_ACTION = null` (already past in-progress).
- Human-readable target for Step 3's confirmation prompt:
  - `{kind: "close-and-remove-label", ...}` → `"Close issue + remove '$IN_PROGRESS_LABEL' label"`.
  - `{kind: "swap-labels", ...}` → `"Remove '$IN_PROGRESS_LABEL', add '$IN_REVIEW_LABEL' (issue stays open)"`.
  - `null` → `"already past in-progress — no transition needed"`.

### Already-terminal handling

If the current state is already terminal (JIRA `statusCategory.key === "done"`, Linear `type ∈ {"completed", "canceled"}`, GitHub `state === "CLOSED"`): set `$NEXT_TRANSITION` / `$NEXT_STATE` / `$NEXT_GH_ACTION` to `null`. The merge can still proceed; the transition step becomes a clean no-op. Surface this in Step 3 as `"already terminal — no transition needed"`.

## Step 3 — Confirm with the user

Show the full plan and get explicit approval. This is the only confirmation prompt — all three remote actions happen on `yes`.

> About to merge $TICKET and ship the code:
>
> 1. **Merge** PR #$PR (`$BRANCH` → `$baseRefName`) with strategy `$STRATEGY` via `gh pr merge`. (`--delete-branch` flag included; GitHub auto-deletes the remote branch if the merge succeeds.)
> 2. **Advance** $TICKET on $SYSTEM by one state: `<current state name>` → `<computed next state name>`. (Or `"<current> — already terminal, no transition needed"` / `"<current> — no forward transition available on this workflow"` if applicable.) This is one step forward, NOT auto-Done. If the workflow's next state isn't what you expected, say `no` and handle it manually.
> 3. **Switch to `$baseRefName`, pull the merge from origin, push it to any other remotes** (mirrors / forks / upstream — if `git remote` lists anything besides `origin`), then **delete the local branch** `$BRANCH` (`gh pr view` already confirmed `state: MERGED`).
>
> Local tracking (`~/.claude/ticket-active/$TICKET/`) and the ticket description are **NOT** touched by this command. After the merge, the summary will tell you whether to run `/slopstop:archive` now (ticket landed in a terminal Done-type state) or to wait until QA/review completes (ticket landed in an intermediate state like `In Review`).
>
> <soft-warning summary if any: BLOCKED / BEHIND / failing checks / no review approval>
>
> Proceed? (yes / no / merge-only)

- `yes`: all three steps.
- `merge-only`: step 1 only — merge the PR, then stop. Do NOT touch the ticket system, do NOT push to non-origin remotes, do NOT delete the local branch, do NOT touch local tracking.
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

Step 2 already computed `$NEXT_TRANSITION` (JIRA), `$NEXT_STATE` (Linear), or `$NEXT_GH_ACTION` (GitHub) — the next forward state in the workflow, with negative completions excluded. Step 3 already showed it to the user in the confirmation prompt. Step 5 just applies it.

**Skip Step 5 entirely** if any:
- The user chose `merge-only` in Step 3 (and Step 7's recommendation falls through to branch **E**).
- `$NEXT_TRANSITION` / `$NEXT_STATE` / `$NEXT_GH_ACTION` is `null` (already-terminal current state, or no forward transition available on this workflow). Note this in the Step 7 summary as `"already terminal — no transition needed"` (branch **C**) or `"no forward transition available"` (branch **D**) respectively.

**JIRA:**
- `mcp__atlassian__transitionJiraIssue($TICKET, cloudId, $NEXT_TRANSITION.id)`.

**Linear:**
- `mcp__linear-server__save_issue` with the issue id and `stateId = $NEXT_STATE.id`.

**GitHub:**
- If `$NEXT_GH_ACTION.kind === "close-and-remove-label"` (3-state):
  - MCP path: `${GH_MCP_NS}update_issue(owner=$OWNER, repo=$REPO, issueNumber=$N, state="closed")` then `${GH_MCP_NS}remove_issue_label(owner=$OWNER, repo=$REPO, issueNumber=$N, label=$NEXT_GH_ACTION.remove)`.
  - CLI path: `$GH issue close $N && $GH issue edit $N --remove-label "$NEXT_GH_ACTION.remove"`.
- If `$NEXT_GH_ACTION.kind === "swap-labels"` (4-state):
  - MCP path: two calls — `${GH_MCP_NS}add_issue_labels(owner=$OWNER, repo=$REPO, issueNumber=$N, labels=[$NEXT_GH_ACTION.add])` then `${GH_MCP_NS}remove_issue_label(owner=$OWNER, repo=$REPO, issueNumber=$N, label=$NEXT_GH_ACTION.remove)`. (Add first to avoid a label-less intermediate state if remove succeeds but add fails.)
  - CLI path: single atomic call — `$GH issue edit $N --add-label "$NEXT_GH_ACTION.add" --remove-label "$NEXT_GH_ACTION.remove"`. Issue stays OPEN.
- For both kinds: github silently accepts add/remove of a label that's already in the target state, so retries are safe.

On any transition error: print the error and continue to Step 6. The PR is already merged; an inability to advance the ticket state isn't fatal. The user can transition manually after the fact.

> **Why advance one state and not auto-Done?** Most real workflows have intermediate states between "In Progress" and "Done" — typically a review or QA step the team uses to gate deployment. Auto-Done on PR merge skips those gates, which is wrong for most teams. Advance-one respects whatever shape the team's workflow happens to be. If your workflow has no intermediate state (just In Progress → Done), advance-one IS Done — because that's what your workflow's "next" actually is.

## Step 6 — Local branch cleanup + propagate the merge to other remotes

**Skip Step 6 entirely** if the user chose `merge-only` in Step 3. The local feature branch stays, non-origin remotes stay unpropagated, and Step 7's summary reports `Branch: untouched (merge-only)` / `Remotes: skipped (merge-only)`.

Otherwise: `gh pr merge --delete-branch` already handled the remote feature branch on origin. The local branch still exists, and any non-origin remotes (mirrors, upstream forks) still need the merged-onto branch pushed.

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

## Step 7 — Confirm and recommend next step

Print the summary, then a `Next step:` block recommending whether to run `/slopstop:archive` now or wait. The recommendation is computed from the post-transition state — terminal vs intermediate.

### Summary block

```
Shipped $TICKET.

PR:      #$PR merged ($STRATEGY, $MERGE_COMMIT) into $baseRefName
Ticket:  $TICKET advanced from '<old state>' to '<new state>' on $SYSTEM
         ( or "already terminal — no transition needed" / "no forward transition available" / "unchanged (merge-only)" )
Remotes: $baseRefName pushed to <list of non-origin remotes>
         ( or "origin only" / "skipped (merge-only)" )
Branch:  local $BRANCH deleted; remote feature branch deleted by gh pr merge
         ( or "untouched (merge-only)" )
Local:   ticket-active/$TICKET/ untouched
```

### Next-step recommendation

Compute terminal-state classification from the **post-transition** state, using the same data Step 2 already fetched (no new ticket-system call):

- **JIRA terminal:** new state's `statusCategory.key === "done"`.
- **Linear terminal:** new state's `type === "completed"`.
- **GitHub terminal:** depends on the workflow shape recorded in Step 2.
  - **3-state** (`$NEXT_GH_ACTION.kind === "close-and-remove-label"`): after Step 5 the issue is CLOSED → **terminal** → branch **A**.
  - **4-state** (`$NEXT_GH_ACTION.kind === "swap-labels"`): after Step 5 the issue is OPEN with `$IN_REVIEW_LABEL` → **NOT terminal** → branch **B**.

Then print exactly ONE of these blocks based on what happened:

**A — Advanced into a terminal state** (Step 5 transitioned, new state is terminal):

```
Next step:
  ✅ Ticket is now in '<new state>' — a terminal/Done state on this workflow.
     Run /slopstop:archive to push the final task plan as the description,
     post a DoD-confirmation comment + findings comment, and move
     ticket-active/$TICKET/ to ticket-archive/.
```

**B — Advanced into an intermediate state** (Step 5 transitioned, new state is NOT terminal):

```
Next step:
  ⚠️ Ticket is now in '<new state>', which is NOT a terminal/Done state on this
     workflow. Do NOT run /slopstop:archive yet — the task plan would land
     on the ticket while QA/review is still in progress, and the local tracking
     dir would move out of ticket-active/ prematurely. Wait until the ticket
     reaches a Done-type state (typically after QA sign-off), then run
     /slopstop:archive.
```

**C — Already terminal before the merge** ($NEXT_TRANSITION / $NEXT_STATE / $NEXT_GH_ACTION was `null` because current state was already terminal):

```
Next step:
  ✅ Ticket was already in '<state>' (terminal) before the merge.
     Run /slopstop:archive to push the final task plan + DoD-confirmation
     comment + findings comment and move tracking to ticket-archive/.
```

**D — No forward transition available** ($NEXT_TRANSITION / $NEXT_STATE / $NEXT_GH_ACTION was `null` because no forward state existed on the workflow):

```
Next step:
  ⏸ No forward transition was available on this workflow — the ticket remains
     in '<state>'. Run /slopstop:archive only when the ticket actually
     reaches a terminal Done-type state (transition manually first).
```

**E — Merge-only path** (user chose `merge-only` in Step 3):

```
Next step:
  ⏸ Ticket state was NOT advanced (merge-only path). Run /slopstop:archive
     only when the ticket reaches a terminal state (transition manually first).
```

`progress.md` is intentionally NOT written to — the user can capture mid-flight notes via `/slopstop:update` if they want.

## Rules

- Confirms ONCE in Step 3 before any destructive remote action. After that, run to completion or fail loudly.
- **The ticket transition advances by ONE state in the workflow, not auto-Done.** Same-bucket transitions are preferred (e.g., "In Progress" → "In Review" over "In Progress" → "Done") so the team's review / QA gates aren't skipped. If the workflow has no intermediate state and the only forward option is Done, then Done is what happens — but that's because Done IS the next state, not because the skill assumed it. The proposed target is shown in Step 3's confirmation prompt; the user can say `no` if it isn't right.
- **Does NOT touch local tracking or push the task plan to the ticket.** `~/.claude/ticket-active/$TICKET/` stays in place, and `task_plan.md` / `findings.md` are not pushed. `/slopstop:archive` does all of that — and the user invokes it separately, once the ticket has actually reached a terminal state on the workflow (typically after QA). This separation exists because `:merge`'s "advance one state" frequently lands the ticket in an intermediate state like "In Review" that QA still needs to act on; pushing the task plan to the description and moving the local tracking dir out at that point would both be premature.
- **Step 7 always tells the user whether to run `/slopstop:archive` now or wait.** Terminal-state classification of the post-transition state: JIRA `statusCategory.key === "done"`, Linear `state.type === "completed"`. Terminal → ✅ recommend `:archive` now. Non-terminal → ⚠️ warn to wait until QA sign-off. No forward transition possible, or merge-only path → ⏸ neutral note ("when ready, transition manually first").
- All-or-nothing on the PR merge (Step 4). If it fails, no other state changes.
- The ticket transition (Step 5) is best-effort after the merge — surface failures but don't roll back. The PR is already merged; we can't un-ship.
- Branch deletion (Step 6) is the last destructive local action. Uses `gh pr view`'s authoritative `state: MERGED` rather than `git`'s commit-equivalence check, so squash and rebase merges work.
- Never run `git push --force`, `git reset --hard`, or skip pre-commit hooks. None of those are part of this flow.
- Never enable `--admin` on `gh pr merge` to bypass branch protection. If the merge is BLOCKED, surface the reason and ask the user to handle it.
- Failure handling per step:
  - **Pre-flight fails**: print reason and stop. No state changed.
  - **Step 1 (PR resolution) fails**: print reason and stop. No state changed.
  - **Step 4 (merge) fails**: print error, stop. No state changed.
  - **Step 5 (transition) fails**: print error, continue to Step 6. PR is merged. Step 7's recommendation falls through to branch **D** (no forward transition) since we don't know the new state.
  - **Step 6 (branch cleanup) fails** (e.g. uncommitted changes appeared): leave local branch in place, continue to Step 7 and report at the end.
