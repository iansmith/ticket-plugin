#!/usr/bin/env bash
#
# install-for-claude-desktop.sh
#
# Installs slopstop's commands into ~/.claude/commands/ for use in
# Claude Desktop (which doesn't yet support /plugin install). They appear
# as /slopstop-start, /slopstop-pause, /slopstop-update, /slopstop-archive (no
# plugin namespace — Claude Desktop loads them as standalone slash commands).
#
# For Claude Code (CLI) users, the proper install is:
#
#     /plugin marketplace add iansmith/slopstop
#     /plugin install slopstop@slopstop
#
# To pin to a specific version, set SLOPSTOP_REF (defaults to master):
#
#     SLOPSTOP_REF=v1.0.0 bash install-for-claude-desktop.sh
#

set -euo pipefail

REPO="iansmith/slopstop"
REF="${SLOPSTOP_REF:-master}"
DEST="$HOME/.claude/commands"
SKILLS=(start plan pause update document archive pr merge doc-sync)

echo "Installing slopstop commands from $REPO@$REF..."
mkdir -p "$DEST"

for skill in "${SKILLS[@]}"; do
  src="https://raw.githubusercontent.com/$REPO/$REF/skills/$skill/SKILL.md"
  dst="$DEST/slopstop-$skill.md"
  echo "  /slopstop-$skill"
  curl -fsSL "$src" \
    | awk 'BEGIN { in_fm=0 }
           NR==1 && /^---$/ { in_fm=1; next }
           in_fm && /^---$/ { in_fm=0; next }
           in_fm { next }
           { print }' \
    | sed -e 's|/slopstop:start|/slopstop-start|g' \
          -e 's|/slopstop:plan|/slopstop-plan|g' \
          -e 's|/slopstop:pause|/slopstop-pause|g' \
          -e 's|/slopstop:update|/slopstop-update|g' \
          -e 's|/slopstop:document|/slopstop-document|g' \
          -e 's|/slopstop:archive|/slopstop-archive|g' \
          -e 's|/slopstop:pr|/slopstop-pr|g' \
          -e 's|/slopstop:merge|/slopstop-merge|g' \
          -e 's|/slopstop:doc-sync|/slopstop-doc-sync|g' \
    > "$dst"
done

cat <<EOF

Installed 9 commands to $DEST:

  /slopstop-start <KEY>     start or resume work on a ticket
  /slopstop-plan [args]     investigate + write a parallelism-aware plan; optional agent fanout
  /slopstop-pause           pause the currently active ticket
  /slopstop-update          mid-session checkpoint to progress.md
  /slopstop-document        push current local docs (description + DoD-confirmation comment
                          + findings) to the ticket. Idempotent; stops on divergence.
                          --force overrides; --dry-run previews
  /slopstop-archive         push final plan + DoD-confirmation comment + findings to a
                          ticket already moved to a Done-type state on Linear/JIRA, then
                          archive the local tracking dir (delegates the push to
                          /slopstop-document; stops cleanly if divergence is detected)
  /slopstop-pr              open a PR: simplify + commit + push + CodeRabbit poll
  /slopstop-merge           ship the code: merge PR + advance ticket one state. Does NOT
                          archive — the summary tells you whether to run
                          /slopstop-archive now (terminal state) or wait (intermediate)
  /slopstop-doc-sync        mirror design/ to the project's doc store (GH wiki / Linear
                          Docs). One-way push; orphan-pruning; reads .project-conf.toml

Restart Claude Desktop if the commands don't appear in autocomplete.

Don't forget to create .project-prefix in each project dir, e.g.:
  echo MAZ > .project-prefix    # Linear team prefix
  echo PLTF > .project-prefix   # JIRA project prefix

This plugin requires either the Linear or Atlassian MCP installed.
See https://github.com/$REPO#prerequisites for details.

To uninstall later:
  rm $DEST/slopstop-{start,plan,pause,update,document,archive,pr,merge,doc-sync}.md
EOF
