#!/usr/bin/env bash
#
# install-for-claude-desktop.sh
#
# Installs ticket-plugin's commands into ~/.claude/commands/ for use in
# Claude Desktop (which doesn't yet support /plugin install). They appear
# as /ticket-start, /ticket-pause, /ticket-update, /ticket-archive (no
# plugin namespace — Claude Desktop loads them as standalone slash commands).
#
# For Claude Code (CLI) users, the proper install is:
#
#     /plugin marketplace add iansmith/ticket-plugin
#     /plugin install ticket-plugin@ticket-plugin
#
# To pin to a specific version, set TICKET_PLUGIN_REF (defaults to master):
#
#     TICKET_PLUGIN_REF=v1.0.0 bash install-for-claude-desktop.sh
#

set -euo pipefail

REPO="iansmith/ticket-plugin"
REF="${TICKET_PLUGIN_REF:-master}"
DEST="$HOME/.claude/commands"
SKILLS=(start plan pause update archive pr merge)

echo "Installing ticket-plugin commands from $REPO@$REF..."
mkdir -p "$DEST"

for skill in "${SKILLS[@]}"; do
  src="https://raw.githubusercontent.com/$REPO/$REF/skills/$skill/SKILL.md"
  dst="$DEST/ticket-$skill.md"
  echo "  /ticket-$skill"
  curl -fsSL "$src" \
    | awk 'BEGIN { in_fm=0 }
           NR==1 && /^---$/ { in_fm=1; next }
           in_fm && /^---$/ { in_fm=0; next }
           in_fm { next }
           { print }' \
    | sed -e 's|/ticket-plugin:start|/ticket-start|g' \
          -e 's|/ticket-plugin:plan|/ticket-plan|g' \
          -e 's|/ticket-plugin:pause|/ticket-pause|g' \
          -e 's|/ticket-plugin:update|/ticket-update|g' \
          -e 's|/ticket-plugin:archive|/ticket-archive|g' \
          -e 's|/ticket-plugin:pr|/ticket-pr|g' \
          -e 's|/ticket-plugin:merge|/ticket-merge|g' \
    > "$dst"
done

cat <<EOF

Installed 7 commands to $DEST:

  /ticket-start <KEY>     start or resume work on a ticket
  /ticket-plan [args]     investigate + write a parallelism-aware plan; optional agent fanout
  /ticket-pause           pause the currently active ticket
  /ticket-update          mid-session checkpoint to progress.md
  /ticket-archive         push final plan + DoD-confirmation comment + findings to a
                          ticket already moved to a Done-type state on Linear/JIRA, then
                          archive the local tracking dir
  /ticket-pr              open a PR: simplify + commit + push + CodeRabbit poll
  /ticket-merge           ship the code: merge PR + advance ticket one state. Does NOT
                          archive — the summary tells you whether to run
                          /ticket-archive now (terminal state) or wait (intermediate)

Restart Claude Desktop if the commands don't appear in autocomplete.

Don't forget to create .project-prefix in each project dir, e.g.:
  echo MAZ > .project-prefix    # Linear team prefix
  echo PLTF > .project-prefix   # JIRA project prefix

This plugin requires either the Linear or Atlassian MCP installed.
See https://github.com/$REPO#prerequisites for details.

To uninstall later:
  rm $DEST/ticket-{start,plan,pause,update,archive,pr,merge}.md
EOF
