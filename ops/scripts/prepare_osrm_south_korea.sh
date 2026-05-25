#!/bin/bash
set -euo pipefail

LOCAL_OSRM_DIR="${OSRM_LOCAL_DATA_DIR:-/opt/brp/osrm-data}"
OSRM_DATASET_DIR="${OSRM_SOUTH_KOREA_DATASET_DIR:-$LOCAL_OSRM_DIR/south-korea}"
OSRM_PBF_FILE="${OSRM_SOUTH_KOREA_PBF_FILE:-south-korea-latest.osm.pbf}"
OSRM_DATASET_FILE="${OSRM_SOUTH_KOREA_DATASET_FILE:-south-korea-latest.osrm}"
OSRM_EXTRACT_THREADS="${OSRM_EXTRACT_THREADS:-2}"

mkdir -p "$OSRM_DATASET_DIR"

if [[ ! -f "$OSRM_DATASET_DIR/$OSRM_PBF_FILE" ]]; then
  echo "South Korea PBF file not found: $OSRM_DATASET_DIR/$OSRM_PBF_FILE" >&2
  exit 1
fi

docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend \
  osrm-extract -p /opt/car.lua --threads "$OSRM_EXTRACT_THREADS" "/data/$OSRM_PBF_FILE"

docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend \
  osrm-partition "/data/$OSRM_DATASET_FILE"

docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend \
  osrm-customize "/data/$OSRM_DATASET_FILE"

echo "South Korea OSRM dataset prepared in $OSRM_DATASET_DIR"
echo "Run ops/scripts/run_osrm_south_korea.sh to start OSRM on port 5006."
