#!/usr/bin/env bash
# Sort unnamed multiTimeline CSVs by reading the query term from each file.
#
#   mkdir -p inbox/unsorted && cp ~/Downloads/multiTimeline*.csv inbox/unsorted/
#   ./run_sort_downloads.sh
#   ./run_sort_downloads.sh --from ~/Downloads --move
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  EXTRA_ARGS+=("$1")
  shift
done

PYTHON="${TRENDS_PYTHON:-python3}"
if [[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
fi

if [[ -f "$SCRIPT_DIR/shared.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/shared.env"
  set +a
fi

exec "$PYTHON" "$SCRIPT_DIR/sort_downloads.py" "${EXTRA_ARGS[@]}"
