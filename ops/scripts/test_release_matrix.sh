#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
py=${BRP_TEST_PYTHON:-python3}
base=$(mktemp -d "${TMPDIR:-/tmp}/brp-test-matrix.XXXXXXXX")
echo "Acceptance evidence: $base"
test -x apps/web/node_modules/.bin/tsc
for mode in 1 0; do
    q="$base/mode-$mode"
    mkdir "$q"
    export BRP_JOB_QUEUE_SCOPE=staging BRP_RUNTIME_DB_PATH="$q/runtime.sqlite" BRP_QUOTA_DB_PATH="$q/quota.sqlite"
    export BRP_BACKEND_CACHE_DIR="$q/backend" BRP_CLIENT_CACHE_DIR="$q/client" BRP_SIDE_TOOLS_DIR="$q/side"
    export BRP_BACKEND_JOBS_DIR="$q/jobs" BRP_AMAP_DISPLAY_GEOMETRY_CACHE_PATH="$q/geometry.json"
    export PYTHONPATH="$PWD/tests/offline_guard${PYTHONPATH:+:$PYTHONPATH}"
    export BRP_KEYED_CACHE=$mode BRP_DELTA_CHECKPOINTS=$mode BRP_DEFER_FLEET_EXPORTS=$mode
    "$py" -m pytest -q tests --tb=short --junitxml="$q/tests.xml" | tee "$q/tests.log"
done
