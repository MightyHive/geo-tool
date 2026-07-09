#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${TRENDS_PYTHON:-python3}"
[[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]] && PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
exec "$PYTHON" "$SCRIPT_DIR/resort_hierarchy.py" "$@"
