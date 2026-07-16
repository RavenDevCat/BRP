#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <china.osm.pbf> <output-root>" >&2
  exit 2
fi

SOURCE_PBF=$1
OUTPUT_ROOT=$2
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SHANGHAI_RELATION_ID=${SHANGHAI_RELATION_ID:-913067}
SHANGHAI_AREA_ID=$((SHANGHAI_RELATION_ID * 2 + 1))

command -v osmium >/dev/null
command -v python3 >/dev/null
command -v jq >/dev/null
[[ -f "$SOURCE_PBF" ]] || { echo "Missing source PBF: $SOURCE_PBF" >&2; exit 2; }

mkdir -p \
  "$OUTPUT_ROOT/working" \
  "$OUTPUT_ROOT/exports" \
  "$OUTPUT_ROOT/canonical" \
  "$OUTPUT_ROOT/metadata" \
  "$OUTPUT_ROOT/reports" \
  "$OUTPUT_ROOT/manifests"

cp "$SCRIPT_DIR/README.md" "$OUTPUT_ROOT/metadata/README.md"
cp "$SCRIPT_DIR/source_registry.json" "$OUTPUT_ROOT/metadata/source_registry.json"
cp "$SCRIPT_DIR/lane_feature.schema.json" "$OUTPUT_ROOT/metadata/lane_feature.schema.json"

SHANGHAI_PBF="$OUTPUT_ROOT/working/shanghai_municipality.osm.pbf"
BOUNDARY_PBF="$OUTPUT_ROOT/working/shanghai_boundary.osm.pbf"
BOUNDARY_ALL="$OUTPUT_ROOT/exports/shanghai_boundary_all.geojson"
BOUNDARY_GEOJSON="$OUTPUT_ROOT/canonical/shanghai_municipality_boundary.geojson"
LANES_PBF="$OUTPUT_ROOT/working/osm_bus_lane_candidates.osm.pbf"
ROUTES_PBF="$OUTPUT_ROOT/working/osm_bus_route_relations.osm.pbf"
LANES_SEQ="$OUTPUT_ROOT/exports/osm_bus_lane_candidates.geojsonseq"
ROUTES_OSM="$OUTPUT_ROOT/exports/osm_bus_route_relations.osm"

osmium getid \
  --add-referenced \
  --overwrite \
  --output "$BOUNDARY_PBF" \
  "$SOURCE_PBF" \
  "r$SHANGHAI_RELATION_ID"

osmium export \
  --add-unique-id type_id \
  --overwrite \
  --format geojson \
  --output "$BOUNDARY_ALL" \
  "$BOUNDARY_PBF"

jq --arg area_id "a$SHANGHAI_AREA_ID" \
  '{type:"FeatureCollection",features:[.features[] | select(.id==$area_id)]}' \
  "$BOUNDARY_ALL" > "$BOUNDARY_GEOJSON"

[[ $(jq '.features | length' "$BOUNDARY_GEOJSON") -eq 1 ]] || {
  echo "Could not isolate Shanghai boundary relation r$SHANGHAI_RELATION_ID" >&2
  exit 2
}

osmium extract \
  --polygon "$BOUNDARY_GEOJSON" \
  --strategy complete_ways \
  --overwrite \
  --output "$SHANGHAI_PBF" \
  "$SOURCE_PBF"

osmium tags-filter \
  --overwrite \
  --output "$LANES_PBF" \
  "$SHANGHAI_PBF" \
  w/highway=busway \
  w/busway w/busway:left w/busway:right w/busway:both \
  w/lanes:bus w/lanes:bus:forward w/lanes:bus:backward \
  w/bus:lanes w/bus:lanes:forward w/bus:lanes:backward \
  w/lanes:psv w/lanes:psv:forward w/lanes:psv:backward \
  w/psv:lanes w/psv:lanes:forward w/psv:lanes:backward

osmium tags-filter \
  --overwrite \
  --output "$ROUTES_PBF" \
  "$SHANGHAI_PBF" \
  r/route=bus

osmium export \
  --add-unique-id type_id \
  --overwrite \
  --format geojsonseq \
  --output "$LANES_SEQ" \
  "$LANES_PBF"

osmium cat --overwrite --output "$ROUTES_OSM" "$ROUTES_PBF"

python3 "$SCRIPT_DIR/normalize_assets.py" \
  --lanes "$LANES_SEQ" \
  --routes "$ROUTES_OSM" \
  --boundary "$BOUNDARY_GEOJSON" \
  --source-pbf "$SOURCE_PBF" \
  --output-root "$OUTPUT_ROOT"
