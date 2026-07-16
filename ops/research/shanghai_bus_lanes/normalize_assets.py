#!/usr/bin/env python3
"""Normalize OSM bus-lane and route exports without touching BRP runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


LANE_KEYS = {
    "busway",
    "busway:left",
    "busway:right",
    "busway:both",
    "lanes:bus",
    "lanes:bus:forward",
    "lanes:bus:backward",
    "bus:lanes",
    "bus:lanes:forward",
    "bus:lanes:backward",
    "lanes:psv",
    "lanes:psv:forward",
    "lanes:psv:backward",
    "psv:lanes",
    "psv:lanes:forward",
    "psv:lanes:backward",
}


def iter_geojson_sequence(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            if feature.get("type") == "Feature":
                yield feature


def line_parts(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "LineString":
        yield coordinates
    elif geometry_type == "MultiLineString":
        yield from coordinates


def haversine_m(first: list[float], second: list[float]) -> float:
    lon1, lat1 = map(math.radians, first[:2])
    lon2, lat2 = map(math.radians, second[:2])
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(value)))


def geometry_length_m(geometry: dict[str, Any]) -> float:
    return sum(
        haversine_m(first, second)
        for part in line_parts(geometry)
        for first, second in zip(part, part[1:])
    )


def classify_lane(tags: dict[str, Any]) -> str:
    if tags.get("highway") == "busway":
        return "separate_busway"
    if any(key.startswith(("lanes:bus", "bus:lanes", "lanes:psv", "psv:lanes")) for key in tags):
        return "designated_lane"
    return "legacy_busway"


def lane_directions(tags: dict[str, Any]) -> list[str]:
    explicit_forward = any(key.endswith(":forward") for key in tags if key in LANE_KEYS)
    explicit_backward = any(key.endswith(":backward") for key in tags if key in LANE_KEYS)
    if explicit_forward or explicit_backward:
        return [
            direction
            for direction, present in (
                ("forward", explicit_forward),
                ("backward", explicit_backward),
            )
            if present
        ]
    if str(tags.get("oneway", "")).lower() in {"yes", "1", "true"}:
        return ["forward"]
    if str(tags.get("oneway", "")).lower() == "-1":
        return ["backward"]
    return ["forward", "backward"]


def active_conditions(tags: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in tags.items()
        if "conditional" in key or key in {"opening_hours", "service_times"}
    }


def source_ref(feature: dict[str, Any]) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("@id") or feature.get("id") or "unknown")


def canonical_lane(feature: dict[str, Any]) -> dict[str, Any] | None:
    geometry = feature.get("geometry") or {}
    if geometry.get("type") not in {"LineString", "MultiLineString"}:
        return None
    tags = {
        key: value
        for key, value in (feature.get("properties") or {}).items()
        if not key.startswith("@")
    }
    if tags.get("highway") != "busway" and not any(key in tags for key in LANE_KEYS):
        return None
    ref = source_ref(feature)
    return {
        "type": "Feature",
        "id": f"osm:{ref}",
        "geometry": geometry,
        "properties": {
            "asset_id": f"osm:{ref}",
            "source": "openstreetmap",
            "source_ref": ref,
            "source_license": "ODbL-1.0",
            "lane_kind": classify_lane(tags),
            "directions": lane_directions(tags),
            "active_conditions": active_conditions(tags),
            "verification_status": "candidate",
            "school_bus_eligible": None,
            "length_m": round(geometry_length_m(geometry), 1),
            "osm_tags": tags,
        },
    }


def assemble_route_geometry(
    member_way_refs: list[str],
    ways: dict[str, list[str]],
    nodes: dict[str, list[float]],
) -> tuple[dict[str, Any] | None, int, int]:
    parts: list[list[list[float]]] = []
    current: list[list[float]] = []
    missing_ways = 0
    missing_nodes = 0

    for way_ref in member_way_refs:
        node_refs = ways.get(way_ref)
        if not node_refs:
            missing_ways += 1
            continue
        coordinates = []
        for node_ref in node_refs:
            coordinate = nodes.get(node_ref)
            if coordinate is None:
                missing_nodes += 1
            else:
                coordinates.append(coordinate)
        if len(coordinates) < 2:
            continue
        if current:
            forward_gap = haversine_m(current[-1], coordinates[0])
            reverse_gap = haversine_m(current[-1], coordinates[-1])
            if reverse_gap < forward_gap:
                coordinates.reverse()
                forward_gap = reverse_gap
            if forward_gap <= 2:
                current.extend(coordinates[1:])
                continue
            parts.append(current)
        current = coordinates

    if current:
        parts.append(current)
    if not parts:
        return None, missing_ways, missing_nodes
    if len(parts) == 1:
        return {"type": "LineString", "coordinates": parts[0]}, missing_ways, missing_nodes
    return {"type": "MultiLineString", "coordinates": parts}, missing_ways, missing_nodes


def canonical_osm_route(
    relation_id: str,
    tags: dict[str, str],
    member_way_refs: list[str],
    ways: dict[str, list[str]],
    nodes: dict[str, list[float]],
) -> dict[str, Any] | None:
    geometry, missing_ways, missing_nodes = assemble_route_geometry(member_way_refs, ways, nodes)
    if geometry is None:
        return None
    ref = f"relation/{relation_id}"
    return {
        "type": "Feature",
        "id": f"osm:{ref}",
        "geometry": geometry,
        "properties": {
            "asset_id": f"osm:{ref}",
            "source": "openstreetmap",
            "source_ref": ref,
            "source_license": "ODbL-1.0",
            "route_ref": tags.get("ref"),
            "name": tags.get("name"),
            "operator": tags.get("operator"),
            "network": tags.get("network"),
            "from": tags.get("from"),
            "to": tags.get("to"),
            "length_m": round(geometry_length_m(geometry), 1),
            "dedicated_lane_evidence": False,
            "member_way_count": len(member_way_refs),
            "geometry_part_count": len(list(line_parts(geometry))),
            "geometry_complete": missing_ways == 0 and missing_nodes == 0,
            "missing_way_count": missing_ways,
            "missing_node_count": missing_nodes,
            "osm_tags": tags,
        },
    }


def read_osm_bus_routes(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    nodes: dict[str, list[float]] = {}
    ways: dict[str, list[str]] = {}
    features: list[dict[str, Any]] = []
    relation_count = 0
    without_geometry = 0

    for _, element in ET.iterparse(path, events=("end",)):
        element_type = element.tag.rsplit("}", 1)[-1]
        if element_type == "node":
            nodes[element.attrib["id"]] = [
                float(element.attrib["lon"]),
                float(element.attrib["lat"]),
            ]
        elif element_type == "way":
            ways[element.attrib["id"]] = [child.attrib["ref"] for child in element.findall("nd")]
        elif element_type == "relation":
            tags = {child.attrib["k"]: child.attrib["v"] for child in element.findall("tag")}
            if tags.get("route") == "bus":
                relation_count += 1
                member_way_refs = [
                    child.attrib["ref"]
                    for child in element.findall("member")
                    if child.attrib.get("type") == "way"
                    and "platform" not in child.attrib.get("role", "")
                ]
                feature = canonical_osm_route(
                    element.attrib["id"], tags, member_way_refs, ways, nodes
                )
                if feature is None:
                    without_geometry += 1
                else:
                    features.append(feature)
        if element_type in {"node", "way", "relation"}:
            element.clear()

    return features, {
        "source_relation_count": relation_count,
        "relations_without_geometry": without_geometry,
        "incomplete_geometry_count": sum(
            not feature["properties"]["geometry_complete"] for feature in features
        ),
    }


def write_feature_collection(path: Path, features: Iterable[dict[str, Any]]) -> dict[str, Any]:
    count = 0
    total_length_m = 0.0
    kinds: Counter[str] = Counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"type":"FeatureCollection","features":[')
        first = True
        for feature in features:
            if not first:
                handle.write(",")
            json.dump(feature, handle, ensure_ascii=False, separators=(",", ":"))
            first = False
            count += 1
            properties = feature["properties"]
            total_length_m += float(properties.get("length_m") or 0)
            if properties.get("lane_kind"):
                kinds[properties["lane_kind"]] += 1
        handle.write("]}")
    return {
        "feature_count": count,
        "summed_geometry_km": round(total_length_m / 1000, 3),
        "lane_kind_counts": dict(sorted(kinds.items())),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(args: argparse.Namespace) -> None:
    root = Path(args.output_root)
    lane_path = root / "canonical" / "shanghai_bus_lane_candidates.geojson"
    route_path = root / "canonical" / "shanghai_bus_route_corridors.geojson"

    lane_summary = write_feature_collection(
        lane_path,
        filter(None, (canonical_lane(feature) for feature in iter_geojson_sequence(Path(args.lanes)))),
    )
    route_features, route_source_summary = read_osm_bus_routes(Path(args.routes))
    route_summary = write_feature_collection(route_path, route_features)
    route_summary.update(route_source_summary)
    route_summary["geometry_complete_ratio"] = round(
        (route_summary["feature_count"] - route_summary["incomplete_geometry_count"])
        / max(1, route_summary["feature_count"]),
        4,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    official_km = 535.9
    report = {
        "generated_at": generated_at,
        "scope": "Shanghai research foundation; not connected to BRP routing",
        "boundary": "OSM relation 913067 (ISO3166-2=CN-SH)",
        "lane_candidates": lane_summary,
        "bus_route_corridors": route_summary,
        "official_reference_network_km": official_km,
        "osm_length_to_official_reference_ratio": round(
            lane_summary["summed_geometry_km"] / official_km, 4
        ),
        "coverage_warning": (
            "The ratio is a diagnostic only. OSM geometry can be incomplete, duplicated by direction, "
            "or include non-current features; no candidate is authoritative verification."
        ),
        "route_corridor_warning": (
            "Bus route corridors are reference geometry only and are not dedicated-lane evidence."
        ),
        "official_geometry_status": "no complete public downloadable GIS layer found",
        "missing_for_production": [
            "authoritative segment geometry",
            "direction verification",
            "active time windows",
            "current operational status",
            "school-bus eligibility verification",
        ],
    }
    report_path = root / "reports" / "coverage_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source_pbf = Path(args.source_pbf)
    manifest = {
        "generated_at": generated_at,
        "source": {
            "path_basename": source_pbf.name,
            "size_bytes": source_pbf.stat().st_size,
            "sha256": sha256(source_pbf),
            "license": "ODbL-1.0",
        },
        "inputs": {
            "lane_export_sha256": sha256(Path(args.lanes)),
            "route_osm_sha256": sha256(Path(args.routes)),
            "boundary_sha256": sha256(Path(args.boundary)),
        },
        "outputs": {
            str(Path(args.boundary).relative_to(root)): sha256(Path(args.boundary)),
            str(lane_path.relative_to(root)): sha256(lane_path),
            str(route_path.relative_to(root)): sha256(route_path),
            str(report_path.relative_to(root)): sha256(report_path),
            "metadata/README.md": sha256(root / "metadata" / "README.md"),
            "metadata/source_registry.json": sha256(root / "metadata" / "source_registry.json"),
            "metadata/lane_feature.schema.json": sha256(
                root / "metadata" / "lane_feature.schema.json"
            ),
        },
        "runtime_integration": False,
    }
    manifest_path = root / "manifests" / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_check() -> None:
    line = {"type": "LineString", "coordinates": [[121.0, 31.0], [121.001, 31.0]]}
    feature = {
        "type": "Feature",
        "id": "w1",
        "geometry": line,
        "properties": {"@id": "way/1", "highway": "primary", "bus:lanes": "yes|designated"},
    }
    normalized = canonical_lane(feature)
    assert normalized is not None
    assert normalized["properties"]["lane_kind"] == "designated_lane"
    assert normalized["properties"]["directions"] == ["forward", "backward"]
    assert 90 < normalized["properties"]["length_m"] < 100
    geometry, missing_ways, missing_nodes = assemble_route_geometry(
        ["10", "11"],
        {"10": ["1", "2"], "11": ["3", "2"]},
        {"1": [121.0, 31.0], "2": [121.001, 31.0], "3": [121.002, 31.0]},
    )
    assert geometry == {
        "type": "LineString",
        "coordinates": [[121.0, 31.0], [121.001, 31.0], [121.002, 31.0]],
    }
    assert (missing_ways, missing_nodes) == (0, 0)
    print("self-check ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--lanes")
    parser.add_argument("--routes")
    parser.add_argument("--boundary")
    parser.add_argument("--source-pbf")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    if not args.self_check and not all(
        (args.lanes, args.routes, args.boundary, args.source_pbf, args.output_root)
    ):
        parser.error(
            "build mode requires --lanes, --routes, --boundary, --source-pbf, and --output-root"
        )
    return args


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.self_check:
        self_check()
    else:
        normalize(arguments)
