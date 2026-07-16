# Shanghai Bus-Lane Foundation

This directory builds research-only Shanghai transport assets. It does not
change the BRP backend, solver, OSRM Manager, environment variables, or any
runtime route-selection behavior.

## Outputs

The build produces five independent artifacts under the supplied output root:

- `canonical/shanghai_municipality_boundary.geojson`: Shanghai municipality
  boundary from OSM relation `913067` (`ISO3166-2=CN-SH`). It is used to
  prevent nearby-city features from entering the extract.

- `canonical/shanghai_bus_lane_candidates.geojson`: OSM ways carrying an
  explicit bus-lane or busway tag. Every record remains `candidate` until an
  authoritative source verifies it.
- `canonical/shanghai_bus_route_corridors.geojson`: OSM `route=bus` relations
  assembled from their ordered member ways. Completeness counters remain on
  each record. These are discovery/reference corridors only and never imply a
  dedicated lane.
- `reports/coverage_report.json`: counts, geometry lengths, missing evidence,
  and comparison with the official 535.9 km published network figure.
- `manifests/build_manifest.json`: source checksums and exact build inputs.
- `metadata/`: a copy of this guide, the source registry, and the canonical
  lane schema tied to the generated dataset.

Intermediate PBF and GeoJSON sequence files remain under `working/` and
`exports/` so a result can be audited without repeating the source download.

## Build

Requirements: `osmium`, `jq`, `python3`, and a Geofabrik China `.osm.pbf`
extract.

```bash
ops/research/shanghai_bus_lanes/build_assets.sh \
  /path/to/china-latest.osm.pbf \
  /path/to/output-root
```

The pipeline first extracts Shanghai by its municipal boundary, then separately
extracts explicit bus-lane ways and public-bus route relations. The normalizer
uses only the Python standard library; route relations are not inferred from
nearby bus stops or passenger-transit API results.

Run the smallest logic check independently:

```bash
python3 ops/research/shanghai_bus_lanes/normalize_assets.py --self-check
```

## Evidence Rules

- OSM is the reproducible geometry bootstrap and is licensed under ODbL 1.0.
- OSM bus-lane tags are not treated as government verification.
- A public-bus route passing through a road is not evidence of a dedicated
  lane.
- Provider passenger-transit results are not custom school-bus routing data.
- Only a source that identifies the road segment, direction, active window,
  and current status can promote a candidate to `verified`.
- The official 535.9 km figure is a coverage benchmark, not geometry and not a
  reason to inflate or infer missing segments.

See `source_registry.json` for source roles and `lane_feature.schema.json` for
the canonical record contract.
