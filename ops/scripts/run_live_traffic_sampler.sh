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
  am_peak)
    job_id="${BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID:-}"
    source="${BRP_LIVE_TRAFFIC_TO_SCHOOL_SOURCE:-route_audit_job}"
    run_id="${BRP_LIVE_TRAFFIC_TO_SCHOOL_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_TO_SCHOOL_BASELINE_PATH:-}"
    market="${BRP_LIVE_TRAFFIC_TO_SCHOOL_MARKET:-CN}"
    city="${BRP_LIVE_TRAFFIC_TO_SCHOOL_CITY:-Shanghai}"
    timing_args=(--target-arrival-local-time "${BRP_LIVE_TRAFFIC_AM_TARGET_ARRIVAL_LOCAL_TIME:-08:00}")
    if [ -n "${BRP_LIVE_TRAFFIC_AM_ROUTE_START_TIMES_PATH:-}" ]; then
      timing_args+=(--route-start-times-path "$BRP_LIVE_TRAFFIC_AM_ROUTE_START_TIMES_PATH")
    fi
    ;;
  pm_peak)
    job_id="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_JOB_ID:-}"
    source="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_SOURCE:-route_audit_job}"
    run_id="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_BASELINE_PATH:-}"
    market="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_MARKET:-CN}"
    city="${BRP_LIVE_TRAFFIC_FROM_SCHOOL_CITY:-Shanghai}"
    timing_args=(--departure-local-time "${BRP_LIVE_TRAFFIC_PM_DEPARTURE_LOCAL_TIME:-15:40}")
    ;;
  off_peak)
    job_id="${BRP_LIVE_TRAFFIC_OFF_PEAK_JOB_ID:-${BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID:-}}"
    source="${BRP_LIVE_TRAFFIC_OFF_PEAK_SOURCE:-route_audit_job}"
    run_id="${BRP_LIVE_TRAFFIC_OFF_PEAK_RUN_ID:-}"
    baseline_path="${BRP_LIVE_TRAFFIC_OFF_PEAK_BASELINE_PATH:-}"
    market="${BRP_LIVE_TRAFFIC_OFF_PEAK_MARKET:-CN}"
    city="${BRP_LIVE_TRAFFIC_OFF_PEAK_CITY:-Shanghai}"
    timing_args=()
    ;;
  *)
    echo "Usage: $0 {am_peak|pm_peak|off_peak} [extra sampler args...]" >&2
    exit 2
    ;;
esac

if [ -z "$job_id" ]; then
  if [ "$source" = "fleet_planner" ] && [ -n "$run_id" ]; then
    :
  elif [ "$source" = "baseline_json" ] && [ -n "$baseline_path" ]; then
    :
  else
    echo "Missing source identifier for period $period. Set the matching job id, fleet planner run id, or baseline path." >&2
    exit 2
  fi
fi

shift || true
if [ "${BRP_LIVE_TRAFFIC_PREFLIGHT_ENABLED:-1}" != "0" ] && [ "${BRP_LIVE_TRAFFIC_PREFLIGHT_ENABLED:-1}" != "false" ]; then
  preflight_profile_prefix="${BRP_LIVE_TRAFFIC_PREFLIGHT_PROFILE_PREFIX:-shanghai}"
  city_profile="${preflight_profile_prefix}_${period}"
  preflight_args=(--profile "$city_profile" --require-under-cap --require-baseline-fast-path)
  if [ "$period" = "off_peak" ]; then
    preflight_args=(--include-off-peak "${preflight_args[@]}")
  fi
  "$BACKEND_PYTHON" "$ROOT_DIR/ops/scripts/report_live_traffic_budget.py" "${preflight_args[@]}"
fi
cd "$ROOT_DIR/apps/backend"
source_args=(--source "$source" --period "$period" --market "$market" --city "$city")
if [ "$source" = "fleet_planner" ]; then
  source_args+=(--run-id "$run_id")
elif [ "$source" = "baseline_json" ]; then
  source_args+=(--baseline-path "$baseline_path")
else
  source_args+=(--job-id "$job_id")
fi
exec "$BACKEND_PYTHON" live_traffic_sampler.py "${source_args[@]}" --max-api-calls-per-run "$max_api_calls_per_run" "${timing_args[@]}" "$@"
