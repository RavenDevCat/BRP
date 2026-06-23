#!/usr/bin/env python3
"""Create synthetic city traffic probe baseline JSON without calling providers."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import BusingProblem as planner  # noqa: E402


PRESET_BBOXES: dict[tuple[str, str], tuple[float, float, float, float]] = {
    ("BK", "Bangkok"): (14.8, 101.6, 12.4, 99.0),
    ("KR", "Seoul Metro"): (37.85, 127.35, 37.15, 126.55),
}


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _bbox(market: str, city: str) -> tuple[float, float, float, float]:
    market_key = _norm(market).upper()
    city_key = _norm(city)
    if market_key in {"CN", "CHINA"}:
        config = planner.CHINA_CITY_CONFIGS.get(planner._normalize_china_city_key(city_key))
        if config and config.get("bbox"):
            return tuple(config["bbox"])  # type: ignore[return-value]
    for (preset_market, preset_city), bbox in PRESET_BBOXES.items():
        if market_key == preset_market and city_key.casefold() == preset_city.casefold():
            return bbox
    raise SystemExit(f"Unsupported market/city: {market}/{city}")


def _country(market: str) -> str:
    market_key = _norm(market).upper()
    if market_key in {"CN", "CHINA"}:
        return "China"
    if market_key in {"KR", "KOREA", "SOUTH KOREA"}:
        return "South Korea"
    if market_key in {"BK", "BANGKOK", "TH", "THAILAND"}:
        return "Thailand"
    return market_key.title()


def _zone(row: int, col: int, max_index: int) -> str:
    edge = row in {0, max_index} or col in {0, max_index}
    inner = 1 <= row < max_index and 1 <= col < max_index
    if edge:
        return "suburban"
    if inner and abs(row - max_index / 2) <= 1 and abs(col - max_index / 2) <= 1:
        return "center"
    return "residential"


def _grid_points(bbox: tuple[float, float, float, float], grid_size: int) -> list[dict[str, Any]]:
    north, east, south, west = bbox
    grid_size = max(3, grid_size)
    points: list[dict[str, Any]] = []
    for row in range(grid_size):
        lat = south + (north - south) * row / (grid_size - 1)
        for col in range(grid_size):
            lng = west + (east - west) * col / (grid_size - 1)
            points.append(
                {
                    "id": f"r{row:02d}c{col:02d}",
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "zone": _zone(row, col, grid_size - 1),
                    "row": row,
                    "col": col,
                }
            )
    return points


def _bearing_sector(origin: dict[str, Any], destination: dict[str, Any]) -> int:
    lat1 = math.radians(float(origin["lat"]))
    lat2 = math.radians(float(destination["lat"]))
    delta_lng = math.radians(float(destination["lng"]) - float(origin["lng"]))
    y = math.sin(delta_lng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lng)
    return int(round(((math.degrees(math.atan2(y, x)) + 360.0) % 360.0) / 45.0)) % 8


def _stop(point: dict[str, Any], *, sequence: int, country: str, city: str, is_school: bool = False) -> dict[str, Any]:
    label = "Synthetic school anchor" if is_school else f"Synthetic {point['zone']} probe {point['id']}"
    return {
        "stop_sequence": sequence,
        "address": label,
        "country": country,
        "city": city,
        "lat": point["lat"],
        "lng": point["lng"],
        "passenger_count": 0 if is_school else 1,
        "is_school": is_school,
        "synthetic_probe_point": True,
        "synthetic_zone": point["zone"],
    }


def _route(origin: dict[str, Any], school: dict[str, Any], *, index: int, country: str, city: str, direction: str) -> dict[str, Any]:
    elbow = {
        "id": f"{origin['id']}-elbow",
        "lat": origin["lat"],
        "lng": school["lng"],
        "zone": "connector",
    }
    stops = [
        _stop(origin, sequence=1, country=country, city=city),
        _stop(elbow, sequence=2, country=country, city=city),
        _stop(school, sequence=3, country=country, city=city, is_school=True),
    ]
    if direction == "from_school":
        stops = list(reversed(stops))
        for sequence, stop in enumerate(stops, start=1):
            stop["stop_sequence"] = sequence
    km = planner.haversine_distance_km(float(origin["lat"]), float(origin["lng"]), float(school["lat"]), float(school["lng"]))
    return {
        "route_id": f"synthetic-{index:03d}-{direction}-{origin['zone']}-b{_bearing_sector(origin, school)}",
        "bus_type": "Synthetic Probe",
        "synthetic_probe": True,
        "synthetic_zone": origin["zone"],
        "synthetic_distance_band": "short" if km < 8 else "mid" if km < 20 else "long",
        "stops": stops,
    }


def build_probe_baseline(market: str, city: str, *, grid_size: int, direction: str) -> dict[str, Any]:
    bbox = _bbox(market, city)
    country = _country(market)
    grid = _grid_points(bbox, grid_size)
    center = min(
        grid,
        key=lambda item: abs(item["lat"] - (bbox[0] + bbox[2]) / 2) + abs(item["lng"] - (bbox[1] + bbox[3]) / 2),
    )
    school = {**center, "id": "school", "zone": "school"}
    origins = [point for point in grid if point["id"] != center["id"]]
    directions = ["to_school", "from_school"] if direction == "both" else [direction]
    routes = [
        _route(origin, school, index=index, country=country, city=city, direction=route_direction)
        for index, (route_direction, origin) in enumerate(
            ((route_direction, origin) for route_direction in directions for origin in origins),
            start=1,
        )
    ]
    return {
        "baseline_id": f"synthetic-{_norm(market).lower()}-{_norm(city).lower().replace(' ', '-')}-{grid_size}x{grid_size}-{direction}",
        "source_title": f"Synthetic traffic probe - {market} {city}",
        "service_direction": direction,
        "synthetic_probe_baseline": True,
        "market": market,
        "country": country,
        "city": city,
        "bbox": {"north": bbox[0], "east": bbox[1], "south": bbox[2], "west": bbox[3]},
        "grid_size": grid_size,
        "route_count": len(routes),
        "point_count": len(grid),
        "routes": routes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True, help="CN, BK, or KR")
    parser.add_argument("--city", required=True, help="Shanghai, Suzhou, Bangkok, or Seoul Metro")
    parser.add_argument("--direction", choices=("to_school", "from_school", "both"), default="both")
    parser.add_argument("--grid-size", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    baseline = build_probe_baseline(args.market, args.city, grid_size=args.grid_size, direction=args.direction)
    payload = json.dumps(baseline, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    print(
        f"synthetic_probe_plan routes={baseline['route_count']} points={baseline['point_count']} "
        f"market={args.market} city={args.city} provider_api_called=false osrm_started=false",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
