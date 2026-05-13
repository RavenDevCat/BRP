#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL_OSRM_DIR="${OSRM_LOCAL_DATA_DIR:-/Users/developer/brp-osrm-data}"

if [[ ! -d "$LOCAL_OSRM_DIR" ]]; then
  echo "Local OSRM data directory not found: $LOCAL_OSRM_DIR" >&2
  echo "Expected preprocessed .osrm* files under that directory." >&2
  exit 1
fi

OSRM_DIR="$LOCAL_OSRM_DIR"

docker rm -f osrm-shanghai osrm-beijing osrm-suzhou osrm-xian osrm-south-korea 2>/dev/null || true

docker run -d --name osrm-shanghai -p 5002:5000 \
  -v "$OSRM_DIR/shanghai:/data" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 1000 /data/shanghai-latest.osrm

docker run -d --name osrm-beijing -p 5003:5000 \
  -v "$OSRM_DIR/beijing:/data" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 1000 /data/beijing-latest.osrm

docker run -d --name osrm-suzhou -p 5004:5000 \
  -v "$OSRM_DIR/suzhou:/data" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 1000 /data/jiangsu-latest.osrm

docker run -d --name osrm-xian -p 5005:5000 \
  -v "$OSRM_DIR/xian:/data" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 1000 /data/shaanxi-latest.osrm

docker run -d --name osrm-south-korea -p 5006:5000 \
  -v "$OSRM_DIR/south-korea:/data" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size 1000 /data/south-korea-latest.osrm
