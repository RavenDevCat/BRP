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

AM_ON_CALENDAR="${BRP_LIVE_TRAFFIC_AM_ON_CALENDAR:-Mon..Fri 08:00:00}"
PM_ON_CALENDAR="${BRP_LIVE_TRAFFIC_PM_ON_CALENDAR:-Mon..Fri 15:30:00}"
SYSTEMD_DIR="${BRP_LIVE_TRAFFIC_SYSTEMD_DIR:-/etc/systemd/system}"

write_service() {
  local name="$1"
  local period="$2"
  local path="$SYSTEMD_DIR/$name.service"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP live traffic sampler ($period)

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=$ROOT_DIR/ops/scripts/run_live_traffic_sampler.sh $period
EOF
}

write_timer() {
  local name="$1"
  local calendar="$2"
  local path="$SYSTEMD_DIR/$name.timer"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP live traffic sampler timer ($name)

[Timer]
OnCalendar=$calendar
Persistent=false
Unit=$name.service

[Install]
WantedBy=timers.target
EOF
}

write_service "brp-live-traffic-am" "am_peak"
write_timer "brp-live-traffic-am" "$AM_ON_CALENDAR"
write_service "brp-live-traffic-pm" "pm_peak"
write_timer "brp-live-traffic-pm" "$PM_ON_CALENDAR"

sudo systemctl daemon-reload
echo "Installed timers, not enabled:"
echo "  brp-live-traffic-am.timer -> $AM_ON_CALENDAR"
echo "  brp-live-traffic-pm.timer -> $PM_ON_CALENDAR"
echo "Enable after confirming times:"
echo "  sudo systemctl enable --now brp-live-traffic-am.timer brp-live-traffic-pm.timer"
