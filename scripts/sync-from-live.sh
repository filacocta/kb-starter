#!/usr/bin/env bash
# sync-from-live.sh — export selected live KB scaffolding into this starter repo.
#
# This helper intentionally requires explicit source paths. It is meant for
# maintainers preparing a sanitized starter update, not for automatic backup of
# a personal vault.
#
# Usage:
#   bash scripts/sync-from-live.sh --kb-root /path/to/vault/knowledgebase \
#     [--kb-feed /path/to/projects/kb-feed] \
#     [--dashboard /path/to/projects/kb-workflow-dashboard] \
#     [--dry-run]

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
KB_ROOT=""
KB_FEED=""
DASHBOARD=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kb-root)
      KB_ROOT="$2"
      shift 2
      ;;
    --kb-feed)
      KB_FEED="$2"
      shift 2
      ;;
    --dashboard)
      DASHBOARD="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$KB_ROOT" ]]; then
  echo "error: --kb-root is required" >&2
  exit 2
fi

copy_file() {
  local src="$1" dst="$2"
  if $DRY_RUN; then
    echo "[dry] cp $src -> $dst"
  else
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  fi
}

copy_tree() {
  local src="$1" dst="$2"
  if $DRY_RUN; then
    echo "[dry] sync $src/ -> $dst/"
  else
    mkdir -p "$dst"
    rsync -a --delete \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '.pytest_cache/' \
      --exclude '__pycache__/' \
      --exclude '*.egg-info/' \
      --exclude 'dist/' \
      "$src/" "$dst/"
  fi
}

echo "Syncing KB contract files"
for file in _schema.md _rules.md _schema-media.md _rules-media.md _todo.md; do
  [[ -f "$KB_ROOT/$file" ]] && copy_file "$KB_ROOT/$file" "$REPO/knowledgebase/$file"
done

echo "Syncing KB tools"
if [[ -d "$KB_ROOT/_tools" ]]; then
  copy_tree "$KB_ROOT/_tools" "$REPO/knowledgebase/_tools"
fi

if [[ -n "$KB_FEED" ]]; then
  echo "Syncing kb-feed project"
  copy_tree "$KB_FEED" "$REPO/projects/kb-feed"
fi

if [[ -n "$DASHBOARD" ]]; then
  echo "Syncing kb-workflow-dashboard project"
  copy_tree "$DASHBOARD" "$REPO/projects/kb-workflow-dashboard"
fi

echo "Done. Review with: git diff --stat && git diff"
echo "Before publishing, run a secret/path scan and avoid committing personal notes."
