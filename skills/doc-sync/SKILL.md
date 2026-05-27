---
description: Mirror the design/ directory to the project's ticket-system documentation store (GitHub wiki, Linear Docs). One-way push; design/ files unchanged; orphan pages pruned. Reads .project-conf.toml for the backend. Use /ticket-plugin:doc-sync.
disable-model-invocation: true
---

# /ticket-plugin:doc-sync

Mirror `design/*.md` to the project's documentation store. One-way push — `design/` is the source of truth; the doc-store copy is overwritten on each sync.

> **Note for Claude agents:** Do NOT invoke this skill in the same tool-use turn as `Edit` or `Write` operations targeting files under `design/`. The sync reads each source file at one moment while concurrent edits modify them; the pushed content will be a mid-edit snapshot rather than the intended final state. (This was observed in development: a parallel sync + edit batch produced a wiki with a stale page.) Finish all `design/` edits in one turn, then run the sync as a separate, subsequent turn.

## Project scope

Read `.project-conf.toml` from cwd. Extract:

- `system` → `$SYSTEM` ∈ {`linear`, `jira`, `github`}
- `key`    → `$KEY`

If `.project-conf.toml` is missing: stop with `"No .project-conf.toml in cwd. Run /ticket-plugin:gh-init or create the file manually."`

## Arguments

None. Operates on the current `design/` directory.

## Pre-flight

- Verify `design/` exists in cwd. If not, stop with `"No design/ directory found in cwd."`
- **Dirty-design check (informational).** Run `git status --porcelain -- design/`. If output is non-empty:

  ```
  Note: design/ has uncommitted changes.
  The sync will push your current working-tree state, not the last committed
  state. If you intended to push the committed version, stash or commit first.
  ```

  Do not block — continue with the sync. This guards against the "I just edited a doc, did I mean to push this version?" surprise.

- Per-system pre-flight (below).

## Frontmatter parsing (all backends)

For each `design/*.md` source file:

- If the file begins with `---` followed by YAML and a closing `---`, parse that block.
- Extract optional `title` and `slug` fields.
- Defaults if absent: `title = <filename without .md>`, `slug = <filename without .md, lowercased>`.
- Strip the frontmatter block from the body before pushing — the doc-store copy contains body only.

Files inside `design/` that aren't `.md` (subdirectories, images, etc.) are skipped.

## system = "github"

1. **Pre-flight:** `gh auth status` must succeed. If not, stop with auth instructions.

2. **Clone the wiki repo. If the wiki has not been initialized upstream, stop with instructions.**

   GitHub requires the first wiki page to be created via the web UI before `git push` to the wiki repo will work. A fresh wiki (feature enabled but no pages) returns "Repository not found" on clone — detect this and stop with a clear message instead of attempting init+push (which also fails).

   ```bash
   TMP=$(mktemp -d)
   if ! git clone git@github.com:$KEY.wiki.git $TMP 2>/dev/null; then
       rm -rf $TMP
       cat <<EOF
The wiki for $KEY has not been initialized yet.
GitHub requires the first wiki page to be created via the web UI
before git push will accept new content — this is a GitHub-specific
quirk, not something the skill can work around.

To unblock:
  1. Visit https://github.com/$KEY/wiki
  2. Click "Create the first page" and save anything — the content
     does not matter; it will be overwritten on the next sync.
  3. Re-run /ticket-plugin:doc-sync.
EOF
       exit 0
   fi
   ```

3. **For each `design/*.md` source file** (excluding subdirectories):

   - Parse frontmatter → `$TITLE`, `$SLUG`.
   - Strip frontmatter from the body.
   - Write the body to `$TMP/$SLUG.md`.

4. **Orphan prune:** for each `*.md` in `$TMP/` that doesn't correspond to a current `design/` source slug:

   ```bash
   rm $TMP/$ORPHAN.md
   ```

5. **Commit and push:**

   ```bash
   cd $TMP
   git add -A
   if git diff --cached --quiet; then
       echo "No changes to push."
   else
       SHA=$(cd $ORIG_CWD && git rev-parse HEAD)
       git commit -m "doc-sync from $SHA"
       git push origin master
   fi
   ```

6. **Cleanup:** `rm -rf $TMP`.

7. **Confirm:** `"Synced N design docs to $KEY wiki."`

## system = "linear"

1. **Pre-flight:** verify the Linear MCP is reachable. If not, stop with `"Linear MCP not available."`

2. **List existing upstream docs** via `mcp__linear-server__list_documents` scoped to the team / project for `$KEY`.

3. **For each `design/*.md`** (excluding subdirectories):

   - Parse frontmatter → `$TITLE`.
   - Strip frontmatter from the body.
   - Look for an existing upstream doc with matching `$TITLE`.
   - If found: call `mcp__linear-server__save_document` with the existing doc's ID and the new body.
   - If not: call `mcp__linear-server__save_document` to create.

4. **Orphan prune:** for each upstream doc whose title doesn't match any current `design/` source title: delete it via the Linear MCP.

5. **Confirm:** `"Synced N design docs to Linear ($KEY)."`

## system = "jira"

Stop with `"Confluence sync not yet supported."`

## Rules

- **One-way only.** Never read from the doc store back into `design/`.
- **Committed `design/` files are never modified.** Only the temp clone (GH) or upstream docs (Linear) change.
- **Frontmatter is stripped** before push. The doc-store copy contains body only.
- **Orphan pruning is mandatory.** Pages without a `design/` counterpart get deleted; the doc store always mirrors the current `design/`.
- **Skip non-`.md` files** in `design/` (subdirectories, images, anything else).
- **No subdirectory recursion** in first cut. Only top-level `design/*.md`.
- **Idempotent.** Re-running with no source changes produces no commit (GH) or no upstream writes (Linear).
- **Partial state on failure is acceptable.** Re-running after a fix completes the sync.
