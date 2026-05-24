---
description: Open a pull request for the active ticket's branch with pre-commit simplify + tests + CodeRabbit polling. Use /ticket-plugin:pr to (1) run Claude Code's simplify skill on uncommitted changes, (2) run the project's tests and refuse to commit on failures, (3) commit with a ticket-anchored message, (4) push and open a PR via GitHub MCP or gh CLI, (5) trigger CodeRabbit when the PR's base isn't the repo default, (6) poll for CodeRabbit feedback up to 15 minutes, and (7) categorize the suggestions for action. Stops after presenting — never auto-applies CodeRabbit's proposals.
disable-model-invocation: true
---

# /ticket-plugin:pr

Open a pull request for the active ticket's branch with a pre-commit review pass and CodeRabbit feedback polling.

Confirms before each significant remote action. Stops after presenting CodeRabbit's review — the user decides which suggestions to apply.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. Never read, write, or clear `CURRENT-*` files for any other prefix.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /ticket-plugin:gh-init (for GitHub) or create the file manually with system + key."`

## Arguments

Optional `--base <branch>` to override the PR target branch (default: the repo's default branch — usually `master` or `main`).
Optional `--no-simplify` to skip Step 1's simplify pass.
Optional `--no-test` to skip Step 2's pre-commit test run.
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

## Step 2 — Run relevant tests before committing

Skip if `--no-test` was passed.

The PR shouldn't commit code that breaks tests. This step runs the project's test suite — at minimum the Phase 0 red tests written by `/ticket-plugin:plan` should be GREEN by now, since the work done since then was supposed to turn them green.

### 2a. Identify the test command

In order, use the first hit:

1. **`**Test command:**` line in `task_plan.md`** — written by `/ticket-plugin:plan` Phase 0, or by a previous `/ticket-plugin:pr` invocation that asked.
2. **Auto-detect** from project files in cwd:
   | Indicator | Command |
   |---|---|
   | `Taskfile.yml` with a `test:` task | `task test` |
   | `Makefile` with a `test:` target | `make test` |
   | `package.json` with `"test"` script + `pnpm-lock.yaml` | `pnpm test` |
   | `package.json` with `"test"` script + `yarn.lock` | `yarn test` |
   | `package.json` with `"test"` script (else) | `npm test` |
   | `Cargo.toml` | `cargo test` |
   | `go.mod` | `go test ./...` |
   | `pyproject.toml` with pytest config | `pytest` |
3. **Ask the user** once: `"What's the test command for this project? (paste it, or 'skip' to skip pre-commit tests)"`. On a real answer, **cache it** by writing `**Test command:** <cmd>` into `task_plan.md` (top of file, before `## Original description`). On `skip`: warn and continue to Step 3 without testing.

### 2b. Run the tests

Execute the test command. Capture output. Treat exit code 0 as success, anything else as failure.

### 2c. Handle results

- **Pass** (exit 0): print `"Tests passed. Continuing to commit."` and proceed to Step 3.

- **Fail** (non-zero exit): print the test output focused on failures, then ask:
  ```
  Tests failed. Refusing to commit by default.

    - "fix":        stop here. You fix the failing tests and re-run /ticket-plugin:pr.
    - "commit anyway":  proceed with the commit despite failing tests (you'll explain in the commit body why).
    - "abort":      stop entirely.
  ```
  On `fix` or `abort`: stop.
  On `commit anyway`: continue to Step 3, but add a line to the commit body: `Note: <N> test(s) failing at commit time — see body for rationale.` The user supplies the rationale before the commit lands.

## Step 3 — Commit (with a ticket-anchored message)

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

## Step 4 — Find the GitHub backend, then push

### 4a. Locate the GitHub backend

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

### 4b. Push the branch

Decide based on upstream state:

- No upstream (`git rev-parse --abbrev-ref @{upstream}` fails): `git push -u origin $BRANCH`.
- Branch ahead of upstream (`git rev-list --count @{upstream}..HEAD` returns >0): `git push origin $BRANCH`.
- Branch in sync with upstream: skip push (nothing to send).

On push failure (non-fast-forward, network, auth): stop with the git output verbatim. Never `git push --force`. The user resolves the divergence manually (rebase, etc.) and re-runs.

## Step 5 — Create the PR

### 5a. Build title and body

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

### 5b. Create the PR

**If `$BACKEND == MCP`:** call the create-pull-request tool (exact name discovered in Step 4a, e.g. `mcp__github__create_pull_request`) with `title`, `body`, `head: $BRANCH`, `base: $BASE`.

**If `$BACKEND == CLI`:** use HEREDOC to preserve markdown formatting in the body:
```
$GH pr create --title "[$TICKET] <summary>" --body "$(cat <<'EOF'
<body content here>
EOF
)" --base "$BASE" --head "$BRANCH"
```

Capture the resulting PR number `$PR` and URL `$PR_URL`. Print: `"PR created: $PR_URL (target: $BASE)"`.

### 5c. Trigger CodeRabbit (if base is not the default branch)

If `$BASE != $DEFAULT_BRANCH` — stacked PR or non-trunk target — CodeRabbit may not auto-review. Post `@coderabbitai review` to trigger it:

- **MCP:** call the add-pull-request-comment tool with body `"@coderabbitai review"`.
- **CLI:** `$GH pr comment $PR --body "@coderabbitai review"`.

If `$BASE == $DEFAULT_BRANCH`: skip — CodeRabbit auto-runs on default-branch PRs.

On comment-add failure: warn (`"Couldn't post the CodeRabbit trigger comment: <error>. Add it manually if needed."`) but continue — the PR exists either way.

## Step 6 — Poll for CodeRabbit feedback

Skip if `--no-poll` was passed.

We're polling for **completion** — CodeRabbit having finished its review of this PR. That can take any of three observable forms:

- **Inline review comments** at `repos/$OWNER/$REPO/pulls/$PR/comments` — line-level suggestions. Non-zero count means CodeRabbit flagged at least one item.
- **Finalized review summaries** at `repos/$OWNER/$REPO/pulls/$PR/reviews` with `state ∈ {CHANGES_REQUESTED, APPROVED, COMMENTED}`. (Empirically, current CodeRabbit posts most or all reviews as `COMMENTED` — the state field isn't a reliable signal of intent, just an artifact of how CodeRabbit submits. Treat any review authored by `coderabbitai[bot]` as a completion signal, regardless of state. The earlier exclusion of `COMMENTED` caused PRs to time out at 15 min when CodeRabbit had actually finished — see `<!-- walkthrough_start -->` marker logic below as the cross-check.)
- **A completed walkthrough comment** at `repos/$OWNER/$REPO/issues/$PR/comments` from `coderabbitai[bot]` whose body contains a CodeRabbit completion marker. The reliable markers, validated against current CodeRabbit output, are the HTML comment `<!-- walkthrough_start -->` and the section heading `## Walkthrough` — CodeRabbit inserts these only when the walkthrough body has been populated, not when the comment is still in its early "I'm reviewing this..." placeholder form. The legacy markers `"Summary by CodeRabbit"`, `"No actionable comments"`, and `"Actionable comments posted:"` are kept as fallbacks for older CodeRabbit versions, but the first two markers above match 100% of current walkthroughs. This is the **zero-findings path** — CodeRabbit ran, may have had nothing to flag (no Review object, no inline comments), but the walkthrough comment is the signal that it finished.

Prefer `gh api` for polling regardless of `$BACKEND` (it's simpler and read-only). If gh isn't installed and you're MCP-only, use the MCP list-comments tool.

```bash
OWNER=$($GH repo view --json owner --jq .owner.login)
REPO=$($GH repo view --json name --jq .name)
for i in $(seq 1 15); do
  inline_count=$($GH api "repos/$OWNER/$REPO/pulls/$PR/comments" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]")] | length')
  review_count=$($GH api "repos/$OWNER/$REPO/pulls/$PR/reviews" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]" and (.state=="CHANGES_REQUESTED" or .state=="APPROVED" or .state=="COMMENTED"))] | length')
  # Zero-findings detection: CodeRabbit updates the walkthrough issue-comment with
  # a completion marker rather than posting a Review. Without this check the loop
  # would time out at 15 min on every clean PR.
  walk_done=$($GH api "repos/$OWNER/$REPO/issues/$PR/comments" \
    --jq '[.[] | select(.user.login=="coderabbitai[bot]" and
      (.body | test("<!-- walkthrough_start -->|## Walkthrough|Summary by CodeRabbit|No actionable comments|Actionable comments posted")))] | length')
  if [ "$inline_count" -gt 0 ] || [ "$review_count" -gt 0 ] || [ "$walk_done" -gt 0 ]; then
    if [ "$inline_count" -gt 0 ] || [ "$review_count" -gt 0 ]; then
      echo "CodeRabbit feedback received: $inline_count inline comments, $review_count finalized reviews"
    else
      echo "CodeRabbit review complete — no actionable comments"
    fi
    break
  fi
  echo "Waiting for CodeRabbit ($i/15)..."
  sleep 60
done
```

**Timeout (15 iterations, no observable completion signal):** CodeRabbit hasn't posted inline comments, a finalized review, or a walkthrough with a completion marker after 15 minutes. Likely causes: CodeRabbit isn't installed on the repo, the webhook is stuck, the service is down, or the PR's base isn't covered by CodeRabbit's config and the `@coderabbitai review` mention in Step 5c didn't take. Print `"CodeRabbit didn't post a completion signal in 15 minutes. Check the PR page directly: $PR_URL. You can re-run /ticket-plugin:pr later (with --no-simplify, since the commit is already made) to re-poll."` and skip to Step 7.

## Step 7 — Verify, classify, and present CodeRabbit's proposals

### 7-pre. Zero-findings fast path

If Step 6 broke on `walk_done` alone (i.e. `inline_count == 0` AND `review_count == 0`), skip the verification + decision tree (there's nothing to classify) and go straight to the **clean-verdict presentation** at 7d-clean below. Fetch only the walkthrough comment for the optional excerpt; skip the inline + review fetches.

### 7-full. Full-findings path

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

### 7a. Read the actual code

Before judging, open the file CodeRabbit is commenting on (use `path` and `line` from the comment, plus 20–30 lines of surrounding context). For "X is unused" or codebase-pattern claims, also grep the broader repo for the symbol or pattern. The classification must be grounded in what the code actually does, not what CodeRabbit asserts it does.

### 7b. Verify CodeRabbit's premise

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

### 7c. Classify by decision tree

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

### 7d. Present

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

### 7d-clean. Clean-verdict presentation (zero-findings fast path)

When 7-pre kicked in, the output is short — there's nothing to verify or classify:

```
CodeRabbit review of PR #$PR — clean ✅

CodeRabbit found no actionable comments to address.

<optional: paste the "Summary by CodeRabbit" section of the walkthrough comment verbatim, indented 2 spaces, if the user might want context on what CodeRabbit looked at. Omit if the walkthrough is just generic acknowledgement text — no need to pad the output.>

PR: $PR_URL
```

Continue to Step 8.

**Stop after presenting.** This skill never auto-applies CodeRabbit suggestions. The user decides what to do next — apply fixes manually with their normal edit/commit flow, or re-run `/ticket-plugin:pr` after applying changes to get a fresh CodeRabbit pass.

## Step 8 — Confirm

```
PR opened for $TICKET.

PR:         #$PR ($BRANCH → $BASE) — $PR_URL
Commit:     <sha> [$TICKET] <subject>
Simplify:   <"clean — no changes needed" | "applied N changes (user confirmed)" | "skipped (--no-simplify)" | "skipped (no uncommitted changes)" | "user aborted">
Tests:      <"passed — N tests" | "skipped (--no-test)" | "skipped (user said skip)" | "failed but user said commit-anyway">
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
  - **Step 2 (tests) command unknown** (user said `skip`): warn and continue without testing.
  - **Step 2 (tests) fail**: refuse commit by default; offer `fix` / `commit anyway` / `abort`.
  - **Step 3 (commit) fails** (pre-commit hook): print hook output, stop. User fixes and re-runs.
  - **Step 4a (no backend found)**: stop with install instructions.
  - **Step 4b (push) fails** (non-fast-forward, etc.): stop. User resolves manually; this skill never `--force`s.
  - **Step 5b (PR creation) fails**: print error, stop. The branch is already pushed; user can retry or open the PR via the GitHub UI.
  - **Step 5c (CodeRabbit alert comment) fails**: warn but continue. PR exists.
  - **Step 6 (poll timeout)**: not a failure — print and continue to Step 8 without Step 7 analysis.
  - **Step 7 (analysis)**: zero-findings case (Step 6 broke on `walk_done` only) takes the 7-pre / 7d-clean fast path — clean ✅ verdict, no verification or classification work. Non-zero takes the 7-full path with verify → classify → present. Step 6 timeout also enters Step 7 but with empty fetch results; the 7d-clean output still renders (printing `"CodeRabbit didn't post a completion signal in 15 minutes"` instead of the clean-verdict body).
