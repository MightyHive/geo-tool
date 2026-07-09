#!/usr/bin/env bash
# Organize inbox CSV → data/<term>_20220601/reference_weekly.csv
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

CONFIG="${CONFIG:-${MANUAL_RUN_CONFIG:-$SCRIPT_DIR/shared.env}}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Config not found: $CONFIG" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG"
set +a

export MANUAL_RUN_CONFIG="$CONFIG"

PYTHON="${TRENDS_PYTHON:-python3}"
if [[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
fi

_term_slug() {
  echo "${QUERY_TERM:-run}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_//;s/_$//'
}

if [[ -z "${TRENDS_DATA_DIR:-}" ]]; then
  slug="$(_term_slug)"
  start_tag="$(echo "${START_DATE:-20220601}" | tr -d '-')"
  TRENDS_DATA_DIR="$("$PYTHON" -c "
import sys
sys.path.insert(0, '$(pwd)')
from term_registry import data_dir_for_slug
print(data_dir_for_slug('$slug'))
")"
  export TRENDS_DATA_DIR
fi

export TERM_SLUG="$(_term_slug)"

exec "$PYTHON" "$SCRIPT_DIR/organize_inbox.py" "${EXTRA_ARGS[@]}"
