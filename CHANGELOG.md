# Changelog

All notable changes to this plugin will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-05-19

### Changed

- **`/ticket-plugin:merge` no longer auto-transitions tickets to Done.** The skill now advances the ticket by **one state** in the workflow, respecting intermediate states like "In Review" or "Awaiting QA" that many teams put between In Progress and Done. The previous behavior (jump straight to a Done-category state) skipped those gates, which is wrong for most real teams. This is a behavior change to the default flow — hence the minor version bump rather than a patch.
- The Step 3 confirmation prompt now shows the **specific computed next state** (e.g. `"In Progress → In Review"`) rather than the vague `"to a terminal Done state"`. If the proposed target isn't what the user expected, they can say `no` and handle the transition manually.
- Computation logic in Step 2 (was: in Step 5):
  - **JIRA:** `getTransitionsForJiraIssue` → exclude negative-completion names (`won't do`, `cancel`, `reject`, `abandon`, `invalid`, `duplicate`) → prefer transitions that stay in the **current** `statusCategory.key` (sideways "In Progress" → "In Review" preferred over the category jump to "Done") → within those, prefer name match `/review|qa|verify|test|pending|ready|merged|shipped/i`. Only falls back to a category-advancing transition (and then preferred-Done picking) when no same-category target exists.
  - **Linear:** `list_issue_statuses` → filter out `type === "canceled"` and negative names → prefer states with the **same** `type` and a higher `position` (the immediate next slot in the same bucket) → if none, advance type to `completed` with the lowest position. Same name preferences as JIRA.
- The semantics also clean up a related asymmetry: `/ticket-plugin:archive` continues to refuse non-terminal tickets, but `/ticket-plugin:merge` (which inlines parts of `:archive`'s push + local-mv logic, NOT the terminal gate) may legitimately leave the ticket in a non-terminal state on the ticket system while still archiving the local tracking dir. The local archive captures "dev's work is done"; the ticket's final state is whatever the team's workflow + QA process produces.

### Also changed

- Moved the repo-maintainer release checklist from `CLAUDE.md` at the root to `.claude/rules/repo-conventions.md`. The plugin validator warns about `CLAUDE.md` at a plugin root (it assumes the file is trying to ship context to plugin users, which doesn't work). Our use case is the opposite — repo conventions for maintainers — and `.claude/rules/` is the right home for that. Claude Code auto-loads both `CLAUDE.md` and `.claude/rules/*.md` at session start, so the behavior is identical; only the file location changed.

### Notes

- If your team's workflow happens to have no intermediate state between In Progress and Done, advance-one IS Done — because that's what your workflow's "next" actually is. The skill doesn't enforce intermediate states; it just doesn't assume them.
- README updated: the workflow diagram, the `:merge` command description, the `:merge` vs `:archive` distinction, and the fictional scenario walkthrough all reflect the new advance-one semantics.

## [1.1.2] — 2026-05-19

### Fixed

- `marketplace.json`'s `plugins[0].source` was set to `"."` (bare-dot relative path), which `claude plugin validate` rejects with `plugins.0.source: Invalid input`. The schema requires either a subdirectory path starting with `./` (e.g. `"./plugins/foo"`) or an object form with a recognized `source` type. Since this repo IS the plugin (no subdirectory), switched to the `github` object form pointing at the same repo:
  ```json
  "source": {
    "source": "github",
    "repo": "iansmith/ticket-plugin"
  }
  ```
  Users adding the marketplace via `/plugin marketplace add iansmith/ticket-plugin` now resolve the plugin from the same repo (default branch). v1.1.0 and v1.1.1 had an unusable `marketplace.json` for the self-hosted install path described in README — this fix unbreaks it.

### Added

- `CLAUDE.md` at the repo root with a release checklist (validate, bump version, update CHANGELOG, never force-move tags), plugin format reference, authoritative docs links, distribution-path table, and repo workflow conventions. Travels with the repo so future Claude sessions, contributors, and Anthropic reviewers see it.

## [1.1.1] — 2026-05-19

### Changed

- `plugin.json` polish for marketplace submission. Added five optional manifest fields recommended by Anthropic's plugin schema: `$schema` (JSON schema URL for editor autocomplete), `displayName` (human-readable name shown in `/plugin` picker — set to `"Ticket Plugin"`), `repository` (source URL — separate slot in the manager UI from `homepage`), `license` (declared as `"MIT"`, matching the LICENSE file), and `keywords` (`["linear", "jira", "ticketing", "productivity", "tdd", "code-review", "agents"]` for discoverability). Mirrored the polished description into `marketplace.json` so self-hosted-marketplace consumers see the same text.
- No functional changes. Skill behavior, slash command set, install path, and tracking-file format are identical to v1.1.0.

## [1.1.0] — 2026-05-16

### Added

- `/ticket-plugin:plan [constraint]` — investigate the codebase against the ticket's outcome (scoped literally by the optional textual constraint), then write a thorough, parallelism-aware plan into `task_plan.md`. **Phase 0 — red tests first**: identifies the project's test command (auto-detects from `Taskfile.yml` / `Makefile` / `package.json` / `Cargo.toml` / `go.mod` / `pyproject.toml`, or asks once and caches the answer in `task_plan.md`), then writes failing tests for the **expected** behavior from the ticket description (not for the current implementation). Runs them; expects RED. If they unexpectedly pass on the current code, surfaces this and offers `revise / continue / abort` — the bug may already be fixed or the tests aren't exercising the right behavior. Commits the red tests as a separate `[$TICKET] Phase 0: red tests` commit, anchoring the rest of the plan's `Done when` criteria to "test X turns green". Then proceeds with investigation (uses the `Explore` subagent), drafts the plan with detailed work items (files, dependencies, parallel-safety, concrete sub-steps, test-anchored Done-when), and an explicit parallelism analysis. When 2+ items are parallel-safe, optionally fan them out across subagents in `Agent(isolation: "worktree")` worktrees with a strict per-agent prompt (worktree-only constraint, fork from known base SHA, frequent small commits). Monitors via the `Monitor` tool on a 15-minute cadence; auto-stops hard-stuck agents (≥60 min without commits AND ≥3 repeating errors in recent output) — single-condition signals flag but don't auto-stop. After all agents finish, offers auto-merge with confirmation in dependency order (stops cleanly on first conflict; user picks subset). Plan is always written to disk before agents launch, so any later abort still leaves the user with a usable plan.
- `/ticket-plugin:pr` — open a pull request for the active ticket's branch with pre-commit simplify + tests + CodeRabbit polling. Runs Claude Code's `simplify` skill on uncommitted changes (surfaces any changes for user approval), then **runs the project's tests** using the same test-command discovery logic as `/ticket-plugin:plan` (read from `task_plan.md` if cached, else auto-detect, else ask once). Test failures refuse the commit by default with a `fix / commit anyway / abort` prompt; `--no-test` overrides. On green: generates a ticket-anchored commit message, pushes, opens the PR via GitHub MCP or `gh` CLI, triggers CodeRabbit if the PR's base isn't the repo default (`@coderabbitai review`), polls for substantive CodeRabbit feedback every 60 seconds for up to 15 minutes, and categorizes inline comments into 🔴 should-fix / 🟡 could-fix / ⚪ skip with reasoning. Stops after presenting — never auto-applies CodeRabbit suggestions.
- `/ticket-plugin:merge` — end-to-end "ship it" command that combines the four steps you'd otherwise do by hand at the end of a ticket: merges the PR via `gh pr merge` (default strategy: squash), transitions the ticket to a Done-category state on Linear/JIRA, propagates the merged-onto branch to all configured remotes, deletes the local branch (after `gh pr view` confirms `state: MERGED` — squash and rebase strategies work, not just merge-commit), and inlines the body of `/ticket-plugin:archive` to push the final task plan + findings comment and archive locally.
- Confirmation contract: `/ticket-plugin:merge` prompts exactly once before any destructive remote action and offers `yes` / `no` / `merge-only` (merge the PR only, leave ticket + local tracking untouched).
- Safety gates: refuses on dirty working tree, unpushed commits, no upstream, draft PR, merge conflicts, mismatched `headRefName`, or no open PR for the current branch. Soft warnings (BLOCKED / BEHIND / failing checks / no review approval) are surfaced in the confirmation prompt but allow the user to proceed.
- Multi-remote propagation: after `gh pr merge`, the merged-onto branch is pulled locally and then pushed to every remote besides `origin` (mirrors, upstream forks, etc.). Best-effort — a failed push to a non-origin remote warns but doesn't abort.
- Positive-completion heuristic for both ticket-system transitions, applied symmetrically:
  - **JIRA:** filters `done`-category transitions to exclude `Won't Do`, `Canceled`, `Rejected`, `Abandoned`, `Invalid`, `Duplicate` so the ticket lands on a real Done (not a terminal-but-negative state) even if the workflow has many done-category options.
  - **Linear:** filters `type === "completed"` states (which already excludes Linear's `canceled` type) and *also* gates by name against the same negative-completion regex, since teams sometimes misconfigure workflow types.
  - In both cases the selection order is: exact `Done` name match → partial positive-completion words (`done|merged|shipped|complete|fixed|closed|resolved`) → first remaining. If nothing remains after exclusion, the command warns and continues without transitioning (the merge already happened; the user can fix the workflow manually).
- Optional `--pr <N>` and `--strategy <squash|merge|rebase>` arguments on `:merge`.
- Optional `--base <branch>`, `--no-simplify`, and `--no-poll` arguments on `:pr`.

### `:plan` specifics

- **Phase 0 is mandatory** unless the user explicitly says `skip` when asked for the test command. The Step-2 plan's `Done when` criteria are anchored to "named red test turns green" rather than prose assertions — without Phase 0, work items lose their objective verification.
- **Test command is shared between `:plan` Phase 0 and `:pr`'s pre-commit gate** via a `**Test command:**` line cached at the top of `task_plan.md`. Setting it once works for both skills going forward.
- **Three explicit confirmation gates**: clean-tree-before-fanout (Step 4 — offers `commit` / `stash` / `abort`), launch-agents (Step 6), auto-merge (Step 9). The user can abort at any of them, and the plan is on disk by then.
- **Argument scope is literal**: `/ticket-plugin:plan focus on the database layer` excludes everything outside the database layer from BOTH the investigation and the resulting plan, even if the ticket text implies it. The constraint is recorded at the top of the Plan section so a future reader knows what was deliberately left out.
- **Investigation offloads to `Explore` subagent** when available (keeps the orchestrator's context clean); falls back to inline `Grep`/`Glob`/`Read` if Explore is unavailable.
- **Per-agent prompts include**: their slice of the plan verbatim, the relevant findings, hard constraints (worktree-only, fork from `$BASE_SHA`, no `/ticket-plugin` invocations, no pushes), a 3–10 commit cadence target with `[$TICKET]` prefix, completion-on-done (no scope creep), and instructions to commit-and-stop on a real dead end.
- **Monitor heuristics**: status line per agent per tick shows commit count, minutes since last commit, and warning flags. Auto-stop requires BOTH ≥60 min no commits AND ≥3 repeating error patterns in recent task output. Single-condition signals are surfaced as `[warn: ...]` flags without action — the user decides.
- **Auto-merge runs in dependency order** built from the plan's `Depends on` graph: `git merge --no-ff <agent-branch>` for each, stopping cleanly at the first conflict (which the user resolves manually before continuing). Never uses `--force`, never bypasses hooks.
- **Soft cap of 4 parallel agents** with a `merge`/`proceed`/`abort` prompt above that. Monitoring more than 4 agents in parallel is hard for a human to track meaningfully.

### `:pr` specifics

- **Pre-commit test gate** (Step 2 — between simplify and commit): identifies the project's test command using the same logic as `/ticket-plugin:plan` Phase 0 (read from `task_plan.md` cached value, else auto-detect, else ask once). Test failures refuse the commit by default with a `fix / commit anyway / abort` prompt. `--no-test` bypasses the gate entirely. When the user picks `commit anyway`, the commit body gets a `Note: <N> test(s) failing at commit time` line so the failing state is visible in the git log.
- **GitHub backend probing**: prefers a `mcp__github__*` MCP if installed, otherwise falls back to the `gh` CLI. For the CLI path, resolves the binary by checking `/usr/local/bin/gh`, `$HOME/.local/bin/gh`, `/opt/homebrew/bin/gh`, then `$PATH` — first hit wins.
- **CodeRabbit trigger**: posts `@coderabbitai review` as a PR comment if and only if the PR's base branch isn't the repo's default (CodeRabbit auto-runs on default-branch PRs; the comment is required to trigger it on stacked PRs targeting non-trunk branches).
- **Polling contract**: ignores CodeRabbit's "walkthrough"/acknowledgement comments. Substantive signal is non-zero inline review comments at `pulls/{N}/comments` OR a finalized review (`state ∈ {CHANGES_REQUESTED, APPROVED}`) at `pulls/{N}/reviews`. 15-minute timeout returns gracefully without analysis.
- **Categorization is grounded in mandatory verification** (Step 6): before classifying any inline comment, the skill reads the actual code CodeRabbit is commenting on and verifies CodeRabbit's premise against the source (e.g. greps for "unused" symbols, checks type signatures for "nullable" claims, confirms async-ness for "missing await" claims, checks neighboring files for "use idiom Y" claims). A false premise short-circuits to ⚪ Skip — the skill never classifies a comment as Should/Could when CodeRabbit's underlying claim about the code is wrong.
- **Classification follows an ordered decision tree** (not parallel bucket descriptions): (1) fixes bug/security/data-loss/runtime-crash → 🔴 Should; (2) contradicts established codebase pattern → ⚪ Skip (codebase wins); (3) clear positive-ROI improvement → 🟡 Could; (4) pure stylistic nit with no functional benefit → ⚪ Skip; (5) otherwise → 🟡 Could (default to optional, not ignore).
- **Output quotes CodeRabbit's actual words** for each item so the user can sanity-check the classification against the source comment, plus a short "Verdict" and "Why" (the Why field surfaces any verification the skill did).
- **Never auto-applies suggestions** — Step 6 stops at presentation; user explicitly opts in to apply.

### Notes

- Does NOT use `gh pr merge --admin` or any other branch-protection-bypass mechanism. If the PR is blocked, the user resolves the blocker themselves.
- Neither `:pr` nor `:merge` uses `git push --force`, `git commit --no-verify`, or `git reset --hard`. None of these have a place in either flow.
- Failure handling for `:merge`: pre-flight or merge-call failures stop with no state changed. Ticket-system and archive failures after the merge are surfaced but don't roll back the merge (it's already irreversible) — the user can re-run `/ticket-plugin:archive` later to recover.
- Failure handling for `:pr`: pre-flight, simplify-abort, commit-hook, and PR-creation failures all stop cleanly. CodeRabbit poll timeout is not a failure — the skill prints a notice and continues to the summary without analysis.

## [1.0.0] — 2026-05-16

### Added

- Initial public release.
- Four slash commands invoked under the `ticket-plugin` plugin namespace:
  - `/ticket-plugin:start <KEY>` — fresh-start or resume work on a ticket. Fresh-start fetches the ticket, transitions it to **In Progress**, and seeds `task_plan.md`, `findings.md`, `progress.md`. Resume reads the tracking files and prints a summary.
  - `/ticket-plugin:update` — mid-session checkpoint to `progress.md`. The ticket stays active. Local-only.
  - `/ticket-plugin:pause` — snapshot state and clear the active-ticket pointer. Local-only.
  - `/ticket-plugin:archive` — push the final task plan back to the ticket as its description, post `findings.md` as a comment, and archive the local folder. Refuses unless the ticket is already in a terminal state on the ticket system.
- Auto-detection of ticket system (JIRA via Atlassian MCP, or Linear via Linear MCP). If both are configured in the same session, the skill asks rather than guessing.
- Per-project `.project-prefix` discipline: a single-line file in cwd names the ticket prefix (`MAZ`, `PLTF`, `LOU`, etc.) for that project. Skills only operate on tickets matching the cwd's prefix.
- Per-prefix `CURRENT-<PREFIX>` pointer (`~/.claude/ticket-active/CURRENT-MAZ`, etc.) lets parallel sessions on different projects work without interference.
- Tracking files live at `~/.claude/ticket-active/<TICKET>/` while active and move to `~/.claude/ticket-archive/<TICKET>/` on archive. Independent of any git repo.

### Also included

- `install-for-claude-desktop.sh` — bash installer for Claude Desktop users, since Claude Desktop doesn't yet support `/plugin install`. Drops the four commands into `~/.claude/commands/` as `/ticket-start`, `/ticket-pause`, `/ticket-update`, `/ticket-archive` (un-namespaced — Claude Desktop loads them as standalone slash commands). The installer strips the SKILL.md YAML frontmatter and rewrites cross-references from `/ticket-plugin:<name>` to `/ticket-<name>` to match the standalone invocation form.
- `PRIVACY.md` — explicit statement that the plugin collects nothing about the user or their usage, with a transparency note about what other tools (Anthropic's Claude API, the Linear / Atlassian MCPs) the slash-command invocations naturally hit.
- README "Why this exists" section that names the three concrete use cases: per-ticket context isolation, parallel project work via `.project-prefix`, and durable record back to the ticket on archive.

### Notes for downstream consumers

- This plugin requires either the official Anthropic Linear or Atlassian plugin (from the `anthropics/claude-plugins-official` marketplace) to be installed. It is a wrapper around those MCPs and has no built-in API client of its own.
- Skills follow the modern `skills/<name>/SKILL.md` layout (with `disable-model-invocation: true` — these are explicit slash commands, not model-invoked auto-skills).
