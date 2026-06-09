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

DEFAULT_BRP_PYTHON="/opt/brp/staging/venv/bin/python"
if [ -z "${BACKEND_PYTHON:-}" ] && [ -x "$DEFAULT_BRP_PYTHON" ]; then
  BACKEND_PYTHON="$DEFAULT_BRP_PYTHON"
else
  BACKEND_PYTHON="${BACKEND_PYTHON:-python3}"
fi

period="${1:-}"
case "$period" in
  am_peak)
    job_id="${BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID:-}"
    ;;
  pm_peak)
    job_id="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_JOB_ID:-}"
    ;;
  off_peak)
    job_id="${BRP_LIVE_TRAFFIC_OFF_PEAK_JOB_ID:-${BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID:-}}"
    ;;
  *)
    echo "Usage: $0 {am_peak|pm_peak|off_peak} [extra sampler args...]" >&2
    exit 2
    ;;
esac

if [ -z "$job_id" ]; then
  echo "Missing job id for period $period. Set BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID or BRP_LIVE_TRAFFIC_FROM_SCHOOL_JOB_ID." >&2
  exit 2
fi

shift || true
cd "$ROOT_DIR/apps/backend"
exec "$BACKEND_PYTHON" live_traffic_sampler.py --job-id "$job_id" --period "$period" "$@"
