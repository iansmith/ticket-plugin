# slopstop — repo conventions

This file is loaded by Claude Code (and Claude Desktop) when working inside this repo via the `.claude/rules/` mechanism (any `*.md` file in there gets pulled into context at session start, same way `CLAUDE.md` would at the repo root). Treat the rules below as binding for any session iterating on the plugin.

(Originally lived at `CLAUDE.md` at the repo root, but the Claude Code plugin validator warns about `CLAUDE.md` at a plugin root — it assumes that file is trying to ship context to *plugin users*, which doesn't work. Our use case is the opposite — repo conventions for *maintainers*. `.claude/rules/` is the right home for that, and avoids the false-positive warning.)

## Release checklist — MANDATORY before pushing a new version tag

1. **Validate the manifests.** Run `claude plugin validate` against both the plugin manifest and (separately) the marketplace manifest. Both must pass.

   ```bash
   ~/.local/bin/claude plugin validate ~/slopstop/.claude-plugin/plugin.json
   ```

   Or from inside the repo (`cd ~/slopstop && claude plugin validate .`) — the latter form validates both `plugin.json` and `marketplace.json` in one shot.

   Common past failure: `marketplace.json` had `"source": "."` (bare-dot path). The schema rejects that. Fixed in v1.1.2 by switching to the object form:

   ```json
   "source": {
     "source": "github",
     "repo": "iansmith/slopstop"
   }
   ```

   If you change either manifest, **re-run validate before committing**.

2. **Bump `version` in `.claude-plugin/plugin.json`.** Semver — `MAJOR.MINOR.PATCH`. Patch for fixes / metadata polish; minor for new features (e.g., a new slash command); major for breaking changes (e.g., renamed plugin, changed install command shape).

3. **Update `CHANGELOG.md`** with the new version section before tagging. Keep entries factual; explain *why* not just *what*.

4. **Never force-move tags** once they're pushed, except during the very-pre-release period before any users existed. The `v1.0.0` tag was force-moved several times during initial polish before submission; from `v1.0.0`-and-later, all tags are immutable. If a release ships broken, ship the fix as a new patch version (`v1.x.y+1`), never rewrite history.

5. **Push master, then push the tag.** Annotated tags only (`git tag -a vX.Y.Z -m "..."`).

## Plugin format reference

- Plugin manifest: `.claude-plugin/plugin.json` (schema: `https://json.schemastore.org/claude-code-plugin-manifest.json`)
- Self-distribution marketplace: `.claude-plugin/marketplace.json` — uses the `{"source": "github", "repo": "..."}` form (NOT bare-dot or `"./"` — schema rejects those)
- Skills: `skills/<name>/SKILL.md` with YAML frontmatter — `description:` required, `disable-model-invocation: true` for explicit slash commands (which all seven of ours are)
- Claude Desktop standalone install: `install-for-claude-desktop.sh` curls each `SKILL.md` from GitHub, strips frontmatter, rewrites `/slopstop:<name>` → `/slopstop-<name>`, and drops the files into `~/.claude/commands/`. Update the script's `SKILLS=( ... )` array and `sed` substitutions when adding or renaming a skill.

## Authoritative docs

- Plugins guide: https://code.claude.com/docs/en/plugins
- Plugins reference (manifest schema): https://code.claude.com/docs/en/plugins-reference
- Marketplaces reference (`source` schema): https://code.claude.com/docs/en/plugin-marketplaces
- Submission form: https://clau.de/plugin-directory-submission (alternately: claude.ai/settings/plugins/submit, platform.claude.com/plugins/submit)

## Distribution paths

| Audience | Path | Invocation |
|---|---|---|
| Claude Code (CLI) — third-party marketplace | `/plugin marketplace add iansmith/slopstop` then `/plugin install slopstop@slopstop` | `/slopstop:<name>` |
| Claude Code (CLI) — official Anthropic marketplace (pending review) | `/plugin install slopstop@claude-plugins-official` | `/slopstop:<name>` |
| Claude Desktop (no `/plugin` support yet) | `curl -fsSL https://raw.githubusercontent.com/iansmith/slopstop/<ref>/install-for-claude-desktop.sh \| bash` | `/slopstop-<name>` (un-namespaced) |

## Workflow conventions inside this repo

- All commits anchored to a ticket get `[TICKET-KEY]` prefix in the subject and `Refs: TICKET-KEY` (or `Closes:` on the final commit) trailer.
- Co-Authored-By trailer on all Claude-assisted commits: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- Never `git push --force`, `git commit --no-verify`, `gh pr merge --admin`, or `git reset --hard` — none of these have a place in this repo's flow.
- **Run the full test suite before pausing to ask about a commit.** When working in a batched plan (or any multi-step change), do NOT pause and ask "ready to commit?" with unverified code in the working tree. Run every relevant test layer first — unit tests (`pytest`) AND the Docker-level smoke tests (`verify-billN.sh`) when changes touch anything the image build sees — and surface the actual results in the consult message. The pause is for the human to approve a *known-green* state, not to ratify untested work. If a layer can't run locally (e.g. image build is broken, registry unreachable), say so explicitly in the consult — don't silently skip.

## Python+FastAPI work in `rag-service/`

The ticket-rag service ([BILL-28 umbrella](https://github.com/iansmith/slopstop/issues/28)) is a Python+FastAPI application under `rag-service/`. **All testing for that code follows [`design/rag-service-testing.md`](../../design/rag-service-testing.md)** — the authoritative contract for the testing layers (pure functions, `TestClient`, `dependency_overrides`), the code-shape rules that keep code testable, the canonical `conftest.py` fixture pattern, and the anti-patterns to avoid.

In one sentence: every external dependency (postgres, models, HTTP clients) is wired via FastAPI `Depends()` so tests can swap it via `app.dependency_overrides` — no monkey-patching globals, no spinning up postgres for unit tests, no loading real model weights in pytest. Docker-level end-to-end tests (`verify-billN.sh`) stay as the integration gate.

Anyone adding or modifying code in `rag-service/` should read that doc before opening a PR.
