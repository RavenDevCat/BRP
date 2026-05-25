#!/bin/bash
set -euo pipefail

LOCAL_OSRM_DIR="${OSRM_LOCAL_DATA_DIR:-/opt/brp/osrm-data}"
OSRM_BIND_HOST="${OSRM_BIND_HOST:-127.0.0.1}"
OSRM_PORT="${OSRM_SOUTH_KOREA_PORT:-5006}"
OSRM_MAX_TABLE_SIZE="${OSRM_MAX_TABLE_SIZE:-1000}"
OSRM_DATASET_DIR="${OSRM_SOUTH_KOREA_DATASET_DIR:-$LOCAL_OSRM_DIR/south-korea}"
OSRM_DATASET_FILE="${OSRM_SOUTH_KOREA_DATASET_FILE:-south-korea-latest.osrm}"
OSRM_CONTAINER_NAME="${OSRM_SOUTH_KOREA_CONTAINER_NAME:-osrm-south-korea}"

if [[ ! -d "$OSRM_DATASET_DIR" ]]; then
  echo "South Korea OSRM dataset directory not found: $OSRM_DATASET_DIR" >&2
  echo "Expected preprocessed .osrm* files in that directory." >&2
  exit 1
fi

if [[ ! -f "$OSRM_DATASET_DIR/$OSRM_DATASET_FILE" ]]; then
  echo "South Korea OSRM dataset file not found: $OSRM_DATASET_DIR/$OSRM_DATASET_FILE" >&2
  echo "Set OSRM_SOUTH_KOREA_DATASET_FILE if your .osrm base filename is different." >&2
  exit 1
fi

docker rm -f "$OSRM_CONTAINER_NAME" 2>/dev/null || true

docker run -d --name "$OSRM_CONTAINER_NAME" \
  -p "${OSRM_BIND_HOST}:${OSRM_PORT}:5000" \
  -v "$OSRM_DATASET_DIR:/data:ro" \
  osrm/osrm-backend \
  osrm-routed --algorithm mld --max-table-size "$OSRM_MAX_TABLE_SIZE" "/data/$OSRM_DATASET_FILE"

echo "South Korea OSRM is starting at http://${OSRM_BIND_HOST}:${OSRM_PORT}"
