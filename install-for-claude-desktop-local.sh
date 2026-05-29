#!/usr/bin/env bash
#
# install-for-claude-desktop-local.sh
#
# Local-source variant of install-for-claude-desktop.sh.
#
# Installs from the working copy this script lives in, NOT from GitHub —
# so you can test uncommitted changes on a feature branch in Claude Desktop
# before opening a PR. Otherwise identical: same destination, same frontmatter
# stripping, same /slopstop:<name> -> /slopstop-<name> rewrites.
#
# Run from anywhere; the script resolves its own location:
#
#     bash install-for-claude-desktop-local.sh
#
# For release installs (pinned to a tag or master on GitHub), use the
# non-"-local" sibling script instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/commands"
SKILLS=(start plan pause update document archive pr merge doc-sync)

# Report what we're installing so it's obvious when testing branches.
if git -C "$SCRIPT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  branch=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD)
  sha=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)
  dirty=""
  if ! git -C "$SCRIPT_DIR" diff --quiet || ! git -C "$SCRIPT_DIR" diff --cached --quiet; then
    dirty=" (working tree has uncommitted changes)"
  fi
  echo "Installing slopstop commands from local source: $SCRIPT_DIR"
  echo "  branch=$branch sha=$sha$dirty"
else
  echo "Installing slopstop commands from local source: $SCRIPT_DIR"
fi

mkdir -p "$DEST"

# Build sed args dynamically from SKILLS so adding a new skill only requires
# updating one list.
SED_ARGS=()
for skill in "${SKILLS[@]}"; do
  SED_ARGS+=(-e "s|/slopstop:$skill|/slopstop-$skill|g")
done

for skill in "${SKILLS[@]}"; do
  src="$SCRIPT_DIR/skills/$skill/SKILL.md"
  dst="$DEST/slopstop-$skill.md"
  if [ ! -f "$src" ]; then
    echo "  /slopstop-$skill — MISSING source at $src; skipping" >&2
    continue
  fi
  echo "  /slopstop-$skill"
  awk 'BEGIN { in_fm=0 }
       NR==1 && /^---$/ { in_fm=1; next }
       in_fm && /^---$/ { in_fm=0; next }
       in_fm { next }
       { print }' "$src" \
    | sed "${SED_ARGS[@]}" \
    > "$dst"
done

cat <<EOF

Installed ${#SKILLS[@]} commands to $DEST.

Restart Claude Desktop if the commands don't appear in autocomplete.

To revert to the released version from GitHub, run the sibling script:
  bash $SCRIPT_DIR/install-for-claude-desktop.sh

To uninstall entirely:
  rm $DEST/slopstop-{$(IFS=,; echo "${SKILLS[*]}")}.md
EOF
