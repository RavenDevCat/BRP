from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import BusingProblem as planner  # noqa: E402


def _point(node_id: int, lat: float, lng: float, plot_lat: float, plot_lng: float) -> dict[str, object]:
    return {
        "node_id": node_id,
        "address": f"Stop {node_id}",
        "lat": lat,
        "lng": lng,
        "plot_lat": plot_lat,
        "plot_lng": plot_lng,
    }


def test_short_leg_with_large_detour_tries_raw_coordinate_route() -> None:
    origin = _point(1, 31.0000, 121.0000, 31.0045, 121.0045)
    destination = _point(2, 31.0100, 121.0000, 31.0145, 121.0045)

    assert planner._should_try_raw_coordinate_route(origin, destination, distance_m=4200)


def test_small_extra_detour_does_not_try_raw_coordinate_route() -> None:
    origin = _point(1, 31.0000, 121.0000, 31.0045, 121.0045)
    destination = _point(2, 31.0100, 121.0000, 31.0145, 121.0045)

    assert not planner._should_try_raw_coordinate_route(origin, destination, distance_m=2200)


def test_short_leg_with_backtracking_geometry_tries_raw_coordinate_route() -> None:
    origin = _point(1, 31.0000, 121.0000, 31.0045, 121.0045)
    destination = _point(2, 31.0100, 121.0000, 31.0145, 121.0045)
    geometry = [
        (31.0045, 121.0045),
        (31.0200, 121.0045),
        (31.0060, 121.0045),
        (31.0145, 121.0045),
    ]

    assert planner._should_try_raw_coordinate_route(
        origin,
        destination,
        distance_m=3100,
        geometry=geometry,
    )


def test_snap_connectors_keep_osrm_geometry_separate_from_stop_markers() -> None:
    origin = _point(1, 31.0000, 121.0000, 31.0000, 121.0000)
    destination = _point(2, 31.0100, 121.0100, 31.0100, 121.0100)
    geometry = [(31.0005, 121.0005), (31.0095, 121.0095)]

    connectors = planner._snap_connectors_for_leg(origin, destination, geometry)

    assert [connector["type"] for connector in connectors] == ["origin", "destination"]
    assert connectors[0]["geometry"][0] == (31.0000, 121.0000)
    assert connectors[0]["geometry"][1] == geometry[0]
    assert connectors[1]["geometry"][0] == geometry[-1]
    assert connectors[1]["geometry"][1] == (31.0100, 121.0100)
