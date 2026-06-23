#!/usr/bin/env bash
# Run FastAPI + Vite dev servers for the TypeScript GEO UI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export APP_ENV="${APP_ENV:-development}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Creating Python venv…"
  python3 -m venv "$ROOT/.venv"
fi

if ! "$PYTHON" -c "import fastapi, google_auth_oauthlib" 2>/dev/null; then
  echo "Installing API dependencies…"
  "$PYTHON" -m pip install -r requirements-api.txt -q
fi

if [[ ! -d web/node_modules ]]; then
  echo "Installing web dependencies…"
  (cd web && npm install)
fi

cleanup() {
  kill "$API_PID" "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT

"$PYTHON" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload --reload-exclude '.venv/*' &
API_PID=$!

(cd web && npm run dev) &
WEB_PID=$!

echo ""
echo "GEO web UI:  http://localhost:5173  (also http://127.0.0.1:5173 — pick one and stay on it)"
echo "GEO API:     http://127.0.0.1:8000/api/health"
echo "Press Ctrl+C to stop both."
wait
