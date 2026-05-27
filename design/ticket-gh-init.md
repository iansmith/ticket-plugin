# `ticket-gh-init` — Design Document

**Status:** Draft, 2026-05-24.

## Summary

A one-time bootstrap skill that prepares a GitHub-backed project for use with the `ticket-*` workflow. It creates the state labels in the GitHub repo and writes a fresh `.project-conf.toml`. It is idempotent and re-runnable.

The skill prints a clear explainer **before** taking any action, then asks a single user question (3-state vs. 4-state workflow), then performs all changes.

## Goals

- Set up everything a GitHub-backed project needs in one command.
- Make all side-effects (label creation, file write) visible upfront via the explainer.
- Idempotent: re-running detects existing setup and confirms rather than duplicating.

## Non-goals

- Migrating an existing Linear or JIRA project to GitHub. Out of scope; that's a manual project-level decision.
- Configuring GitHub itself beyond labels — milestones, project boards, branch-protection rules are user responsibility.
- Authenticating to GitHub. Requires `gh auth status` to be clean before running.
- Repos hosted on enterprise GitHub variants (GHE Server / Cloud). First cut targets github.com only; the `gh` CLI commands used here generally work against GHE, but it's not validated.

## Flow

### 1. Pre-flight

- Verify cwd is inside a git repo: `git rev-parse --is-inside-work-tree`.
- Verify a GitHub remote exists: parse `git remote get-url origin` for a `github.com` host.
- Verify `gh auth status` succeeds. If not, stop with `"GitHub CLI not authenticated. Run \`gh auth login\` first."`
- Determine `owner/repo` from `gh repo view --json nameWithOwner`. Display it and pause for user confirmation (`Confirm this is the right repo? [y/N]`). Misidentification is a hard error.

### 2. Explainer — printed before any action

```
/ticket-gh-init prepares this GitHub repo for the ticket-* workflow.

It will:
  - Create state labels in <owner>/<repo> (if missing):
      • status:in-progress
      • status:in-review  (only in the 4-state workflow)
  - Write .project-conf.toml in this directory with:
      • system = "github"
      • key    = "<owner>/<repo>"
      • prefix = "<your-chosen-prefix>"  (short ticket-ID prefix, e.g. BILL)
      • the chosen status_labels mapping

It will NOT:
  - Modify any existing issue, PR, or branch.
  - Touch the repo's other settings (milestones, projects, branch protection).
  - Authenticate to GitHub for you (handled separately by `gh auth login`).
```

Plain text rendered to stdout. The user reads it before answering the question in step 3.

### 3. Single question — 3-state vs. 4-state

```
Choose a workflow:

  3-state: todo → in-progress → done
  4-state: todo → in-progress → in-review → done

In BOTH workflows, the PR process includes the pre-merge simplify pass
and a CodeRabbit review. Those are part of the work in `in-progress`,
not what `in-review` means.

The 4-state workflow adds an `in-review` stage AFTER the code is
complete relative to its requirements. The stage exists for
shake-down validation:

  • another person running or reviewing the working code, OR
  • the author dogfooding the code for a period to make sure it
    behaves the way they wanted before declaring it done.

In other words: `in-review` is "the code works; now we're verifying
it does the right thing in practice." Choose 4-state if your workflow
includes such a validation step; choose 3-state otherwise.

Which workflow? [3/4]
```

The user types `3` or `4`. No other prompts. The explainer + this question are the only user-facing interaction before action.

### 4. Apply changes (idempotent)

**Label creation:**

For each label that the chosen workflow needs (`status:in-progress` always; `status:in-review` only in 4-state mode):

1. Check existence: `gh label list --json name`.
2. If missing: `gh label create "<name>" --color <color> --description "<desc>"`.
3. If present: print `"label '<name>' already exists — skipping"` and continue.

Defaults:

| Label | Color | Description |
|---|---|---|
| `status:in-progress` | `#fbca04` | Ticket is actively being worked on |
| `status:in-review` | `#0e8a16` | Code is complete; shake-down validation in progress (other-person review and/or author dogfood) |

Overridable via flags (see Arguments below) if the user wants to match an existing repo convention.

**`.project-conf.toml` write:**

- If the file already exists, read it. If `system` is not `"github"` or `key` doesn't match the current repo: stop with conflict error. The skill refuses to overwrite a different project's config.
- If `system = "github"` and `key` matches: merge in the chosen `[status_labels]` (preserving any other namespaces like `[rag]`, `[exp]`).
- If absent: write a fresh file in the format defined in [project-conf-toml.md](project-conf-toml.md).

Writes go through a temp file + atomic rename so a crashed write never leaves a partial file.

### 5. Confirm

```
ticket-gh-init complete.

Created labels: status:in-progress, status:in-review
Wrote: .project-conf.toml (system=github, key=<owner>/<repo>)

You can now run /ticket-start <ISSUE-NUMBER> to begin work.
```

If any labels were already present and any config was already correct, the confirm line for that item reads "already configured."

## Arguments and flags

```
/ticket-gh-init [--workflow {3,4}]
                [--in-progress-color HEX]
                [--in-progress-label NAME]
                [--in-review-color HEX]
                [--in-review-label NAME]
```

- `--workflow {3,4}` — skip the user prompt in step 3.
- `--in-progress-color HEX`, `--in-review-color HEX` — override default label colors.
- `--in-progress-label NAME`, `--in-review-label NAME` — override default label names (e.g. for a repo that already uses `🚧 In Progress` instead of `status:in-progress`).

All optional. Without any, the skill behaves as described above.

## Idempotency contract

Re-running `/ticket-gh-init` on a fully-configured project must:

1. Detect existing labels and skip creation.
2. Detect existing `.project-conf.toml`, validate it, and confirm "already configured."
3. Exit successfully.

This makes the skill safe to run as a "verify" operation, not just a "set up once" operation. CI scripts or onboarding flows can invoke it without conditional logic.

## Error matrix

| Condition | Behavior |
|---|---|
| `gh auth status` fails | Stop with auth instructions. |
| Not in a git repo | Stop: `"Run inside a git repository with a GitHub remote."` |
| No GitHub remote | Stop: `"No GitHub remote found. Add \`origin\` first."` |
| Existing `.project-conf.toml` for `system != "github"` | Stop: `"This project is configured for system='<other>'. Refusing to overwrite."` |
| Existing `.project-conf.toml` with mismatched `key` | Stop: `"Existing config points to '<other>/<repo>'; current repo is '<owner>/<repo>'. Refusing to overwrite."` |
| User cancels the repo-confirm prompt | Stop: `"No changes made."` |
| User cancels the workflow question | Stop: `"No changes made."` |
| Label creation API error | Stop with the API error; do not partially apply remaining labels. |
| `.project-conf.toml` write fails | Stop with the OS error; temp-file + atomic rename ensures no partial file. |

Partial application is forbidden — any error mid-flight stops the skill and reports what was already done so the user can clean up by hand if needed.

## Open questions (refinement, not blockers)

- **`done` label.** Should the skill also create a `done` label? Probably no — GitHub's native open/closed already represents done. Mentioned for completeness.
- **`.gitignore` for `.project-conf.toml`.** Should the skill offer to add the config file to `.gitignore`? Depends on whether the project wants the config committed (more shareable) or local-only (more flexible per-clone). Default: do nothing; let the user decide.

## Prerequisites

- [`.project-conf.toml` format](project-conf-toml.md) — defines the file this skill writes.

No dependency on the multi-ticket or RAG designs. `ticket-gh-init` is a self-contained bootstrap step that runs before any other `ticket-*` skill on a new GH-backed project.

## Adjacent

- [multi-ticket.md](multi-ticket.md) — defines the workflow that consumes the state labels this skill creates.
- [project-conf-toml.md](project-conf-toml.md) — the config format this skill writes.
