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
source="${BRP_LIVE_TRAFFIC_SUZHOU_SOURCE:-baseline_json}"
run_id="${BRP_LIVE_TRAFFIC_SUZHOU_RUN_ID:-0048b194830c}"
baseline_path="${BRP_LIVE_TRAFFIC_SUZHOU_BASELINE_PATH:-suzhou/suzhou_fleet_planner_0048b194830c_current_plan.json}"
market="${BRP_LIVE_TRAFFIC_SUZHOU_MARKET:-CN}"
city="${BRP_LIVE_TRAFFIC_SUZHOU_CITY:-Suzhou}"

case "$period" in
  am_peak)
    timing_args=(--target-arrival-local-time "${BRP_LIVE_TRAFFIC_SUZHOU_AM_TARGET_ARRIVAL_LOCAL_TIME:-08:00}")
    ;;
  pm_peak)
    timing_args=(--departure-local-time "${BRP_LIVE_TRAFFIC_SUZHOU_PM_DEPARTURE_LOCAL_TIME:-15:40}")
    ;;
  off_peak)
    timing_args=()
    ;;
  *)
    echo "Usage: $0 {am_peak|pm_peak|off_peak} [extra sampler args...]" >&2
    exit 2
    ;;
esac

shift || true
if [ "${BRP_LIVE_TRAFFIC_PREFLIGHT_ENABLED:-1}" != "0" ] && [ "${BRP_LIVE_TRAFFIC_PREFLIGHT_ENABLED:-1}" != "false" ]; then
  preflight_profile_prefix="${BRP_LIVE_TRAFFIC_PREFLIGHT_PROFILE_PREFIX:-suzhou}"
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
  echo "Unsupported Suzhou live traffic source: $source" >&2
  exit 2
fi

exec "$BACKEND_PYTHON" live_traffic_sampler.py "${source_args[@]}" --max-api-calls-per-run "$max_api_calls_per_run" "${timing_args[@]}" "$@"
