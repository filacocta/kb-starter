#!/usr/bin/env bash
# sync-from-live.sh — export live kb-bot changes to this kb-starter package
#
# Run this after modifying skills, plugin source, or KB meta files in the live setup.
# Review the diff and commit manually.
#
# Usage: bash scripts/sync-from-live.sh [--dry-run]

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_SRC="/mnt/c/Users/markr/Documents/KB/journal/.obsidian/plugins/kb-bot"
KB_SRC="/mnt/c/Users/markr/Documents/KB/journal/knowledgebase"
SKILLS_SRC="/home/markr/.claude/skills"

DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "DRY RUN — no files will be written"
fi

copy() {
  local src="$1" dst="$2"
  if $DRY_RUN; then
    echo "  [dry] cp $src → $dst"
  else
    cp "$src" "$dst"
  fi
}

echo "=== Syncing plugin source ==="
for f in "$PLUGIN_SRC"/src/*.ts; do
  copy "$f" "$REPO/plugin/src/$(basename "$f")"
done
for f in "$PLUGIN_SRC"/tests/__mocks__/*.ts; do
  copy "$f" "$REPO/plugin/tests/__mocks__/$(basename "$f")"
done
for f in "$PLUGIN_SRC"/tests/*.ts "$PLUGIN_SRC"/tests/*.mjs; do
  [[ -f "$f" ]] && copy "$f" "$REPO/plugin/tests/$(basename "$f")"
done
copy "$PLUGIN_SRC/main.ts"              "$REPO/plugin/main.ts"
copy "$PLUGIN_SRC/package.json"         "$REPO/plugin/package.json"
copy "$PLUGIN_SRC/tsconfig.json"        "$REPO/plugin/tsconfig.json"
copy "$PLUGIN_SRC/tsconfig.test.json"   "$REPO/plugin/tsconfig.test.json"
copy "$PLUGIN_SRC/manifest.json"        "$REPO/plugin/manifest.json"
copy "$PLUGIN_SRC/versions.json"        "$REPO/plugin/versions.json"
copy "$PLUGIN_SRC/version-bump.mjs"     "$REPO/plugin/version-bump.mjs"
copy "$PLUGIN_SRC/esbuild.config.mjs"   "$REPO/plugin/esbuild.config.mjs"
copy "$PLUGIN_SRC/eslint.config.mjs"    "$REPO/plugin/eslint.config.mjs"
echo "  Plugin source synced (no path patching needed)"

echo ""
echo "=== Syncing KB meta files ==="

# _schema.md: verbatim copy (no user-specific content)
copy "$KB_SRC/_schema.md" "$REPO/knowledgebase/_schema.md"
echo "  _schema.md: verbatim"

# _rules.md: patch Rule 19 example (duckmage → generic)
if $DRY_RUN; then
  echo "  [dry] _rules.md: copy + patch Rule 19 example"
else
  cp "$KB_SRC/_rules.md" "$REPO/knowledgebase/_rules.md"
  sed -i 's/\[\[RPG\/duckmage\/world\/cryptids\/Mothman\]\]/[[YourVault\/your\/folder\/YourNote]]/g' \
      "$REPO/knowledgebase/_rules.md"
  sed -i 's/\*\*Allowed:\*\* `\[\[RPG\/duckmage\/world\/cryptids\/Mothman\]\]`/\*\*Allowed:\*\* `[[YourVault\/your\/folder\/YourNote]]`/g' \
      "$REPO/knowledgebase/_rules.md"
  echo "  _rules.md: copied + Rule 19 example patched"
fi

echo ""
echo "=== Syncing skills ==="

OLD_KB_LOCATION='## KB Location

KB root: `/mnt/c/Users/markr/Documents/KB/journal/knowledgebase/`
Vault name for obsidian:// links: `journal`'

NEW_CONFIG_BLOCK='## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links).'

for skill in kb-ask kb-ingest kb-queue kb-synthesise kb-lint kb-auto; do
  SRC="$SKILLS_SRC/$skill/SKILL.md"
  DST="$REPO/skills/$skill/SKILL.md"

  if [[ ! -f "$SRC" ]]; then
    echo "  WARN: $SRC not found — skipping"
    continue
  fi

  if $DRY_RUN; then
    echo "  [dry] $skill: copy + patch KB Location → config file block"
    continue
  fi

  cp "$SRC" "$DST"

  # Replace KB Location block with config file block
  python3 - "$DST" << 'PYEOF'
import sys

path = sys.argv[1]
with open(path, 'r') as f:
    content = f.read()

old = """## KB Location

KB root: `/mnt/c/Users/markr/Documents/KB/journal/knowledgebase/`
Vault name for obsidian:// links: `journal`"""

new = """## Configuration

At startup, read `~/.claude/kb-config.md` to get your KB location:

```
KB_ROOT: /path/to/your/vault/knowledgebase
VAULT_NAME: your-vault-name
```

If `~/.claude/kb-config.md` does not exist, stop and tell the user:
> "`~/.claude/kb-config.md` not found. Create it with `KB_ROOT` and `VAULT_NAME` — see the kb-starter README for instructions."

Use the `KB_ROOT` value wherever a KB root path is needed, and `VAULT_NAME` wherever the vault name is needed (e.g. in `obsidian://open?vault=<VAULT_NAME>&file=...` links)."""

if old in content:
    content = content.replace(old, new)
else:
    print(f"  WARN: KB Location block not found in {path} — check for format drift")

# Also scrub any remaining hardcoded path/vault references in the body
content = content.replace(
    '`/mnt/c/Users/markr/Documents/KB/journal/knowledgebase/`',
    '`<KB_ROOT>`'
)
content = content.replace(
    '/mnt/c/Users/markr/Documents/KB/journal/knowledgebase/',
    '<KB_ROOT>/'
)
content = content.replace('vault=journal', 'vault=<VAULT_NAME>')

with open(path, 'w') as f:
    f.write(content)
PYEOF

  echo "  $skill: synced + patched"
done

echo ""
echo "=== Syncing obsidian topic ==="
OBSIDIAN_SRC="$KB_SRC/topics/obsidian"
OBSIDIAN_DST="$REPO/knowledgebase/topics/obsidian"

if $DRY_RUN; then
  echo "  [dry] rsync obsidian topic (excluding _Index_of_*.md)"
else
  rsync -av --delete \
    --exclude '_Index_of_*.md' \
    "$OBSIDIAN_SRC/" "$OBSIDIAN_DST/"
  echo "  Obsidian topic synced"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  cd $REPO"
echo "  git diff --stat          # review what changed"
echo "  git add -A && git commit -m 'Sync from live: <summary of what changed>'"
