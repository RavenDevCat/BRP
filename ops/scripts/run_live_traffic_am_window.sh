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

WINDOW_START="${BRP_LIVE_TRAFFIC_AM_WINDOW_START:-06:10}"
WINDOW_END="${BRP_LIVE_TRAFFIC_AM_WINDOW_END:-07:40}"
INTERVAL_SECONDS="${BRP_LIVE_TRAFFIC_AM_WINDOW_INTERVAL_SECONDS:-300}"
export TZ="${BRP_LIVE_TRAFFIC_TIMEZONE:-Asia/Shanghai}"

today="$(date +%F)"
start_epoch="$(date -d "$today $WINDOW_START" +%s)"
end_epoch="$(date -d "$today $WINDOW_END" +%s)"
now_epoch="$(date +%s)"

if [ "$now_epoch" -lt "$start_epoch" ]; then
  sleep "$((start_epoch - now_epoch))"
fi

while true; do
  now_epoch="$(date +%s)"
  if [ "$now_epoch" -gt "$end_epoch" ]; then
    break
  fi
  "$ROOT_DIR/ops/scripts/run_live_traffic_sampler.sh" am_peak --sample-due-routes-only --skip-empty
  now_epoch="$(date +%s)"
  next_epoch="$((now_epoch + INTERVAL_SECONDS))"
  if [ "$next_epoch" -gt "$end_epoch" ]; then
    break
  fi
  sleep "$INTERVAL_SECONDS"
done
