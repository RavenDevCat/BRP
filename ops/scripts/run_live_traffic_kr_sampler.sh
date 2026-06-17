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

max_api_calls_per_run="${BRP_LIVE_TRAFFIC_MAX_API_CALLS_PER_RUN:-1000}"
period="${1:-}"
case "$period" in
  am_peak|pm_peak|off_peak)
    ;;
  *)
    echo "Usage: $0 {am_peak|pm_peak|off_peak} [extra sampler args...]" >&2
    exit 2
    ;;
esac
shift || true

export BRP_LIVE_TRAFFIC_TIMEZONE="${BRP_LIVE_TRAFFIC_KR_TIMEZONE:-Asia/Seoul}"
export BRP_LIVE_TRAFFIC_PROVIDER="${BRP_LIVE_TRAFFIC_KR_PROVIDER:-kakao_navi}"

source="${BRP_LIVE_TRAFFIC_KR_SOURCE:-baseline_json}"
market="${BRP_LIVE_TRAFFIC_KR_MARKET:-KR}"
city="${BRP_LIVE_TRAFFIC_KR_CITY:-Seoul}"
baseline_dir="${BRP_LIVE_TRAFFIC_KR_BASELINE_DIR:-${BRP_LIVE_TRAFFIC_BASELINE_DIR:-}}"

case "$period" in
  am_peak)
    job_id="${BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_JOB_ID:-}"
    run_id="${BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH:-${BRP_LIVE_TRAFFIC_TO_SCHOOL_BASELINE_PATH:-}}"
    timing_args=(--target-arrival-local-time "${BRP_LIVE_TRAFFIC_KR_AM_TARGET_ARRIVAL_LOCAL_TIME:-08:00}")
    ;;
  pm_peak)
    job_id="${BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_JOB_ID:-}"
    run_id="${BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH:-${BRP_LIVE_TRAFFIC_FROM_SCHOOL_BASELINE_PATH:-}}"
    timing_args=(--departure-local-time "${BRP_LIVE_TRAFFIC_KR_PM_DEPARTURE_LOCAL_TIME:-15:40}")
    ;;
  off_peak)
    job_id="${BRP_LIVE_TRAFFIC_KR_OFF_PEAK_JOB_ID:-${BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_JOB_ID:-}}"
    run_id="${BRP_LIVE_TRAFFIC_KR_OFF_PEAK_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_KR_OFF_PEAK_BASELINE_PATH:-${BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH:-}}"
    timing_args=(--departure-local-time "${BRP_LIVE_TRAFFIC_KR_OFF_PEAK_DEPARTURE_LOCAL_TIME:-11:00}")
    ;;
esac

if [ "$source" = "baseline_json" ] && [ -z "$baseline_path" ]; then
  echo "Missing KR baseline path for $period. Set BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH or BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH." >&2
  exit 2
fi
if [ "$source" = "fleet_planner" ] && [ -z "$run_id" ]; then
  echo "Missing KR fleet planner run id for $period." >&2
  exit 2
fi
if [ "$source" = "route_audit_job" ] && [ -z "$job_id" ]; then
  echo "Missing KR route audit job id for $period." >&2
  exit 2
fi

cd "$ROOT_DIR/apps/backend"
source_args=(--source "$source" --period "$period" --provider "$BRP_LIVE_TRAFFIC_PROVIDER" --market "$market" --city "$city")
if [ -n "$baseline_dir" ]; then
  source_args+=(--baseline-dir "$baseline_dir")
fi
if [ "$source" = "fleet_planner" ]; then
  source_args+=(--run-id "$run_id")
elif [ "$source" = "baseline_json" ]; then
  source_args+=(--baseline-path "$baseline_path")
else
  source_args+=(--job-id "$job_id")
fi

exec "$BACKEND_PYTHON" live_traffic_sampler.py "${source_args[@]}" --max-api-calls-per-run "$max_api_calls_per_run" "${timing_args[@]}" "$@"
