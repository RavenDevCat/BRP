#!/bin/bash
set -euo pipefail

DEFAULT_OSRM_DIR="/opt/brp/osrm-data"
if [[ -n "${OSRM_LOCAL_DATA_DIR:-}" ]]; then
  LOCAL_OSRM_DIR="$OSRM_LOCAL_DATA_DIR"
elif [[ -d "$DEFAULT_OSRM_DIR" ]]; then
  LOCAL_OSRM_DIR="$DEFAULT_OSRM_DIR"
elif [[ -d "$HOME/brp-osrm-data" ]]; then
  LOCAL_OSRM_DIR="$HOME/brp-osrm-data"
else
  LOCAL_OSRM_DIR="$DEFAULT_OSRM_DIR"
fi

OSRM_BIND_HOST="${OSRM_BIND_HOST:-127.0.0.1}"
OSRM_DOCKER_PLATFORM="${OSRM_DOCKER_PLATFORM:-linux/amd64}"
OSRM_PORT="${OSRM_BANGKOK_PORT:-${OSRM_THAILAND_PORT:-5007}}"
OSRM_MAX_TABLE_SIZE="${OSRM_MAX_TABLE_SIZE:-1000}"
OSRM_DATASET_DIR="${OSRM_BANGKOK_DATASET_DIR:-${OSRM_THAILAND_DATASET_DIR:-$LOCAL_OSRM_DIR/bangkok}}"
if [[ ! -d "$OSRM_DATASET_DIR" && -d "$LOCAL_OSRM_DIR/thailand" ]]; then
  OSRM_DATASET_DIR="$LOCAL_OSRM_DIR/thailand"
fi
OSRM_DATASET_FILE="${OSRM_BANGKOK_DATASET_FILE:-${OSRM_THAILAND_DATASET_FILE:-thailand-bangkok.osrm}}"
OSRM_CONTAINER_NAME="${OSRM_BANGKOK_CONTAINER_NAME:-${OSRM_THAILAND_CONTAINER_NAME:-osrm-bangkok}}"

if [[ ! -d "$OSRM_DATASET_DIR" ]]; then
  echo "Bangkok OSRM dataset directory not found: $OSRM_DATASET_DIR" >&2
  echo "Expected preprocessed .osrm* files in that directory." >&2
  exit 1
fi

if [[ ! -f "$OSRM_DATASET_DIR/$OSRM_DATASET_FILE" ]]; then
  echo "Bangkok OSRM dataset file not found: $OSRM_DATASET_DIR/$OSRM_DATASET_FILE" >&2
  echo "Run ops/scripts/prepare_osrm_bangkok.sh first or set OSRM_BANGKOK_DATASET_FILE." >&2
  exit 1
fi

docker rm -f "$OSRM_CONTAINER_NAME" 2>/dev/null || true
docker run -d --name "$OSRM_CONTAINER_NAME" --platform "$OSRM_DOCKER_PLATFORM" --restart unless-stopped -p "${OSRM_BIND_HOST}:${OSRM_PORT}:5000" -v "$OSRM_DATASET_DIR:/data:ro" osrm/osrm-backend osrm-routed --algorithm mld --max-table-size "$OSRM_MAX_TABLE_SIZE" "/data/$OSRM_DATASET_FILE"

echo "Bangkok OSRM is starting at http://${OSRM_BIND_HOST}:${OSRM_PORT}"
