from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = ROOT / "apps"
CLIENT_DIR = APPS_DIR / "client"
for path in (APPS_DIR, CLIENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import demand_global_optimizer  # noqa: E402
from planning_assumptions import PlanningAssumptions  # noqa: E402
from planning_contract import PlanningRequest  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "planning_contract"


def _load_request(name: str) -> PlanningRequest:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return PlanningRequest.from_dict(payload["request"])


def _geocode_payload(request: PlanningRequest) -> dict:
    def point_payload(point) -> dict:
        return {
            "status": "ok",
            "address": point.address,
            "formatted_address": point.address,
            "lat": point.latitude,
            "lng": point.longitude,
            "student_count": point.demand,
        }

    return {
        "school": point_payload(request.depot),
        "demand_points": [point_payload(point) for point in request.service_points],
    }


def _custom_catalog(request: PlanningRequest) -> list[dict]:
    return [
        {
            "vehicle_type": vehicle.vehicle_type,
            "display_name": vehicle.vehicle_type,
            "listed_seats": vehicle.capacity + 1,
            "category": vehicle.metadata.get("category", "mini_bus"),
            "propulsion": "diesel",
            "available_count": vehicle.available_count,
            "enabled": True,
        }
        for vehicle in request.vehicles
    ]


def _matrix(node_count: int) -> tuple[list[list[int]], list[list[float]]]:
    durations = [
        [0 if row == column else 40 + 40 * abs(row - column) for column in range(node_count)]
        for row in range(node_count)
    ]
    distances = [[float(value * 10) for value in row] for row in durations]
    return durations, distances


@pytest.mark.parametrize(
    "fixture_name",
    ["cn_to_school.json", "kr_from_school.json"],
)
def test_fleet_legacy_solver_preserves_contract_invariants(fixture_name: str) -> None:
    request = _load_request(fixture_name)
    constraints = request.constraints
    assumptions = PlanningAssumptions(
        market=request.market,
        mode="balanced",
        monitor_seats=1,
        max_route_duration_minutes=int(constraints.max_route_duration_s // 60),
        max_stops_per_route=int(constraints.max_stops_per_route),
        target_load_factor=1.0,
        min_reasonable_load_factor=0.0,
        default_max_vehicles_per_type=max(vehicle.available_count for vehicle in request.vehicles),
    )
    real_search_builder = demand_global_optimizer.build_guided_local_search_parameters

    def quick_search(*args, **kwargs):
        kwargs["time_limit_seconds"] = 1
        return real_search_builder(*args, **kwargs)

    with (
        patch.object(demand_global_optimizer, "get_planning_assumptions", return_value=assumptions),
        patch.object(demand_global_optimizer, "_build_osrm_matrix", return_value=_matrix(len(request.points))),
        patch.object(
            demand_global_optimizer,
            "build_guided_local_search_parameters",
            side_effect=quick_search,
        ),
    ):
        result = demand_global_optimizer.build_global_ortools_plan(
            _geocode_payload(request),
            market=request.market,
            mode="balanced",
            monitor_seats=1,
            custom_catalog=_custom_catalog(request),
            service_direction=request.direction,
        )

    routes = result["routes"]
    rows = result["route_rows"]
    service_nodes = [node for route in routes for node in route["order"] if node != 0]

    assert sorted(service_nodes) == list(range(1, len(request.points)))
    assert sum(row["students"] for row in rows) == request.total_demand
    assert all(row["students"] <= route["selected_vehicle"]["student_capacity"] for row, route in zip(rows, routes))
    assert all(row["stops"] <= constraints.max_stops_per_route for row in rows)
    assert all(route["duration_s"] <= constraints.max_route_duration_s for route in routes)
    assert result["summary"]["service_direction"] == request.direction
    if request.direction == "to_school":
        assert all(route["order"][-1] == 0 for route in routes)
    else:
        assert all(route["order"][0] == 0 for route in routes)
