#!/usr/bin/env bash
# Batch weekly Trends manual runs (fixed 2022-06-01 → 2026-05-31).
#
#   ./run_batch_terms.sh --print-checklists
#   … download CSVs into inbox/<term_slug>/ each …
#   ./run_batch_terms.sh --organize
#   ./run_batch_terms.sh --finalize
#
# Single term:
#   ./run_batch_terms.sh --term "good food" --organize
#   ./run_batch_terms.sh --term "good food" --finalize --skip-bq
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ENV="$SCRIPT_DIR/shared.env"
TERMS_FILE="$SCRIPT_DIR/terms.txt"
PYTHON="${TRENDS_PYTHON:-python3}"
[[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]] && PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
ACTION=""
SINGLE_TERM=""
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-checklists|--organize|--finalize)
      ACTION="$1"
      shift
      ;;
    --term)
      SINGLE_TERM="$2"
      shift 2
      ;;
    --dry-run|--skip-bq|--move)
      EXTRA+=("$1")
      shift
      ;;
    --help|-h)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  echo "Pass --print-checklists, --organize, or --finalize" >&2
  exit 1
fi

if [[ ! -f "$SHARED_ENV" ]]; then
  echo "Missing $SHARED_ENV" >&2
  exit 1
fi

_term_slug() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_//;s/_$//'
}

read_terms() {
  if [[ -n "$SINGLE_TERM" ]]; then
    printf '%s\n' "$SINGLE_TERM"
    return
  fi
  if [[ ! -f "$TERMS_FILE" ]]; then
    echo "Missing $TERMS_FILE (copy terms.example.txt)" >&2
    exit 1
  fi
  grep -v '^[[:space:]]*$' "$TERMS_FILE" | grep -v '^#'
}

run_for_term() {
  local term="$1"
  local slug data_dir inbox config
  slug="$(_term_slug "$term")"
  inbox="$SCRIPT_DIR/inbox/$slug"
  config="$SCRIPT_DIR/.batch_term.env"
  data_dir="$("$PYTHON" -c "
import sys
sys.path.insert(0, '$(pwd)')
from term_registry import data_dir_for_slug
print(data_dir_for_slug('$slug'))
")"

  mkdir -p "$inbox"

  {
    cat "$SHARED_ENV"
    printf 'QUERY_TERM="%s"\n' "$term"
    printf 'TRENDS_DATA_DIR="%s"\n' "$data_dir"
    printf 'BQ_TRENDS_WEEKLY_TABLE="emea-ds-sandbox.geo_tool.google_trends_weekly_%s"\n' "$slug"
  } > "$config"

  echo ""
  echo "════════════════════════════════════════════════════════════════"
  echo "TERM: $term"
  echo "Inbox:    $inbox/"
  echo "Data dir: $data_dir"
  echo "════════════════════════════════════════════════════════════════"

  case "$ACTION" in
    --print-checklists)
      ./run_manual_trends.sh --config "$config" --print-checklist
      ;;
    --organize)
      ./run_organize.sh --config "$config" --from "$inbox" "${EXTRA[@]}"
      ;;
    --finalize)
      ./run_manual_trends.sh --config "$config" "${EXTRA[@]}"
      ;;
  esac
}

fail=0
while IFS= read -r term || [[ -n "$term" ]]; do
  term="${term#"${term%%[![:space:]]*}"}"
  term="${term%"${term##*[![:space:]]}"}"
  [[ -z "$term" ]] && continue
  if ! run_for_term "$term"; then
    fail=1
  fi
done < <(read_terms)

exit "$fail"
