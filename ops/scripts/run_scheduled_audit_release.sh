#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL_ENV_FILE="$ROOT_DIR/ops/env/local.env"
if [ -f "$LOCAL_ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$LOCAL_ENV_FILE"
  set +a
fi

PYTHON_BIN="${BRP_PYTHON_BIN:-${BACKEND_PYTHON:-python3}}"
cd "$ROOT_DIR"
exec "$PYTHON_BIN" "$ROOT_DIR/ops/scripts/release_scheduled_audit_jobs.py"
