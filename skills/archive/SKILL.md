---
description: End the local lifecycle for a ticket. Delegates the documentation push to /slopstop:document (description body + DoD-confirmation comment + findings comment, with idempotent skip-when-current and divergence-stop safety), then mv the local tracking dir to ~/.claude/ticket-archive/. Use /slopstop:archive AFTER moving the ticket to a terminal state (Done/Closed/etc.) on the ticket system yourself. Refuses to run otherwise. Does NOT support --force — if the documentation push would overwrite a divergent managed version on the ticket, archive stops cleanly; the user runs /slopstop:document --force separately to overwrite (after eyeballing the diff), then re-runs :archive. Auto-detects ticket system.
disable-model-invocation: true
---

# /slopstop:archive

End the local lifecycle for a ticket: delegate documentation push to `/slopstop:document` (which handles the description body + DoD-confirmation comment + findings comment, idempotently with per-artifact safety), then move the local tracking dir to `~/.claude/ticket-archive/`. Only operates on tickets already in a terminal state on the ticket system — the user transitions there first, then runs this. Auto-detects ticket system.

`:archive`'s job is the *lifecycle* (terminal-state gate + local archive); the *content push* lives in `/slopstop:document`. See `skills/document/SKILL.md` for the full per-artifact classification, divergence detection, DoD-evidence gathering, and description-appendix logic.

## Project scope (every ticket skill follows this rule)

Read `.project-conf.toml` from cwd. Extract `key` (Linear team key, JIRA project key, or GitHub `owner/repo`) and call it `$PREFIX`. Also note `system` (`linear` | `jira` | `github`) for downstream logic.

**Only operate on `$PREFIX`'s tickets. The branch-IS-selection parser only matches `$PREFIX-\d+`, so a branch encoding a different project's prefix correctly fails the no-match check.**

If `.project-conf.toml` is missing in cwd: stop with `"No .project-conf.toml in cwd. Run /slopstop:gh-init (for GitHub) or create the file manually with system + key."`

## Arguments and target ticket

- If `$ARGUMENTS` is provided and matches `^$PREFIX-\d+$`, use it as `$TICKET`. (Supports archiving a paused ticket without resuming it first.) If it's another prefix, refuse: `"$ARGUMENTS doesn't match this project's prefix ($PREFIX)."`
- If `$ARGUMENTS` is empty, resolve `$TICKET` from the current git branch (see the standard Pre-flight selection lookup above). If the branch doesn't encode a `$PREFIX-N` ticket: stop with the standard no-match error.
- Verify `~/.claude/ticket-active/$TICKET/` exists. If not, error and stop.

## Step 1 — Detect ticket system

`.project-conf.toml`'s `system` field is authoritative for which backend to use; the ToolSearches resolve *how* to talk to it.

Run three ToolSearches in parallel:

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__editJiraIssue,mcp__atlassian__addCommentToJiraIssue,mcp__atlassian__getAccessibleAtlassianResources", max_results=8)
ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__save_comment", max_results=8)
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

## Step 2 — Terminal-state gate (refuse if not terminal)

The specific terminal state doesn't matter; the gate is the category.

**JIRA:**
- Get cloudId via `mcp__atlassian__getAccessibleAtlassianResources` and cache it.
- Fetch via `mcp__atlassian__getJiraIssue($TICKET, cloudId, fields=["status","description"])`.
- If `status.statusCategory.key !== "done"`, refuse.

**Linear:**
- Fetch via `mcp__linear-server__get_issue($TICKET)`.
- If `state.type` ∉ `{"completed", "canceled"}`, refuse.

**GitHub:**
- Parse `$OWNER` and `$REPO` from `.project-conf.toml`'s `key` field. Parse `$N` from `$TICKET`.
- **MCP path:** `${GH_MCP_NS}get_issue(owner=$OWNER, repo=$REPO, issueNumber=$N)` → read `state` and `body`.
- **CLI path:** `$GH issue view $N --json state,body` → same fields.
- If `state !== "CLOSED"`, refuse. Github has no completed/canceled nuance — binary OPEN/CLOSED.

**Refusal output:**

```
Cannot archive $TICKET — ticket is in state '<state name>' (<system> category: <category>).

/slopstop:archive only operates on tickets already in a terminal state on the ticket system.
- JIRA: Done category (Done, Closed, Resolved, Won't Do, Canceled).
- Linear: state type 'completed' or 'canceled'.
- GitHub: issue state CLOSED.

Move $TICKET to a terminal state on <system> first, then re-run /slopstop:archive.
```

Stop. Do not push anything. Do not archive. Do not modify any local files.

**Empty-tracking edge case:** if the gate passes AND all three tracking files are template-empty, ask: `"Tracking is empty — really archive $TICKET? Will push an empty plan and skip the findings comment. (yes / no)"`

## Step 3 — Confirm with the user

Show what will happen and get explicit approval (partially irreversible — hits the ticket system):

> About to archive $TICKET (currently in '<state name>'):
>
> 1. Push documentation to $SYSTEM via `/slopstop:document` — description body (with current ticket description preserved as `## Original description (preserved)`), DoD-confirmation comment (if `task_plan.md` has a Definition of Done section), and findings comment (if `findings.md` has content). Already-current artifacts are skipped cleanly. **If any artifact has a managed version on the ticket that differs from local** (someone hand-edited the ticket, or another session pushed different content), archive STOPS here without moving local tracking — you'd run `/slopstop:document --force` separately to overwrite, then re-run `/slopstop:archive`.
> 2. `mv ~/.claude/ticket-active/$TICKET/ → ~/.claude/ticket-archive/$TICKET/`
>
> Proceed? (yes / no / skip-push)

- `yes`: all three steps.
- `skip-push`: step 1 skipped — no remote push; jump straight to local mv + clear CURRENT. Useful when the ticket is already documented (e.g. via a prior standalone `:document` run) and you just want to close the local loop.
- `no`: stop.

## Step 4 — Push documentation (delegate to `/slopstop:document`)

Skip entirely if user picked `skip-push` in Step 3.

Execute the body of `/slopstop:document` Steps 1–7 against `$TICKET`. Reuse system-specific context from this skill's Step 1 + Step 2 — don't re-fetch (JIRA `cloudId`; Linear nothing extra; GitHub `$OWNER`/`$REPO`/`$N`/`$GH_BACKEND`/`$GH_MCP_NS`/`$GH`). The reader should consult `skills/document/SKILL.md` for the full per-artifact classification, divergence detection (Step 4–5 there), DoD-confirmation comment format + evidence gathering (Step 3b), findings comment format (Step 3c), and description-with-preserved-original-appendix logic (Step 3a). What follows describes how `:archive`'s invocation differs from a standalone `:document` run:

- **No `--force` support in `:archive`.** If `:document`'s Step 5 safety check would stop on a divergent artifact, `:archive` propagates the stop:
  - Print the per-artifact divergence diff exactly as `:document` Step 5 would.
  - Skip Step 5 (local archive) entirely — local tracking stays put.
  - Append: `"Archive stopped on documentation divergence. Review the diffs above. To proceed: run /slopstop:document --force (after eyeballing the diff) to overwrite the ticket's version with local, then re-run /slopstop:archive."`
  - Exit cleanly. Not an error — a deliberate refusal. The friction is intentional: archive is the irreversible end of the local lifecycle, and forcing past a documentation divergence shouldn't be a single-flag thing on the lifecycle-ending command. The standalone `:document --force` is the explicit acknowledgment.
- **No `--dry-run` propagation.** `:archive`'s Step 3 confirmation prompt is the user's preview; for a documentation-only dry-run, cancel `:archive` and run `/slopstop:document --dry-run` standalone.
- **Reused inputs.** `:document`'s Step 1 (detect system) and Step 2 (fetch state) are already done. Reuse cached values; skip the duplicate fetches.

If `:document` completes successfully (no divergence + all `new` or `unchanged` outcomes), proceed to Step 5.

`progress.md` is intentionally never pushed (`:document` enforces this).

## Step 5 — Archive locally

- `mv ~/.claude/ticket-active/$TICKET ~/.claude/ticket-archive/$TICKET`
- If destination already exists (ticket was reopened and archived twice): rename to `~/.claude/ticket-archive/$TICKET-<timestamp>`. Don't lose history.

## Step 6 — Confirm

```
Archived $TICKET (was '<state name>' on $SYSTEM).

Description:   <"updated (new)" | "already current — skipped" | "skipped (skip-push selected)">
DoD comment:   <"posted (new)" | "already current — skipped" | "skipped (no DoD section in task_plan.md)" | "skipped (skip-push selected)">
Findings:      <"posted (new)" | "already current — skipped" | "skipped (findings.md template-empty)" | "skipped (skip-push selected)">
Local:         archived to ~/.claude/ticket-archive/$TICKET/
```

(The per-artifact verdicts come from the inlined `:document` Step 7 output.) `:archive` doesn't surface `--force` cases here — `:archive` itself doesn't support `--force`, so all `divergent` artifacts cause Step 4 to stop instead of pushing.

## Rules

- This command does NOT transition the ticket-system state. It refuses unless the ticket is *already* terminal. The user controls the transition; this is the local follow-up.
- **`:archive` delegates the documentation push to `/slopstop:document`** (Step 4 inlines its body). All push-side logic — per-artifact classification, idempotent skip-when-current, divergence detection, DoD-evidence gathering, description appendix — lives in `:document`. `:archive` adds the terminal-state gate (Step 2) and the local-tracking move (Step 5).
- **`:archive` does NOT support `--force`.** If `:document`'s divergence check fires, `:archive` propagates the stop without touching local tracking. The user runs `/slopstop:document --force` separately (after eyeballing the diff) to overwrite, then re-runs `/slopstop:archive`. The friction is intentional — archive is the irreversible end of the local lifecycle; forcing past a documentation divergence shouldn't be a single-flag thing on the lifecycle-ending command.
- After archive, future `/slopstop:start $TICKET` treats it as fresh-start (which would then ask whether to reopen the terminal ticket).
- To resume an archived ticket without going through the reopen prompt: manually `mv ~/.claude/ticket-archive/$TICKET ~/.claude/ticket-active/` first.
- Failure handling:
  - Ticket-system detection fails: error and stop. No state changed.
  - Terminal-state gate fails: refusal message and stop. No state changed.
  - `:document` reports divergence: print the per-artifact diff, skip Step 5, exit cleanly. Not an error — a deliberate refusal. Local tracking unchanged so re-run after divergence resolution works.
  - `:document` mid-push failure (network, MCP error): `:document` reports per-artifact success/failure. `:archive` then SKIPS Step 5 (local move) — half-published remote state without local archive lets the user retry the push without losing the active tracking dir.
  - Archive move fails (Step 5, after all pushes succeeded): report. Don't roll back the ticket-system push (already correct remotely); leave the active dir in place. User can re-run `:archive` — Step 4's idempotency means the push is a no-op and Step 5 retries the move.
