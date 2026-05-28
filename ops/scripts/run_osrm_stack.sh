#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
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
OSRM_BIND_HOST="${OSRM_BIND_HOST:-0.0.0.0}"
OSRM_DOCKER_PLATFORM="${OSRM_DOCKER_PLATFORM:-linux/amd64}"
OSRM_ENABLED_REGIONS="${OSRM_ENABLED_REGIONS:-auto}"
OSRM_MAX_TABLE_SIZE="${OSRM_MAX_TABLE_SIZE:-1000}"

if [[ ! -d "$LOCAL_OSRM_DIR" ]]; then
  echo "Local OSRM data directory not found: $LOCAL_OSRM_DIR" >&2
  echo "Expected preprocessed .osrm* files under that directory." >&2
  exit 1
fi

OSRM_DIR="$LOCAL_OSRM_DIR"
STARTED_CONTAINERS=()

docker rm -f osrm-shanghai osrm-beijing osrm-suzhou osrm-xian osrm-south-korea 2>/dev/null || true

region_enabled() {
  local region="$1"
  if [[ "$OSRM_ENABLED_REGIONS" == "auto" ]]; then
    return 0
  fi
  local normalized=",${OSRM_ENABLED_REGIONS// /},"
  [[ "$normalized" == *",$region,"* ]]
}

start_region() {
  local region="$1"
  local container="$2"
  local port="$3"
  local dataset_dir="$4"
  local dataset_file="$5"

  if ! region_enabled "$region"; then
    echo "Skipping $region because OSRM_ENABLED_REGIONS=$OSRM_ENABLED_REGIONS"
    return
  fi
  if [[ ! -f "$dataset_dir/$dataset_file" ]]; then
    if [[ "$OSRM_ENABLED_REGIONS" == "auto" ]]; then
      echo "Skipping $region; dataset not found: $dataset_dir/$dataset_file"
      return
    fi
    echo "Dataset for enabled region $region not found: $dataset_dir/$dataset_file" >&2
    exit 1
  fi

  docker run -d --name "$container" --platform "$OSRM_DOCKER_PLATFORM" --restart unless-stopped -p "${OSRM_BIND_HOST}:${port}:5000" \
    -v "$dataset_dir:/data:ro" \
    osrm/osrm-backend \
    osrm-routed --algorithm mld --max-table-size "$OSRM_MAX_TABLE_SIZE" "/data/$dataset_file"
  STARTED_CONTAINERS+=("$container:$port")
}

start_region "shanghai" "osrm-shanghai" "${OSRM_SHANGHAI_PORT:-5002}" "${OSRM_SHANGHAI_DATASET_DIR:-$OSRM_DIR/shanghai}" "${OSRM_SHANGHAI_DATASET_FILE:-shanghai-latest.osrm}"
start_region "beijing" "osrm-beijing" "${OSRM_BEIJING_PORT:-5003}" "${OSRM_BEIJING_DATASET_DIR:-$OSRM_DIR/beijing}" "${OSRM_BEIJING_DATASET_FILE:-beijing-latest.osrm}"
start_region "suzhou" "osrm-suzhou" "${OSRM_SUZHOU_PORT:-5004}" "${OSRM_SUZHOU_DATASET_DIR:-$OSRM_DIR/suzhou}" "${OSRM_SUZHOU_DATASET_FILE:-jiangsu-latest.osrm}"
start_region "xian" "osrm-xian" "${OSRM_XIAN_PORT:-5005}" "${OSRM_XIAN_DATASET_DIR:-$OSRM_DIR/xian}" "${OSRM_XIAN_DATASET_FILE:-shaanxi-latest.osrm}"
start_region "south-korea" "osrm-south-korea" "${OSRM_SOUTH_KOREA_PORT:-5006}" "${OSRM_SOUTH_KOREA_DATASET_DIR:-$OSRM_DIR/south-korea}" "${OSRM_SOUTH_KOREA_DATASET_FILE:-south-korea-latest.osrm}"

if [[ ${#STARTED_CONTAINERS[@]} -eq 0 ]]; then
  echo "No OSRM regions were started. Check OSRM_ENABLED_REGIONS and dataset paths." >&2
  exit 1
fi

echo "Started OSRM containers:"
printf '  %s\n' "${STARTED_CONTAINERS[@]}"
