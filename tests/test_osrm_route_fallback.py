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
