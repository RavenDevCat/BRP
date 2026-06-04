from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from vehicle_catalog import DEFAULT_MONITOR_SEATS


PLANNING_MODES: tuple[str, ...] = ("balanced", "cost_saver", "comfort_saver")


@dataclass(frozen=True)
class PlanningWeights:
    vehicle_count: float
    empty_seat: float
    under_target_load: float
    under_min_load: float
    category_preference: float


@dataclass(frozen=True)
class PlanningAssumptions:
    market: str = "KR"
    mode: str = "balanced"
    monitor_seats: int = DEFAULT_MONITOR_SEATS
    max_route_duration_minutes: int = 60
    max_stops_per_route: int = 12
    target_load_factor: float = 0.82
    min_reasonable_load_factor: float = 0.55
    allow_electric: bool = True
    allow_vans: bool = True
    default_max_vehicles_per_type: int = 20
    weights: PlanningWeights = field(
        default_factory=lambda: PlanningWeights(
            vehicle_count=1000.0,
            empty_seat=12.0,
            under_target_load=180.0,
            under_min_load=360.0,
            category_preference=20.0,
        )
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["weights"] = asdict(self.weights)
        return payload


def normalize_planning_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower().replace(" ", "_").replace("-", "_")
    return normalized if normalized in PLANNING_MODES else "balanced"


def normalize_market(market: str | None) -> str:
    normalized = str(market or "").strip().upper()
    if normalized in {"CN", "CHINA"}:
        return "CN"
    return "KR"


def get_planning_assumptions(
    market: str = "KR",
    *,
    mode: str = "balanced",
    monitor_seats: int = DEFAULT_MONITOR_SEATS,
    max_route_duration_minutes: int | None = None,
    allow_electric: bool = True,
    allow_vans: bool = True,
) -> PlanningAssumptions:
    normalized_market = normalize_market(market)
    normalized_mode = normalize_planning_mode(mode)
    reserved_monitor_seats = max(0, int(monitor_seats))
    route_time_override = int(max_route_duration_minutes or 0)

    if normalized_mode == "cost_saver":
        assumptions = PlanningAssumptions(
            market=normalized_market,
            mode=normalized_mode,
            monitor_seats=reserved_monitor_seats,
            max_route_duration_minutes=70,
            max_stops_per_route=14,
            target_load_factor=0.88,
            min_reasonable_load_factor=0.62,
            allow_electric=allow_electric,
            allow_vans=allow_vans,
            default_max_vehicles_per_type=20,
            weights=PlanningWeights(
                vehicle_count=1300.0,
                empty_seat=18.0,
                under_target_load=260.0,
                under_min_load=520.0,
                category_preference=16.0,
            ),
        )
        return replace(assumptions, max_route_duration_minutes=route_time_override) if route_time_override > 0 else assumptions

    if normalized_mode == "comfort_saver":
        assumptions = PlanningAssumptions(
            market=normalized_market,
            mode=normalized_mode,
            monitor_seats=reserved_monitor_seats,
            max_route_duration_minutes=45,
            max_stops_per_route=8,
            target_load_factor=0.72,
            min_reasonable_load_factor=0.42,
            allow_electric=allow_electric,
            allow_vans=allow_vans,
            default_max_vehicles_per_type=24,
            weights=PlanningWeights(
                vehicle_count=760.0,
                empty_seat=7.0,
                under_target_load=90.0,
                under_min_load=180.0,
                category_preference=24.0,
            ),
        )
        return replace(assumptions, max_route_duration_minutes=route_time_override) if route_time_override > 0 else assumptions

    assumptions = PlanningAssumptions(
        market=normalized_market,
        mode=normalized_mode,
        monitor_seats=reserved_monitor_seats,
        max_route_duration_minutes=60,
        max_stops_per_route=12,
        target_load_factor=0.82,
        min_reasonable_load_factor=0.55,
        allow_electric=allow_electric,
        allow_vans=allow_vans,
        default_max_vehicles_per_type=20,
    )
    return replace(assumptions, max_route_duration_minutes=route_time_override) if route_time_override > 0 else assumptions


def get_default_planning_profiles(market: str = "KR") -> dict[str, dict[str, Any]]:
    return {
        mode: get_planning_assumptions(market, mode=mode).to_dict()
        for mode in PLANNING_MODES
    }
