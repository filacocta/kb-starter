#!/usr/bin/env bash
# install.sh — install KB skills and create config template
set -e

SKILLS_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

echo "Installing KB skills to $CLAUDE_DIR/skills/"

for skill in kb-ask kb-ingest kb-queue kb-synthesise kb-lint kb-auto; do
  mkdir -p "$CLAUDE_DIR/skills/$skill"
  cp "$SKILLS_DIR/$skill/SKILL.md" "$CLAUDE_DIR/skills/$skill/SKILL.md"
  echo "  ✓ $skill"
done

CONFIG_FILE="$CLAUDE_DIR/kb-config.md"

if [ ! -f "$CONFIG_FILE" ]; then
  cat > "$CONFIG_FILE" << 'EOF'
# KB Config
# Edit these two values to match your setup, then save.
# All KB skills read this file at startup.

KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
EOF
  echo ""
  echo "Created $CONFIG_FILE"
  echo "→ Edit it with your KB root path and Obsidian vault name before running any skill."
else
  echo ""
  echo "$CONFIG_FILE already exists — not overwritten."
  echo "  Current contents:"
  grep -v "^#" "$CONFIG_FILE" | grep -v "^$" | sed 's/^/  /'
fi

echo ""
echo "Done. Next steps:"
echo "  1. Edit $CONFIG_FILE with your actual KB_ROOT and VAULT_NAME"
echo "  2. Copy knowledgebase/ into your Obsidian vault root"
echo "  3. Run /kb-ask in Claude Code to verify"
