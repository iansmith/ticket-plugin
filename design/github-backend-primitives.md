# GitHub backend primitives — Design Document

**Status:** Draft, 2026-05-25.

## Summary

The 4 lifecycle skills (`:start`, `:document`, `:archive`, `:merge`) currently dispatch between Linear (`mcp__linear-server__*`) and JIRA (`mcp__atlassian__*`) at three points: Step 1 (MCP detection / `$SYSTEM` resolution), each skill's call sites for issue read/write operations, and `:merge`'s "advance one state" logic. For github-backed projects (those with `system = "github"` in `.project-conf.toml`), each skill currently stops at Step 1 with *"No ticket-system MCP found."*

This document defines the github backend that closes that gap. It enumerates the github-specific implementation of every primitive the 4 skills need, picks one canonical dispatch shape (MCP-preferred, `gh` CLI fallback — symmetric with how `:pr` already works), and specifies the exact snippets each skill should embed at its call sites. The 4 skills consume this doc — each adds a `**GitHub:**` block alongside its existing `**JIRA:**` / `**Linear:**` blocks, copying the relevant snippet verbatim.

The doc exists so the four skills stay consistent and the dispatch decisions are made once, not re-litigated per skill.

## Goals

- Concrete, verbatim-copyable snippets for every github operation a lifecycle skill performs.
- One canonical Step 1 detection block (3-way ToolSearch + `$SYSTEM` + `$GH_BACKEND` resolution).
- Symmetric with `:pr`'s existing MCP-preferred / CLI-fallback shape — no new dispatch pattern invented.
- Tolerant of github MCP namespace variance (canonical `mcp__github__*` vs Anthropic-managed `mcp__plugin_github_github__*`).
- Idempotency notes per primitive so `:document`'s skip-when-current and divergence detection extend cleanly.

## Non-goals

- A general GitHub Issues API reference. This doc covers only what the 4 lifecycle skills consume.
- GitHub Projects v2 / status fields / native state machine support. Github's state machine is intentionally shallow here — label-based via `[status_labels]`, with binary OPEN/CLOSED as the only intrinsic state.
- Cross-skill shared helper file. Each `SKILL.md` is self-contained; the snippets in this doc get copied (not included) into each skill that needs them. If duplication becomes painful later, factor then.
- Migration of existing Linear/JIRA logic to use the github model. Different systems, different shapes — leave them be.

## Architectural decisions

These are settled here so the 4 skills don't have to re-decide each.

### 1. MCP-preferred, `gh` CLI fallback (mirror `:pr`)

Each lifecycle skill's Step 1 runs a third `ToolSearch` for github MCP tools alongside the existing JIRA + Linear searches. If github MCP tools are present, set `$GH_BACKEND = "MCP"` and use MCP tool calls at each call site. If not, set `$GH_BACKEND = "CLI"`, resolve `$GH` via the trial-path logic from `:pr` Step 4a, and use `gh` CLI invocations at each call site.

Rationale: `:pr` already established this shape. Keeping the lifecycle skills symmetric means a future github MCP installation is picked up automatically without further skill changes.

### 2. Tool-name namespace fallback

Github MCPs may be installed under either of two namespaces:

- `mcp__github__*` — canonical (e.g., open-source GitHub MCP server).
- `mcp__plugin_github_github__*` — Anthropic's managed `github@claude-plugins-official` plugin's namespacing.

The Step 1 `ToolSearch` tries the canonical names first. On empty result, tries the plugin-namespaced variant. If both empty, fall through to `$GH_BACKEND = "CLI"`. The Step 1 detection block (below) encodes this fallback.

### 3. No shared cross-skill helper file

Each `SKILL.md` is self-contained — there's no include mechanism, and a cross-skill shared file would need a new convention. Github logic gets copied verbatim from this design doc into each of the 4 consumer skills, the same way JIRA and Linear blocks are duplicated today. If maintenance pain accumulates, factor into a shared `skills/_shared/github.md` (or similar) then.

### 4. `[status_labels]` parsing — inline TOML read snippet

`.project-conf.toml` is parsed inline by each skill (no shared parser exists today). The github backend needs to read the nested `[status_labels]` table — slightly trickier than the flat top-level `system` / `key` / `prefix`. A reusable Bash snippet is specified once (below) and embedded at each skill's call site where a label name is needed.

## Step 1 detection block (canonical)

Embed this verbatim into each lifecycle skill's Step 1, alongside the existing JIRA + Linear ToolSearch calls. Run the three searches in parallel (single message, three tool calls).

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__getAccessibleAtlassianResources,mcp__atlassian__getTransitionsForJiraIssue,mcp__atlassian__transitionJiraIssue", max_results=8)

ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__list_issue_statuses", max_results=8)

# New for github — try canonical names first
ToolSearch(query="select:mcp__github__get_issue,mcp__github__add_issue_comment,mcp__github__update_issue,mcp__github__list_issue_comments", max_results=8)
```

Set `$SYSTEM`:

- JIRA tools only (and `.project-conf.toml` says `system = "jira"`) → `JIRA`
- Linear tools only (and `system = "linear"`) → `Linear`
- Github MCP tools resolved above (and `system = "github"`) → `GitHub`; **`$GH_BACKEND = "MCP"`**
- Github MCP search returned empty AND `system = "github"` → run the fallback ToolSearch:
  ```
  ToolSearch(query="select:mcp__plugin_github_github__get_me,mcp__plugin_github_github__add_issue_comment,mcp__plugin_github_github__issue_write", max_results=8)
  ```
  If non-empty → `$SYSTEM = "GitHub"`, **`$GH_BACKEND = "MCP"`** (the actual tool names are `mcp__plugin_github_github__*`; record the namespace prefix as `$GH_MCP_NS = "mcp__plugin_github_github__"` so call sites can construct the right tool name).
  If still empty → `$SYSTEM = "GitHub"`, **`$GH_BACKEND = "CLI"`** (resolve `$GH` via the snippet below).
- Multiple systems detected ambiguously (the user's `.project-conf.toml` says one thing but tools for another are present) → trust `.project-conf.toml`'s `system` value. The other systems' tools are coincidentally available; they're not the active backend.
- Neither MCP nor matching `system` value → stop with `"No ticket-system MCP found for system='<value>' in .project-conf.toml. Configure the matching MCP and retry."`

The key rule: `.project-conf.toml`'s `system` field is authoritative. The ToolSearches are about *resolving the backend implementation* (MCP vs CLI for github; MCP for JIRA/Linear), not about *choosing the system*. This avoids the ambiguous case where the user has both Linear and github MCPs installed and the skills can't tell which project they're in.

## `$GH` binary discovery (CLI path)

Used when `$GH_BACKEND = "CLI"`. Lifted from `:pr` Step 4a verbatim.

> For the **CLI** path, find the `gh` binary. Try each in order; use the first one where `<path> --version` succeeds:
>
> 1. `/usr/local/bin/gh`
> 2. `$HOME/.local/bin/gh`
> 3. `/opt/homebrew/bin/gh`
> 4. `command -v gh` (i.e. whatever `$PATH` resolves)
>
> Save as `$GH`. If none resolve, stop:
> ```
> Neither GitHub MCP nor `gh` CLI found. Install one of:
> - gh CLI: https://cli.github.com/
> - GitHub plugin: /plugin install github@claude-plugins-official
> ```

Verify auth: `$GH auth status` succeeds.

Embed this snippet verbatim into each lifecycle skill's Step 1 immediately after `$GH_BACKEND` is set to `CLI`.

## `[status_labels]` read snippet

Used when `$SYSTEM = "GitHub"` and the skill needs the in-progress or in-review label name. Reads the `[status_labels]` table from `.project-conf.toml` in cwd.

Bash one-liner per key (no TOML parser dependency — minimal grep/sed):

```bash
# Read [status_labels].in_progress (required for github projects).
IN_PROGRESS_LABEL=$(awk '
  /^\[status_labels\]/ { in_section=1; next }
  /^\[/ && !/^\[status_labels\]/ { in_section=0 }
  in_section && /^[[:space:]]*in_progress[[:space:]]*=/ {
    sub(/^[^=]*=[[:space:]]*"/, "")
    sub(/".*$/, "")
    print
    exit
  }
' .project-conf.toml)

# Same shape for in_review (optional — empty if not present).
IN_REVIEW_LABEL=$(awk '
  /^\[status_labels\]/ { in_section=1; next }
  /^\[/ && !/^\[status_labels\]/ { in_section=0 }
  in_section && /^[[:space:]]*in_review[[:space:]]*=/ {
    sub(/^[^=]*=[[:space:]]*"/, "")
    sub(/".*$/, "")
    print
    exit
  }
' .project-conf.toml)
```

If `$IN_PROGRESS_LABEL` is empty (github project but no `[status_labels].in_progress`): stop with `"system='github' requires [status_labels].in_progress in .project-conf.toml. Run /slopstop:gh-init or add it manually."`

`$IN_REVIEW_LABEL` empty is fine — that's the signal for 3-state workflow (used by `:merge`).

## Workflow shape detection (used by `:merge`)

Github's workflow is binary by default (OPEN / CLOSED). `[status_labels]` adds intermediate states. Two supported workflows:

| `in_progress` | `in_review` | Workflow shape | `:merge` behavior |
|---|---|---|---|
| Set | Unset | **3-state** (todo → in-progress → done) | `gh issue close $N` + remove `in_progress` label |
| Set | Set | **4-state** (todo → in-progress → in-review → done) | Swap: remove `in_progress`, add `in_review`. Issue stays open. `:archive` closes it later. |

`:merge` reads `$IN_PROGRESS_LABEL` and `$IN_REVIEW_LABEL` (per the snippet above) and dispatches based on whether `$IN_REVIEW_LABEL` is empty.

No introspection of label history or comments needed — the workflow shape is declared in `.project-conf.toml`.

## Primitives

Each primitive lists both backends (MCP and CLI) plus the consumer(s). MCP names assume canonical namespace; if `$GH_MCP_NS = "mcp__plugin_github_github__"` was recorded in Step 1, substitute that prefix.

### Read issue (state + body + labels + assignees + milestone)

**Consumer:** `:start` Step 2, `:document` Step 2, `:archive` Step 2, `:merge` Step 2.

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__get_issue(owner=$OWNER, repo=$REPO, issueNumber=$N)` |
| CLI | `$GH issue view $N --json number,state,body,labels,assignees,milestone,url` |

`$OWNER` and `$REPO` come from `.project-conf.toml`'s `key` field, which is `owner/repo` for github projects.

`$N` is the numeric part of `$TICKET` (e.g. `$TICKET = BILL-8` → `$N = 8`).

Returns JSON. Consumer parses fields as needed:
- `state`: `"OPEN"` or `"CLOSED"` (use for terminal-state gate)
- `body`: the description markdown (use for divergence detection)
- `labels`: array of `{name, color, description}` — find `$IN_PROGRESS_LABEL` / `$IN_REVIEW_LABEL` membership
- `assignees`, `milestone`: for `task_plan.md` metadata at `:start` time

### Read comments

**Consumer:** `:document` Step 2 (find the existing DoD and findings comments by leading marker).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__list_issue_comments(owner=$OWNER, repo=$REPO, issueNumber=$N)` |
| CLI | `$GH api repos/$OWNER/$REPO/issues/$N/comments` |

Returns array of `{id, body, user.login, created_at, updated_at}`. Consumer matches by leading marker (e.g. `## Definition of Done` or `## Findings (from local tracking)`) to find the comment to update vs. create.

### Set issue body (description)

**Consumer:** `:document` Step 6 (description update).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__update_issue(owner=$OWNER, repo=$REPO, issueNumber=$N, body=$BODY)` |
| CLI | `$GH issue edit $N --body "$BODY"` (or HEREDOC to preserve markdown) |

CLI HEREDOC form for multi-line bodies (recommended):
```bash
$GH issue edit $N --body "$(cat <<'EOF'
<body content>
EOF
)"
```

**Idempotency:** caller should compare the local `$BODY` (whitespace-trimmed) to the gh-fetched body (also trimmed) and skip the call if equal. Github normalizes `\r\n` to `\n` on its end, so the trim is necessary. Existing JIRA/Linear logic already does this; the github path follows the same pattern.

### Add comment

**Consumer:** `:document` Step 6 (DoD comment create, findings comment create), `:archive` Step 4 (delegates to `:document`).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__add_issue_comment(owner=$OWNER, repo=$REPO, issueNumber=$N, body=$BODY)` |
| CLI | `$GH issue comment $N --body "$BODY"` (HEREDOC for multi-line) |

**Idempotency:** the caller distinguishes "create new" from "update existing" using the read-comments primitive. If a comment with the expected leading marker exists, use `edit comment` instead of `add comment`.

### Edit comment

**Consumer:** `:document` Step 6 (DoD/findings comment update when content diverged).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__update_issue_comment(owner=$OWNER, repo=$REPO, commentId=$ID, body=$BODY)` |
| CLI | `$GH api -X PATCH "repos/$OWNER/$REPO/issues/comments/$ID" -f body="$BODY"` |

`$ID` is the numeric comment id from the read-comments primitive.

**Idempotency:** same whitespace-trimmed equality check as for body. Skip if the gh-fetched comment body matches local.

### Add label

**Consumer:** `:start` Step 3 (transition to In Progress), `:merge` Step 5 (4-state: add `in_review`).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__add_issue_labels(owner=$OWNER, repo=$REPO, issueNumber=$N, labels=[$LABEL])` |
| CLI | `$GH issue edit $N --add-label "$LABEL"` |

Github silently accepts adding a label that's already on the issue (idempotent by default). The skill doesn't need to pre-check.

**Pre-condition:** `$LABEL` must already exist on the repo. The label `status:in-progress` is created by `/slopstop:gh-init` (when implemented; see design/ticket-gh-init.md). For the bootstrap on slopstop itself, the label was created manually before BILL-8 started.

### Remove label

**Consumer:** `:merge` Step 5 (3-state: remove `in_progress`; 4-state: remove `in_progress` while adding `in_review`).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__remove_issue_label(owner=$OWNER, repo=$REPO, issueNumber=$N, label=$LABEL)` |
| CLI | `$GH issue edit $N --remove-label "$LABEL"` |

Github silently accepts removing a label that wasn't on the issue (idempotent by default).

### Close issue

**Consumer:** `:merge` Step 5 (3-state only; 4-state leaves it open for `:archive` to close).

| Backend | Invocation |
|---|---|
| MCP | `mcp__github__update_issue(owner=$OWNER, repo=$REPO, issueNumber=$N, state="closed")` |
| CLI | `$GH issue close $N` |

`gh issue close` is idempotent (closing a closed issue succeeds quietly). No pre-check needed.

## Per-skill consumption summary

Which skill needs which primitives, for quick reference when adding the `**GitHub:**` block:

| Skill | Primitives used |
|---|---|
| `:start` | Read issue (Step 2); Add label (Step 3) |
| `:document` | Read issue (Step 2); Read comments (Step 2); Set issue body (Step 6); Add comment (Step 6); Edit comment (Step 6) |
| `:archive` | Read issue (Step 2 terminal gate); delegates to `:document` for Step 4 push |
| `:merge` | Read issue (Step 2); Add label + Remove label + Close issue (Step 5, dispatched on workflow shape) |

## Open questions / TBDs

- **Github MCP tool names are not stable yet.** The canonical tool list assumed above (`mcp__github__get_issue`, `mcp__github__add_issue_comment`, `mcp__github__update_issue`, etc.) is based on what's commonly installed. If a particular install exposes different names, the ToolSearch in Step 1 may need adjustment. The plugin-namespaced fallback (`mcp__plugin_github_github__*`) at least covers the Anthropic-managed install; other MCPs may need additional fallbacks added later.

- **Edit-comment MCP availability.** Some github MCP installs may not expose an edit-comment tool. If `$GH_BACKEND = "MCP"` but the edit-comment tool is missing, fall through to `$GH_BACKEND = "CLI"` *just for that operation* (and use `$GH api -X PATCH …`). Document this as a per-op fallback if it comes up in practice.

- **Multi-repo projects.** This doc assumes `key = "owner/repo"` is a single repo. Future work (cross-repo tickets, monorepos with multiple GH issue trackers) would need a richer `key` shape. Out of scope for this ticket.

- **Race on label add-then-remove (4-state `:merge`).** When `:merge` swaps labels in 4-state mode (`--remove-label in_progress --add-label in_review`), gh CLI does both in one invocation (atomic from the user's perspective). The MCP equivalent may require two separate calls; if the first succeeds and the second fails, the issue ends up label-less which is a confusing intermediate state. Caller should detect partial failure and either retry or surface clearly.

## Consumers

- [skills/start/SKILL.md](../skills/start/SKILL.md) — Step 1 detection, Step 2 fetch (read issue), Step 3 transition (add label).
- [skills/document/SKILL.md](../skills/document/SKILL.md) — Step 1 detection, Step 2 fetch (read issue + read comments), Step 6 push (set body, add/edit comment).
- [skills/archive/SKILL.md](../skills/archive/SKILL.md) — Step 1 detection, Step 2 terminal gate (read issue → `state == "CLOSED"`).
- [skills/merge/SKILL.md](../skills/merge/SKILL.md) — Step 1 detection, Step 2 compute next state (workflow shape from `.project-conf.toml`), Step 5 apply (add label / remove label / close issue).

## Dependencies

- [`project-conf-toml.md`](project-conf-toml.md) — defines `system = "github"`, `key = "owner/repo"`, `prefix`, and the `[status_labels]` table that this backend consumes.
- [`multi-ticket.md`](multi-ticket.md) — defines the lifecycle skill shape (`:start` / `:document` / `:archive` / `:merge`) that this backend slots into.
- [`ticket-gh-init.md`](ticket-gh-init.md) — bootstrap skill that writes `.project-conf.toml` + creates the labels this backend reads.

## See also

- BILL-8 — the ticket that implements this design across the 4 consumer skills.
- BILL-2 (closed) — the precedent; surfaced the gap when its archived documentation never landed on the github issue.
