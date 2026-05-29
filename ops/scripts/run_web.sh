#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"

if [[ ! -f "$WEB_DIR/package.json" ]]; then
  echo "BRP web package not found: $WEB_DIR" >&2
  exit 1
fi

cd "$WEB_DIR"
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-/api}"
npm run dev -- --host "${WEB_HOST:-127.0.0.1}" --port "${WEB_PORT:-5173}"
