---
description: Open a pull request for the active ticket's branch with a pre-commit simplify pass and CodeRabbit review polling. Use /ticket-plugin:pr to (1) run Claude Code's simplify skill on uncommitted changes, (2) commit with a ticket-anchored message, (3) push and open a PR via GitHub MCP or gh CLI, (4) trigger CodeRabbit when the PR's base isn't the repo default, (5) poll for CodeRabbit feedback up to 15 minutes, and (6) categorize the suggestions for action. Stops after presenting — never auto-applies CodeRabbit's proposals.
disable-model-invocation: true
---

# /ticket-plugin:pr

Open a pull request for the active ticket's branch with a pre-commit review pass and CodeRabbit feedback polling.

Confirms before each significant remote action. Stops after presenting CodeRabbit's review — the user decides which suggestions to apply.

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or clear `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments

Optional `--base <branch>` to override the PR target branch (default: the repo's default branch — usually `master` or `main`).
Optional `--no-simplify` to skip Step 1's simplify pass.
Optional `--no-poll` to open the PR and stop without waiting for CodeRabbit.

The active ticket is whatever `~/.claude/ticket-active/CURRENT-$PREFIX` contains. If empty: `"No active $PREFIX ticket to PR."` and stop.

## Pre-flight (run in parallel)

- `$TICKET` = contents of `~/.claude/ticket-active/CURRENT-$PREFIX`. If empty: stop.
- Verify `~/.claude/ticket-active/$TICKET/` exists. If not: state corruption — stop without writing anything.
- `$BRANCH` = `git branch --show-current`. If on the main/master branch: refuse with `"Refusing: on the main branch, not a feature branch."`
- `$DIRTY` = `git status --porcelain` (used in Step 1 and Step 2).
- `$DEFAULT_BRANCH` = `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name` (cache for Step 4c).
- `$BASE` = `--base` argument if given, else `$DEFAULT_BRANCH`.

If an open PR already exists for `$BRANCH` (`gh pr list --head $BRANCH --state open` returns ≥1), refuse: `"PR already exists for $BRANCH: <url>. Use /ticket-plugin:merge to ship it, or push more commits to update."`

## Step 1 — Simplify pass on uncommitted changes

Skip if `--no-simplify` was passed, OR if `$DIRTY` is empty (nothing to simplify).

The goal: catch reuse/quality/efficiency issues before they land in a commit, since simplify works best on uncommitted work. Approach:

1. Snapshot the current diff: `git diff > /tmp/pr-before-simplify.diff && git diff --staged >> /tmp/pr-before-simplify.diff`.
2. Invoke the `simplify` skill via the Skill tool:
   ```
   Skill(skill: "simplify")
   ```
3. If the Skill tool reports `simplify` is unavailable in this session: print `"simplify skill not available — install Claude Code's bundled skills, or proceed without it."` and ask `"Continue without simplify? (yes / no)"`. On `no`: stop.
4. After simplify completes, capture the post-state diff the same way: `git diff > /tmp/pr-after-simplify.diff && git diff --staged >> /tmp/pr-after-simplify.diff`.
5. Compare the two diffs:
   - **Identical** — simplify found nothing to fix. Continue silently to Step 2.
   - **Different** — simplify modified the working tree. Show the user the delta (`diff /tmp/pr-before-simplify.diff /tmp/pr-after-simplify.diff`, or just `git diff` against the snapshot reference) and ask:
     > simplify made the changes above. Continue with these incorporated, or abort to review/revert manually? (continue / abort)
     - On `continue`: proceed to Step 2.
     - On `abort`: stop. Remote state unchanged. The simplify changes remain in the working tree for the user to inspect/revert manually with `git checkout -p` or `git stash`.

## Step 2 — Commit (with a ticket-anchored message)

Skip if `$DIRTY` is empty after Step 1 (nothing to commit; just push and PR).

Stage everything: `git add -A`. The contract is "all current changes get committed as one PR's first commit". If the user has staged-vs-unstaged distinctions they want to preserve, they'd commit manually before invoking this skill.

Generate the commit message:

- **Subject** (≤ 72 chars): `[$TICKET] <imperative summary>`. Derive the summary from:
  - The ticket title (first heading line of `task_plan.md`, stripped of the `# $TICKET — ` prefix).
  - The actual change set (`git diff --staged --stat` for file scope).
- **Body** (blank line, then 1–3 short paragraphs): explain WHY. Pull from `task_plan.md`'s Plan section if it has relevant context; otherwise summarize the diff. Cite specific files where useful.
- **Trailer** (blank line, then): `Refs: $TICKET`. (Use `Refs:` not `Closes:` — `/ticket-plugin:merge` is what actually closes the ticket; `Refs:` is the right linkage during the in-flight phase.)

Commit:
```
git commit -m "<subject>" -m "<body>" -m "Refs: $TICKET" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Or HEREDOC for a cleaner multi-paragraph body.

If pre-commit hooks fail: print the hook output verbatim and stop. Do NOT pass `--no-verify`. The user fixes the hook violation and re-runs this skill.

## Step 3 — Find the GitHub backend, then push

### 3a. Locate the GitHub backend

Run two ToolSearches in parallel:

```
ToolSearch(query="select:mcp__github__create_pull_request,mcp__github__add_pull_request_comment,mcp__github__get_pull_request,mcp__github__list_pull_request_review_comments", max_results=8)
ToolSearch(query="github create pull request comment", max_results=5)
```

Set `$BACKEND`:
- Any `mcp__github__*` (or similar github-namespace) tools exposed → `MCP`
- Else → `CLI`

For the **CLI** path, find the `gh` binary. Try each in order; use the first one where `<path> --version` succeeds:

1. `/usr/local/bin/gh`
2. `$HOME/.local/bin/gh`
3. `/opt/homebrew/bin/gh`
4. `command -v gh` (i.e. whatever `$PATH` resolves)

Save as `$GH`. If none resolve, stop:
```
Neither GitHub MCP nor `gh` CLI found. Install one of:
- gh CLI: https://cli.github.com/
- GitHub plugin: /plugin install github@claude-plugins-official
```

For the **MCP** path, also try to resolve `$GH` (gh CLI) — `gh api` is the cleanest way to poll for CodeRabbit feedback in Step 5, even when the high-level PR operations go through MCP. If gh isn't installed, fall back to MCP's list-comments tool for the poll.

Verify auth on whichever backend you'll use:
- **CLI:** `$GH auth status` succeeds.
- **MCP:** the MCP tools typically auth themselves; trust them unless a call fails.

### 3b. Push the branch

Decide based on upstream state:

- No upstream (`git rev-parse --abbrev-ref @{upstream}` fails): `git push -u origin $BRANCH`.
- Branch ahead of upstream (`git rev-list --count @{upstream}..HEAD` returns >0): `git push origin $BRANCH`.
- Branch in sync with upstream: skip push (nothing to send).

On push failure (non-fast-forward, network, auth): stop with the git output verbatim. Never `git push --force`. The user resolves the divergence manually (rebase, etc.) and re-runs.

## Step 4 — Create the PR

### 4a. Build title and body

- **Title**: same as the most recent commit's subject — `[$TICKET] <summary>`. (If this skill made the commit in Step 2, use that subject; if Step 2 was skipped, use `git log -1 --format=%s`.)

- **Body**:
  ```
  ## Summary
  <1–3 bullets pulled from task_plan.md's Plan section, or summarized from the commit body>

  ## Ticket
  $TICKET — <ticket URL from the **Ticket URL:** line of task_plan.md>

  ## Test plan
  <bulleted checklist — pull from task_plan.md if it has a relevant section, otherwise generate from the diff: list changed files and what should be exercised>
  ```

### 4b. Create the PR

**If `$BACKEND == MCP`:** call the create-pull-request tool (exact name discovered in Step 3a, e.g. `mcp__github__create_pull_request`) with `title`, `body`, `head: $BRANCH`, `base: $BASE`.

**If `$BACKEND == CLI`:** use HEREDOC to preserve markdown formatting in the body:
```
$GH pr create --title "[$TICKET] <summary>" --body "$(cat <<'EOF'
<body content here>
EOF
)" --base "$BASE" --head "$BRANCH"
```

Capture the resulting PR number `$PR` and URL `$PR_URL`. Print: `"PR created: $PR_URL (target: $BASE)"`.

### 4c. Trigger CodeRabbit (if base is not the default branch)

If `$BASE != $DEFAULT_BRANCH` — stacked PR or non-trunk target — CodeRabbit may not auto-review. Post `@coderabbitai review` to trigger it:

- **MCP:** call the add-pull-request-comment tool with body `"@coderabbitai review"`.
- **CLI:** `$GH pr comment $PR --body "@coderabbitai review"`.

If `$BASE == $DEFAULT_BRANCH`: skip — CodeRabbit auto-runs on default-branch PRs.

On comment-add failure: warn (`"Couldn't post the CodeRabbit trigger comment: <error>. Add it manually if needed."`) but continue — the PR exists either way.

## Step 5 — Poll for CodeRabbit feedback

Skip if `--no-poll` was passed.

We're polling for **substantive** feedback — inline review comments or a finalized review summary. Do NOT exit early on CodeRabbit's first "walkthrough" or "I'm working on it" comment — those are acknowledgements, not the actual review. The substantive content appears as:

- **Inline review comments** at `repos/$OWNER/$REPO/pulls/$PR/comments` — these are the line-level suggestions.
- **Finalized review summaries** at `repos/$OWNER/$REPO/pulls/$PR/reviews` with `state ∈ {CHANGES_REQUESTED, APPROVED}`. A `state == COMMENTED` review can be just an ack — don't count those.

Prefer `gh api` for polling regardless of `$BACKEND` (it's simpler and read-only). If gh isn't installed and you're MCP-only, use the MCP list-comments tool.

```bash
OWNER=$($GH repo view --json owner --jq .owner.login)
REPO=$($GH repo view --json name --jq .name)
for i in $(seq 1 15); do
  inline_count=$($GH api "repos/$OWNER/$REPO/pulls/$PR/comments" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]")] | length')
  review_count=$($GH api "repos/$OWNER/$REPO/pulls/$PR/reviews" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]" and (.state=="CHANGES_REQUESTED" or .state=="APPROVED"))] | length')
  if [ "$inline_count" -gt 0 ] || [ "$review_count" -gt 0 ]; then
    echo "CodeRabbit feedback received: $inline_count inline comments, $review_count finalized reviews"
    break
  fi
  echo "Waiting for CodeRabbit ($i/15)..."
  sleep 60
done
```

**Timeout (15 iterations, no substantive feedback):** print `"CodeRabbit didn't post substantive feedback in 15 minutes. Check the PR page directly: $PR_URL. You can re-run /ticket-plugin:pr later (with --no-simplify, since the commit is already made) to re-poll."` and skip to Step 7.

## Step 6 — Verify, classify, and present CodeRabbit's proposals

Fetch the full set of CodeRabbit comments:

```bash
# Inline review comments (the substantive line-level suggestions)
$GH api "repos/$OWNER/$REPO/pulls/$PR/comments" \
  --jq '[.[] | select(.user.login=="coderabbitai[bot]") | {path, line, body, diff_hunk}]'

# Review summaries (state, body, timestamp)
$GH api "repos/$OWNER/$REPO/pulls/$PR/reviews" \
  --jq '[.[] | select(.user.login=="coderabbitai[bot]") | {state, body, submitted_at}]'

# Top-level walkthrough / first-impression comments
$GH api "repos/$OWNER/$REPO/issues/$PR/comments" \
  --jq '[.[] | select(.user.login=="coderabbitai[bot]") | {body, created_at}]'
```

For each **inline** comment, apply this process in order. Do NOT skip to classification on CodeRabbit's claim alone — CodeRabbit hallucinates, and a wrong-premise bucket is the most common categorization error.

### 6a. Read the actual code

Before judging, open the file CodeRabbit is commenting on (use `path` and `line` from the comment, plus 20–30 lines of surrounding context). For "X is unused" or codebase-pattern claims, also grep the broader repo for the symbol or pattern. The classification must be grounded in what the code actually does, not what CodeRabbit asserts it does.

### 6b. Verify CodeRabbit's premise

Common failure modes — check whichever applies:

| CodeRabbit claim | How to verify |
|---|---|
| "X is unused / dead code" | `grep -r "<symbol>"` across the repo (and across reverse deps if it's an exported API). Could be called via reflection, plugin registry, dynamic dispatch. |
| "X can be null / undefined" | Check the type signature / contract. Is the input actually nullable, or is non-null guaranteed upstream? |
| "Missing await" | Is the called function actually async? Read its signature. |
| "Use idiom Y instead of Z" | Grep neighboring files. Does the codebase use Y or Z? The codebase's existing convention wins over generic best practice. |
| "X is a security risk" | Is the input actually attacker-controlled at this call site? An internal-only function with internal-only inputs isn't a security risk regardless of how the operation looks. |
| "Race condition" | Is concurrent access actually possible here, or is the call site single-threaded by construction? |

If CodeRabbit's premise turns out to be **false**, the verdict is **⚪ Skip — "premise wrong: <specifics>"** and you stop processing this comment. Do not classify it as "Should" or "Could" just because the suggestion *would* be a fix if the premise were true.

### 6c. Classify by decision tree

If the premise checks out, apply these questions in order. The first one that matches wins:

1. **Does the suggestion fix a bug, security issue, data loss, or runtime crash?**
   Concrete failure mode (off-by-one in a slice that returns wrong data, SQL injection, silently-swallowed error that should propagate, missing null check that crashes on real input).
   → **🔴 Should fix**

2. **Does the suggestion contradict an established pattern in the codebase?**
   Check neighboring files. If the codebase consistently uses approach X and CodeRabbit suggests Y, codebase wins. (Consistency has more compounding value than any single generic best practice.)
   → **⚪ Skip — "contradicts convention: <file you checked>"**

3. **Is it a clear improvement with positive ROI?**
   Simpler code, fewer edge cases, removes a dependency, better error message, a test for a real edge case (not a speculative one).
   → **🟡 Could fix**

4. **Is it a pure stylistic nit with no functional benefit?**
   "Consider renaming foo to fooValue", "extract this 3-line block to a helper", "use template literal instead of string concat" (when both are equivalent in context).
   → **⚪ Skip — "stylistic nit, no functional benefit"**

5. **Otherwise** (legitimate refactor that's not strictly better, speculative test coverage, documentation that's nice-to-have):
   → **🟡 Could fix** (default to optional)

### 6d. Present

Quote CodeRabbit's actual words for each item so the user can sanity-check the classification against the source comment:

```
CodeRabbit review of PR #$PR — $N inline comments, $M finalized reviews

🔴 Should fix ($N1):

  📄 <file>:<line>
     CodeRabbit: "<first ~120 chars of the comment body, with a trailing … if truncated>"
     Verdict:    <one-line summary of the recommended fix>
     Why:        <reasoning, including any verification you did (e.g. "confirmed the symbol is only used here")>

  📄 <file>:<line>
     ...

🟡 Could fix ($N2):

  📄 <file>:<line>
     CodeRabbit: "..."
     Verdict:    ...
     Why:        ...

⚪ Skip ($N3):

  📄 <file>:<line>
     CodeRabbit: "..."
     Verdict:    Skip
     Why:        <"premise wrong: ..." | "contradicts convention: ..." | "stylistic nit, no functional benefit">

Walkthrough summary:
<excerpt of the walkthrough comment if it adds useful context beyond the inline comments — otherwise omit this section>

PR: $PR_URL
```

**Stop after presenting.** This skill never auto-applies CodeRabbit suggestions. The user decides what to do next — apply fixes manually with their normal edit/commit flow, or re-run `/ticket-plugin:pr` after applying changes to get a fresh CodeRabbit pass.

## Step 7 — Confirm

```
PR opened for $TICKET.

PR:         #$PR ($BRANCH → $BASE) — $PR_URL
Commit:     <sha> [$TICKET] <subject>
Simplify:   <"clean — no changes needed" | "applied N changes (user confirmed)" | "skipped (--no-simplify)" | "skipped (no uncommitted changes)" | "user aborted">
Backend:    <"MCP" | "CLI ($GH)">
CodeRabbit: <"reviewed — $N comments categorized above" | "timed out after 15 min" | "skipped (--no-poll)">
```

## Rules

- **One confirmation per destructive remote action.** Step 1 may ask for confirmation if simplify made changes. Step 2 may pause if pre-commit hooks fail. Step 4 doesn't ask separately — pushing and creating the PR is the implicit confirmation that came from invoking this skill.
- **Never** `git push --force`, `git reset --hard`, `git commit --no-verify`, or `gh pr merge --admin`. None of those have a place in this flow.
- **Never auto-apply CodeRabbit suggestions in Step 6.** Present only. The user explicitly opts in.
- **All commits made by this skill are anchored to the active ticket** via `Refs: $TICKET` in the trailer. If the active ticket doesn't match the work being committed, the user should switch tickets first (`/ticket-plugin:pause` → `/ticket-plugin:start <OTHER>`) before invoking this skill.
- **Simplify is a soft prerequisite.** If unavailable, the skill warns and asks the user to confirm continuing — not a hard stop.
- **CodeRabbit is a soft prerequisite.** If the PR is created but CodeRabbit never responds within 15 minutes, that's not a failure — the skill prints a notice and stops without analysis. The PR is fine on its own.
- **Failure handling per step:**
  - **Pre-flight fails** (no active ticket, on main branch, existing open PR): stop. No state changed.
  - **Step 1 (simplify) unavailable**: warn, ask user to continue or abort.
  - **Step 1 (simplify) made changes**: ask user to confirm or abort.
  - **Step 2 (commit) fails** (pre-commit hook): print hook output, stop. User fixes and re-runs.
  - **Step 3a (no backend found)**: stop with install instructions.
  - **Step 3b (push) fails** (non-fast-forward, etc.): stop. User resolves manually; this skill never `--force`s.
  - **Step 4b (PR creation) fails**: print error, stop. The branch is already pushed; user can retry or open the PR via the GitHub UI.
  - **Step 4c (CodeRabbit alert comment) fails**: warn but continue. PR exists.
  - **Step 5 (poll timeout)**: not a failure — print and continue to Step 7 without Step 6 analysis.
  - **Step 6 (analysis)**: if no inline comments after Step 5 succeeded (only the walkthrough), present the walkthrough alone and proceed to Step 7.
