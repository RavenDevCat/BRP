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

OSRM_DATASET_DIR="${OSRM_BANGKOK_DATASET_DIR:-${OSRM_THAILAND_DATASET_DIR:-$LOCAL_OSRM_DIR/bangkok}}"
if [[ ! -d "$OSRM_DATASET_DIR" && -d "$LOCAL_OSRM_DIR/thailand" ]]; then
  OSRM_DATASET_DIR="$LOCAL_OSRM_DIR/thailand"
fi
OSRM_PBF_URL="${OSRM_BANGKOK_SOURCE_PBF_URL:-https://download.geofabrik.de/asia/thailand-latest.osm.pbf}"
OSRM_SOURCE_PBF_FILE="${OSRM_BANGKOK_SOURCE_PBF_FILE:-thailand-latest.osm.pbf}"
OSRM_PBF_FILE="${OSRM_BANGKOK_PBF_FILE:-thailand-bangkok.osm.pbf}"
OSRM_DATASET_FILE="${OSRM_BANGKOK_DATASET_FILE:-thailand-bangkok.osrm}"
OSRM_BANGKOK_BBOX="${OSRM_BANGKOK_BBOX:-99.0,12.4,101.6,14.8}"
OSRM_EXTRACT_THREADS="${OSRM_EXTRACT_THREADS:-2}"

mkdir -p "$OSRM_DATASET_DIR"

if [[ ! -f "$OSRM_DATASET_DIR/$OSRM_SOURCE_PBF_FILE" ]]; then
  echo "Downloading Thailand OSM extract to $OSRM_DATASET_DIR/$OSRM_SOURCE_PBF_FILE"
  curl -fL "$OSRM_PBF_URL" -o "$OSRM_DATASET_DIR/$OSRM_SOURCE_PBF_FILE"
fi

if [[ ! -f "$OSRM_DATASET_DIR/$OSRM_PBF_FILE" ]]; then
  echo "Extracting Bangkok OSRM bbox $OSRM_BANGKOK_BBOX to $OSRM_DATASET_DIR/$OSRM_PBF_FILE"
  osmium extract -b "$OSRM_BANGKOK_BBOX" "$OSRM_DATASET_DIR/$OSRM_SOURCE_PBF_FILE" -o "$OSRM_DATASET_DIR/$OSRM_PBF_FILE" --overwrite
fi

docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua --threads "$OSRM_EXTRACT_THREADS" "/data/$OSRM_PBF_FILE"
docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend osrm-partition "/data/$OSRM_DATASET_FILE"
docker run --rm -t -v "$OSRM_DATASET_DIR:/data" osrm/osrm-backend osrm-customize "/data/$OSRM_DATASET_FILE"

echo "Bangkok OSRM dataset prepared in $OSRM_DATASET_DIR"
echo "Run ops/scripts/run_osrm_bangkok.sh to start OSRM on port 5007."
