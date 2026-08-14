from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = ROOT / "apps"
if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from planning_contract import CandidatePlan, CandidateRoute  # noqa: E402
from planning_contract_adapters import (  # noqa: E402
    audit_shadow_from_legacy,
    evaluate_contract_constraints,
    fleet_shadow_from_normalized,
)


def _fleet_state():
    points = [
        {"address": "School", "lat": 31.2, "lng": 121.4, "student_count": 0},
        {"address": "A", "lat": 31.21, "lng": 121.41, "student_count": 2},
        {"address": "B", "lat": 31.22, "lng": 121.42, "student_count": 3},
    ]
    vehicle_pool = [
        {
            "vehicle_type": "mini",
            "display_name": "Mini",
            "category": "mini_bus",
            "capacity": 6,
        },
        {
            "vehicle_type": "mini",
            "display_name": "Mini",
            "category": "mini_bus",
            "capacity": 6,
        },
    ]
    result = {
        "routes": [
            {
                "cluster_id": "G01",
                "solver": "global_ortools",
                "order": [2, 1, 0],
                "ordered_points": [points[2], points[1], points[0]],
                "duration_s": 300,
                "distance_m": 2500.0,
                "selected_vehicle": {
                    "vehicle_type": "mini",
                    "display_name": "Mini",
                    "student_capacity": 6,
                },
            }
        ],
        "route_rows": [{"cluster_id": "G01", "students": 5, "vehicle": "Mini"}],
    }
    return points, vehicle_pool, result


def test_fleet_adapter_preserves_normalized_solver_metrics() -> None:
    points, vehicle_pool, result = _fleet_state()
    shadow = fleet_shadow_from_normalized(
        points=points,
        vehicle_pool=vehicle_pool,
        result=result,
        market="CN",
        direction="to_school",
        max_route_duration_s=3600,
        max_stops_per_route=10,
    )

    assert shadow.parity_passed is True
    assert shadow.constraints_passed is True
    assert shadow.request.total_demand == 5
    assert shadow.request.total_available_capacity == 12
    assert shadow.candidate_plan.route_count == 1
    assert shadow.candidate_plan.routes[0].node_ids == (2, 1, 0)


def test_audit_adapter_preserves_status_and_exact_vehicle_target() -> None:
    points = [
        {"node_id": 0, "is_depot": True, "address": "School", "lat": 37.5, "lng": 127.0},
        {"node_id": 1, "address": "A", "lat": 37.51, "lng": 127.01, "passenger_count": 4},
        {"node_id": 2, "address": "B", "lat": 37.52, "lng": 127.02, "passenger_count": 3},
    ]
    result = {
        "bus_count": 1,
        "routes": [
            {
                "route_id": "Opt Bus 1",
                "nodes": [0, 1, 2],
                "load": 7,
                "bus_capacity": 10,
                "bus_type_name": "Mid Bus",
                "time_s": 1200,
                "distance_m": 8000,
            }
        ],
        "scenario_status": "rejected",
        "feasibility_report": {
            "status": "failed",
            "failure_reasons": ["time_impact"],
        },
    }
    config = SimpleNamespace(
        service_direction="From School",
        max_route_duration_minutes=60,
        route_stop_limit=10,
        comfort_load_factor=1.0,
        stop_service_minutes=1,
    )
    shadow = audit_shadow_from_legacy(
        points=points,
        bus_type_configs=[{"name": "Mid Bus", "capacity": 10, "max_count": 2}],
        result=result,
        config=config,
        attempted_vehicle_count=1,
        market="KR",
    )

    assert shadow.parity_passed is True
    assert shadow.constraints_passed is True
    assert shadow.candidate_plan.status == "rejected"
    assert shadow.candidate_plan.failure_reasons == ("time_impact",)
    assert shadow.request.constraints.exact_vehicle_count == 1


def test_audit_adapter_converts_retired_skipped_status_to_unresolved() -> None:
    points = [
        {"is_depot": True, "address": "School"},
        {"address": "A", "passenger_count": 1},
    ]
    config = SimpleNamespace(
        service_direction="To School",
        max_route_duration_minutes=60,
        route_stop_limit=None,
        comfort_load_factor=1.0,
        stop_service_minutes=0,
    )
    shadow = audit_shadow_from_legacy(
        points=points,
        bus_type_configs=[{"name": "Bus", "capacity": 10, "max_count": 1}],
        result={"scenario_status": "skipped", "routes": []},
        config=config,
        market="CN",
    )

    assert shadow.candidate_plan.status == "unresolved"
    assert "skipped" not in shadow.candidate_plan.to_dict().values()


def test_shared_evaluator_reports_coverage_direction_and_capacity() -> None:
    points, vehicle_pool, result = _fleet_state()
    shadow = fleet_shadow_from_normalized(
        points=points,
        vehicle_pool=vehicle_pool,
        result=result,
        market="CN",
        direction="to_school",
        max_route_duration_s=3600,
        max_stops_per_route=10,
    )
    broken = CandidatePlan(
        source="test",
        status="rejected",
        routes=(
            CandidateRoute(
                route_id="broken",
                vehicle_type="mini",
                node_ids=(0, 1, 1),
                load=5,
                capacity=4,
                duration_s=100,
                distance_m=10,
            ),
        ),
    )

    violations = evaluate_contract_constraints(shadow.request, broken)

    assert any("does not end at the school" in item for item in violations)
    assert any("duplicated service node" in item for item in violations)
    assert any("missing service node" in item for item in violations)
    assert any("exceeds effective capacity" in item for item in violations)
