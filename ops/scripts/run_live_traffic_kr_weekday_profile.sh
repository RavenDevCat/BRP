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

export TZ="${BRP_LIVE_TRAFFIC_KR_TIMEZONE:-Asia/Seoul}"

period="${1:-both}"
case "$period" in
  am_peak|pm_peak|off_peak|both)
    ;;
  *)
    echo "Usage: $0 {am_peak|pm_peak|off_peak|both} [extra sampler args...]" >&2
    exit 2
    ;;
esac
shift || true

week_start="${BRP_LIVE_TRAFFIC_KR_PROFILE_WEEK_START:-}"
if [ -z "$week_start" ]; then
  day_of_week="$(date +%u)"
  days_until_monday="$(( (8 - day_of_week) % 7 ))"
  week_start="$(date -d "today + ${days_until_monday} days" +%F)"
fi

run_sample() {
  local sample_period="$1"
  local sample_date="$2"
  "$ROOT_DIR/ops/scripts/run_live_traffic_kr_sampler.sh" "$sample_period" --sample-date "$sample_date" "$@"
}

for offset in 0 1 2 3 4; do
  sample_date="$(date -d "$week_start + ${offset} days" +%F)"
  case "$period" in
    both)
      run_sample am_peak "$sample_date" "$@"
      run_sample pm_peak "$sample_date" "$@"
      ;;
    *)
      run_sample "$period" "$sample_date" "$@"
      ;;
  esac
done
