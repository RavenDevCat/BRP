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

AM_ON_CALENDAR="${BRP_SCHEDULED_AUDIT_AM_ON_CALENDAR:-Mon..Fri 06:00:00 Asia/Shanghai}"
PM_ON_CALENDAR="${BRP_SCHEDULED_AUDIT_PM_ON_CALENDAR:-Mon..Fri 15:40:00 Asia/Shanghai}"
SYSTEMD_DIR="${BRP_SCHEDULED_AUDIT_SYSTEMD_DIR:-/etc/systemd/system}"

write_service() {
  local name="$1"
  local description="$2"
  local path="$SYSTEMD_DIR/$name.service"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP scheduled audit queue release ($description)

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=$ROOT_DIR/ops/scripts/run_scheduled_audit_release.sh
KillMode=process
EOF
}

write_timer() {
  local name="$1"
  local calendar="$2"
  local path="$SYSTEMD_DIR/$name.timer"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP scheduled audit queue release timer ($name)

[Timer]
OnCalendar=$calendar
Persistent=false
Unit=$name.service

[Install]
WantedBy=timers.target
EOF
}

write_service "brp-scheduled-audit-am" "06:00 AM release"
write_timer "brp-scheduled-audit-am" "$AM_ON_CALENDAR"
write_service "brp-scheduled-audit-pm" "15:40 PM release"
write_timer "brp-scheduled-audit-pm" "$PM_ON_CALENDAR"

sudo systemctl daemon-reload
echo "Installed scheduled audit timers:"
echo "  brp-scheduled-audit-am.timer -> $AM_ON_CALENDAR"
echo "  brp-scheduled-audit-pm.timer -> $PM_ON_CALENDAR"
