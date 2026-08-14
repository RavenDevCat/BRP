from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = ROOT / "apps"
if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from planning_contract import (  # noqa: E402
    CandidatePlan,
    PlanningConstraints,
    PlanningPoint,
    PlanningRequest,
    VehicleOption,
    normalize_direction,
)


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "planning_contract"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_name", ["cn_to_school.json", "kr_from_school.json"])
def test_planning_contract_snapshots_round_trip(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    request = PlanningRequest.from_dict(fixture["request"])
    candidate = CandidatePlan.from_dict(fixture["candidate_plan"])
    expected = fixture["expected"]

    assert request.to_dict() == fixture["request"]
    assert candidate.to_dict() == fixture["candidate_plan"]
    assert len(request.service_points) == expected["service_point_count"]
    assert request.total_demand == expected["total_demand"]
    assert request.total_available_capacity == expected["total_available_capacity"]
    assert candidate.route_count == expected["candidate_route_count"]
    assert candidate.total_load == expected["candidate_total_load"]
    assert candidate.total_duration_s == expected["candidate_total_duration_s"]
    assert candidate.total_distance_m == expected["candidate_total_distance_m"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("To School", "to_school"),
        ("to-school", "to_school"),
        ("From School", "from_school"),
        ("fromschool", "from_school"),
    ],
)
def test_direction_aliases_are_canonical(raw: str, expected: str) -> None:
    assert normalize_direction(raw) == expected


def test_request_rejects_duplicate_nodes_and_missing_capacity() -> None:
    depot = PlanningPoint(0, "depot", "School", 31.2, 121.4)
    service = PlanningPoint(1, "service", "Stop", 31.3, 121.5, demand=1)
    constraints = PlanningConstraints()

    with pytest.raises(ValueError, match="unique"):
        PlanningRequest(
            market="CN",
            direction="to_school",
            points=(depot, service, service),
            vehicles=(VehicleOption("bus", 10, 1),),
            constraints=constraints,
        )

    with pytest.raises(ValueError, match="capacity"):
        PlanningRequest(
            market="CN",
            direction="to_school",
            points=(depot, service),
            vehicles=(VehicleOption("bus", 10, 0),),
            constraints=constraints,
        )


def test_contract_rejects_invalid_constraints_and_retired_status() -> None:
    with pytest.raises(ValueError, match="comfort_load_factor"):
        PlanningConstraints(comfort_load_factor=1.1)

    with pytest.raises(ValueError, match="Unsupported candidate status"):
        CandidatePlan(source="legacy", status="skipped", routes=())


@pytest.mark.parametrize("status", ["passed", "rejected", "infeasible", "unresolved"])
def test_contract_preserves_current_audit_outcomes(status: str) -> None:
    assert CandidatePlan(source="route_audit", status=status, routes=()).status == status


def test_contract_rejects_unknown_serialized_version() -> None:
    payload = _load_fixture("cn_to_school.json")["request"]
    payload["contract_version"] = 2

    with pytest.raises(ValueError, match="Unsupported planning contract version"):
        PlanningRequest.from_dict(payload)
