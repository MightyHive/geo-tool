#!/usr/bin/env bash
# Weekly Google Trends manual download → normalize → optional BigQuery.
#
#   ./run_manual_trends.sh --config config.env --print-checklist
#   … download one weekly CSV into data/<term>_20220601/reference_weekly.csv …
#   ./run_manual_trends.sh --config config.env
#
# Options:
#   --print-checklist   print URL + save path (no finalize)
#   --skip-bq           write trends_weekly.csv only
#   --data-dir PATH     override TRENDS_DATA_DIR
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

_resolve_data_dir() {
  if [[ -n "${TRENDS_DATA_DIR:-}" ]]; then
    echo "$TRENDS_DATA_DIR"
    return
  fi
  slug="$(_term_slug)"
  "$PYTHON" -c "
import os, sys
sys.path.insert(0, '$(pwd)')
from term_registry import data_dir_for_slug
print(data_dir_for_slug('$slug'))
"
}

if [[ -z "${TRENDS_DATA_DIR:-}" ]]; then
  export TRENDS_DATA_DIR="$(_resolve_data_dir)"
fi

export TERM_SLUG="$(_term_slug)"
export RUN_ID="${RUN_ID:-weekly_$(date -u +%Y-%m-%dT%H-%M-%SZ)}"

echo "Config:          $CONFIG"
echo "TRENDS_DATA_DIR: $TRENDS_DATA_DIR"
echo "QUERY_TERM:      ${QUERY_TERM:-<unset>}"
echo "Date range:      ${START_DATE:-2022-06-01} → ${END_DATE:-2026-05-31}"
echo ""

if ((${#EXTRA_ARGS[@]} > 0)); then
  exec "$PYTHON" "$SCRIPT_DIR/finalize_weekly.py" "${EXTRA_ARGS[@]}"
else
  exec "$PYTHON" "$SCRIPT_DIR/finalize_weekly.py"
fi
