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

SYSTEMD_DIR="${BRP_OSRM_CLEANUP_SYSTEMD_DIR:-/etc/systemd/system}"
RUN_USER="${BRP_OSRM_CLEANUP_USER:-$(id -un)}"
INTERVAL_SECONDS="${BRP_OSRM_CLEANUP_INTERVAL_SECONDS:-900}"
RANDOMIZED_DELAY_SECONDS="${BRP_OSRM_CLEANUP_RANDOMIZED_DELAY_SECONDS:-120}"
DEFAULT_BRP_PYTHON="/opt/brp/staging/venv/bin/python"
if [ -z "${BACKEND_PYTHON:-}" ] && [ -x "$DEFAULT_BRP_PYTHON" ]; then
  BACKEND_PYTHON="$DEFAULT_BRP_PYTHON"
else
  BACKEND_PYTHON="${BACKEND_PYTHON:-python3}"
fi

ENABLE_NOW=false
if [ "${1:-}" = "--enable-now" ]; then
  ENABLE_NOW=true
elif [ "${1:-}" != "" ]; then
  echo "Usage: $0 [--enable-now]" >&2
  exit 2
fi

sudo tee "$SYSTEMD_DIR/brp-osrm-cleanup.service" >/dev/null <<EOF
[Unit]
Description=BRP OSRM manager idle cleanup
After=docker.service

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$ROOT_DIR
ExecStart=$BACKEND_PYTHON $ROOT_DIR/ops/scripts/report_osrm_manager.py cleanup-idle
EOF

sudo tee "$SYSTEMD_DIR/brp-osrm-cleanup.timer" >/dev/null <<EOF
[Unit]
Description=BRP OSRM manager idle cleanup timer

[Timer]
OnBootSec=10min
OnUnitActiveSec=${INTERVAL_SECONDS}
AccuracySec=60s
RandomizedDelaySec=${RANDOMIZED_DELAY_SECONDS}
Persistent=false
Unit=brp-osrm-cleanup.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
echo "Installed brp-osrm-cleanup.timer every ${INTERVAL_SECONDS}s, not enabled by default."
echo "Service user: $RUN_USER"
if [ "$ENABLE_NOW" = true ]; then
  sudo systemctl enable --now brp-osrm-cleanup.timer
  echo "Enabled and started brp-osrm-cleanup.timer."
else
  echo "Enable after confirming policy:"
  echo "  sudo systemctl enable --now brp-osrm-cleanup.timer"
fi
