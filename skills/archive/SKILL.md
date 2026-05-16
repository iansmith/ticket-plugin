---
description: Push the active ticket's final task plan back to the ticket as its description and findings as a comment, then archive the local folder. Use /ticket-plugin:archive AFTER moving the ticket to a terminal state (Done/Closed/etc.) on the ticket system yourself. Refuses to run otherwise. Auto-detects ticket system.
disable-model-invocation: true
---

# /ticket-plugin:archive

Push final tracking state to the ticket system, archive the local folder, clear `CURRENT-<PREFIX>`. Only operates on tickets already in a terminal state on the ticket system — the user transitions there first, then runs this. Auto-detects ticket system.

## Project scope (every ticket skill follows this rule)

Read `.project-prefix` from cwd. It contains a single prefix like `LOU`, `MAZ`, or `PLTF`. Call that value `$PREFIX`.

**Only operate on `$PREFIX`'s tickets. Never read, write, or clear `CURRENT-*` files for any other prefix.**

If `.project-prefix` is missing in cwd: stop with `"No .project-prefix in cwd. Create one (e.g. echo MAZ > .project-prefix) and retry."`

## Arguments and target ticket

- If `$ARGUMENTS` is provided and matches `^$PREFIX-\d+$`, use it as `$TICKET`. (Supports archiving a paused ticket without resuming it first.) If it's another prefix, refuse: `"$ARGUMENTS doesn't match this project's prefix ($PREFIX)."`
- If `$ARGUMENTS` is empty, `$TICKET` = contents of `~/.claude/ticket-active/CURRENT-$PREFIX`. If empty or missing: `"No active $PREFIX ticket to archive."` and stop.
- Verify `~/.claude/ticket-active/$TICKET/` exists. If not, error and stop.

## Step 1 — Detect ticket system

Run two ToolSearches in parallel:

```
ToolSearch(query="select:mcp__atlassian__getJiraIssue,mcp__atlassian__editJiraIssue,mcp__atlassian__addCommentToJiraIssue,mcp__atlassian__getAccessibleAtlassianResources", max_results=8)
ToolSearch(query="select:mcp__linear-server__get_issue,mcp__linear-server__save_issue,mcp__linear-server__save_comment", max_results=8)
```

Set `$SYSTEM`:
- JIRA only → `JIRA`
- Linear only (`mcp__linear-server__*`) → `Linear`
- Both → ask: `"Both JIRA and Linear MCP are configured. Which ticket system is $TICKET on? (jira / linear)"`
- Neither → stop: `"No ticket-system MCP found. Configure Atlassian or Linear MCP and retry."`

## Step 2 — Terminal-state gate (refuse if not terminal)

The specific terminal state doesn't matter; the gate is the category.

**JIRA:**
- Get cloudId via `mcp__atlassian__getAccessibleAtlassianResources` and cache it.
- Fetch via `mcp__atlassian__getJiraIssue($TICKET, cloudId, fields=["status","description"])`.
- If `status.statusCategory.key !== "done"`, refuse.

**Linear:**
- Fetch via `mcp__linear-server__get_issue($TICKET)`.
- If `state.type` ∉ `{"completed", "canceled"}`, refuse.

**Refusal output:**

```
Cannot archive $TICKET — ticket is in state '<state name>' (<system> category: <category>).

/ticket-plugin:archive only operates on tickets already in a terminal state on the ticket system.
- JIRA: Done category (Done, Closed, Resolved, Won't Do, Canceled).
- Linear: state type 'completed' or 'canceled'.

Move $TICKET to a terminal state on <system> first, then re-run /ticket-plugin:archive.
```

Stop. Do not push anything. Do not archive. Do not modify any local files.

**Empty-tracking edge case:** if the gate passes AND all three tracking files are template-empty, ask: `"Tracking is empty — really archive $TICKET? Will push an empty plan and skip the findings comment. (yes / no)"`

## Step 3 — Confirm with the user

Show what will happen and get explicit approval (partially irreversible — hits the ticket system):

> About to archive $TICKET (currently in '<state name>'):
>
> 1. Update <system> description with final task plan (current desc preserved as `## Original description (preserved)` section)
> 2. Post a "Findings" comment with contents of findings.md (skipped if template-empty)
> 3. `mv ~/.claude/ticket-active/$TICKET/ → ~/.claude/ticket-archive/$TICKET/`
> 4. Clear `~/.claude/ticket-active/CURRENT-$PREFIX`
>
> Proceed? (yes / no / skip-push)

- `yes`: all four steps.
- `skip-push`: steps 3 and 4 only — archive locally, no ticket-system push.
- `no`: stop.

## Step 4 — Push to ticket system (unless skip-push)

Use the description already fetched in Step 2 — don't re-fetch.

Build the new description (both systems accept markdown, concat directly):

```
<body of task_plan.md verbatim>

---

## Original description (preserved)

<existing description from the ticket>
```

**JIRA:**
- Call `mcp__atlassian__editJiraIssue` with the new description. Do NOT touch status — the ticket is already in a done state.
- If `findings.md` has content beyond the template scaffold (any `## ` heading or any prose past the empty placeholder), call `mcp__atlassian__addCommentToJiraIssue` with:
  ```
  ## Findings (from local tracking)

  <body of findings.md verbatim>
  ```
  Skip if findings.md is template-empty.

**Linear:**
- Call `mcp__linear-server__save_issue` with the issue id and the new description. Do NOT touch state.
- If `findings.md` has content beyond the template scaffold, call `mcp__linear-server__save_comment` with the issue id and body:
  ```
  ## Findings (from local tracking)

  <body of findings.md verbatim>
  ```
  Skip if findings.md is template-empty.

`progress.md` is intentionally NOT pushed — per-session diary is noise on a ticket.

## Step 5 — Archive locally

- `mv ~/.claude/ticket-active/$TICKET ~/.claude/ticket-archive/$TICKET`
- If destination already exists (ticket was reopened and archived twice): rename to `~/.claude/ticket-archive/$TICKET-<timestamp>`. Don't lose history.
- `: > ~/.claude/ticket-active/CURRENT-$PREFIX` (empty, don't delete)

## Step 6 — Confirm

```
Archived $TICKET (was '<state name>' on <system>).

Push: <"description updated + findings comment posted" | "description updated" | "skipped (skip-push)">
Local: archived to ~/.claude/ticket-archive/$TICKET/
```

## Rules

- This command does NOT transition the ticket-system state. It refuses unless the ticket is *already* terminal. The user controls the transition; this is the local follow-up.
- After archive, future `/ticket-plugin:start $TICKET` treats it as fresh-start (which would then ask whether to reopen the terminal ticket).
- To resume an archived ticket without going through the reopen prompt: manually `mv ~/.claude/ticket-archive/$TICKET ~/.claude/ticket-active/` first.
- Failure handling:
  - Ticket-system detection fails: error and stop. No state changed.
  - Terminal-state gate fails: refusal message and stop. No state changed.
  - Description update fails: do NOT archive. Report; leave local folder intact for retry. (Half-pushed remote state is the worst outcome — all-or-nothing on the ticket-system side.)
  - Description update succeeds but comment fails: report and proceed with archive — description has the bulk; user can post the comment manually.
  - Archive move fails: report. Don't roll back the ticket-system push (already correct remotely); leave the active dir in place.
