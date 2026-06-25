#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
BRP live traffic sampling timers are retired on CN.

Use ops/scripts/install_scheduled_audit_timers.sh instead. The new flow queues
Route Audit jobs and releases them at the fixed AM/PM traffic windows; it does
not maintain separate AMap sampling timers.
EOF
exit 1
