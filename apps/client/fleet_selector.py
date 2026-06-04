from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planning_assumptions import PlanningAssumptions, get_planning_assumptions
from vehicle_catalog import get_vehicle_catalog


CATEGORY_RANK: dict[str, int] = {
    "van": 1,
    "mini_bus": 2,
    "mid_bus": 3,
    "large_bus": 4,
}


@dataclass(frozen=True)
class VehicleSelectionResult:
    rider_count: int
    selected_vehicle: dict[str, Any] | None
    feasible_options: list[dict[str, Any]]
    rejected_options: list[dict[str, Any]]
    assumptions: dict[str, Any]


def _vehicle_score(vehicle: dict[str, Any], rider_count: int, assumptions: PlanningAssumptions) -> float:
    capacity = int(vehicle.get("student_capacity", 0) or 0)
    if capacity <= 0:
        return float("inf")

    empty_seats = max(0, capacity - rider_count)
    load_factor = rider_count / capacity
    under_target_gap = max(0.0, assumptions.target_load_factor - load_factor)
    under_min_gap = max(0.0, assumptions.min_reasonable_load_factor - load_factor)
    category_rank = CATEGORY_RANK.get(str(vehicle.get("category", "")).strip(), 5)

    return (
        assumptions.weights.vehicle_count
        + empty_seats * assumptions.weights.empty_seat
        + under_target_gap * assumptions.weights.under_target_load
        + under_min_gap * assumptions.weights.under_min_load
        + category_rank * assumptions.weights.category_preference
    )


def _propulsion_priority(vehicle: dict[str, Any]) -> int:
    propulsion = str(vehicle.get("propulsion", "")).strip().lower()
    return 0 if propulsion == "diesel" else 1


def _explain_rejection(vehicle: dict[str, Any], rider_count: int, assumptions: PlanningAssumptions) -> str | None:
    capacity = int(vehicle.get("student_capacity", 0) or 0)
    category = str(vehicle.get("category", "")).strip()
    propulsion = str(vehicle.get("propulsion", "")).strip().lower()

    if capacity < rider_count:
        return f"capacity {capacity} is below rider count {rider_count}"
    if propulsion == "electric" and not assumptions.allow_electric:
        return "electric vehicles are disabled for this scenario"
    if category == "van" and not assumptions.allow_vans:
        return "vans are disabled for this scenario"
    return None


def select_vehicle_for_group(
    rider_count: int,
    *,
    market: str = "KR",
    mode: str = "balanced",
    monitor_seats: int | None = None,
    max_route_duration_minutes: int | None = None,
    custom_catalog: list[dict[str, Any]] | None = None,
    assumptions: PlanningAssumptions | None = None,
) -> VehicleSelectionResult:
    if rider_count <= 0:
        raise ValueError("rider_count must be greater than zero")

    active_assumptions = assumptions or get_planning_assumptions(
        market,
        mode=mode,
        monitor_seats=1 if monitor_seats is None else monitor_seats,
        max_route_duration_minutes=max_route_duration_minutes,
    )
    catalog = get_vehicle_catalog(
        active_assumptions.market,
        monitor_seats=active_assumptions.monitor_seats,
        custom_catalog=custom_catalog,
    )

    feasible_options: list[dict[str, Any]] = []
    rejected_options: list[dict[str, Any]] = []
    for vehicle in catalog:
        rejection_reason = _explain_rejection(vehicle, rider_count, active_assumptions)
        if rejection_reason:
            rejected = dict(vehicle)
            rejected["rejection_reason"] = rejection_reason
            rejected_options.append(rejected)
            continue

        scored_vehicle = dict(vehicle)
        capacity = int(scored_vehicle.get("student_capacity", 0) or 0)
        scored_vehicle["load_factor"] = rider_count / capacity if capacity else 0.0
        scored_vehicle["empty_seats"] = max(0, capacity - rider_count)
        scored_vehicle["selection_score"] = _vehicle_score(scored_vehicle, rider_count, active_assumptions)
        feasible_options.append(scored_vehicle)

    feasible_options.sort(
        key=lambda vehicle: (
            float(vehicle["selection_score"]),
            _propulsion_priority(vehicle),
            int(vehicle["student_capacity"]),
            str(vehicle["display_name"]).lower(),
        )
    )

    return VehicleSelectionResult(
        rider_count=rider_count,
        selected_vehicle=feasible_options[0] if feasible_options else None,
        feasible_options=feasible_options,
        rejected_options=rejected_options,
        assumptions=active_assumptions.to_dict(),
    )


def estimate_vehicle_mix_for_groups(
    rider_counts: list[int],
    *,
    market: str = "KR",
    mode: str = "balanced",
    monitor_seats: int = 1,
    max_route_duration_minutes: int | None = None,
    custom_catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assumptions = get_planning_assumptions(
        market,
        mode=mode,
        monitor_seats=monitor_seats,
        max_route_duration_minutes=max_route_duration_minutes,
    )
    selections = [
        select_vehicle_for_group(int(rider_count), assumptions=assumptions, custom_catalog=custom_catalog)
        for rider_count in rider_counts
        if int(rider_count) > 0
    ]
    mix: dict[str, int] = {}
    for selection in selections:
        if not selection.selected_vehicle:
            continue
        display_name = str(selection.selected_vehicle.get("display_name", "Unknown"))
        mix[display_name] = mix.get(display_name, 0) + 1

    return {
        "market": assumptions.market,
        "mode": assumptions.mode,
        "monitor_seats": assumptions.monitor_seats,
        "group_count": len(selections),
        "vehicle_mix": mix,
        "selections": [
            {
                "rider_count": selection.rider_count,
                "selected_vehicle": selection.selected_vehicle,
                "feasible_option_count": len(selection.feasible_options),
                "rejected_option_count": len(selection.rejected_options),
            }
            for selection in selections
        ],
    }
