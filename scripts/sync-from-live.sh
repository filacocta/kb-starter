#!/usr/bin/env bash
# Compatibility entrypoint: planning only; never recursively copy a live vault.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/plan-starter-export.py" "$@"
