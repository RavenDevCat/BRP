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
POLL_ON_CALENDAR="${BRP_SCHEDULED_JOB_POLL_ON_CALENDAR:-*-*-* *:*:00}"
SYSTEMD_DIR="${BRP_SCHEDULED_AUDIT_SYSTEMD_DIR:-/etc/systemd/system}"

write_service() {
  local name="$1"
  local description="$2"
  local path="$SYSTEMD_DIR/$name.service"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP scheduled job queue release ($description)

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
  local persistent="${3:-false}"
  local path="$SYSTEMD_DIR/$name.timer"
  sudo tee "$path" >/dev/null <<EOF
[Unit]
Description=BRP scheduled job queue release timer ($name)

[Timer]
OnCalendar=$calendar
Persistent=$persistent
AccuracySec=5s
RandomizedDelaySec=0
Unit=$name.service

[Install]
WantedBy=timers.target
EOF
}

write_service "brp-scheduled-audit-am" "06:00 AM release"
write_timer "brp-scheduled-audit-am" "$AM_ON_CALENDAR"
write_service "brp-scheduled-audit-pm" "15:40 PM release"
write_timer "brp-scheduled-audit-pm" "$PM_ON_CALENDAR"
write_service "brp-scheduled-jobs" "general due-job poll"
write_timer "brp-scheduled-jobs" "$POLL_ON_CALENDAR" "true"

sudo systemctl daemon-reload
sudo systemctl enable --now brp-scheduled-jobs.timer
echo "Installed scheduled job timers:"
echo "  brp-scheduled-audit-am.timer -> $AM_ON_CALENDAR"
echo "  brp-scheduled-audit-pm.timer -> $PM_ON_CALENDAR"
echo "  brp-scheduled-jobs.timer -> $POLL_ON_CALENDAR"
