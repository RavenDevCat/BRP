"""Canonical, versioned request/result boundary for BRP planning engines.

This module contains only transport and validation types, not solving logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


PLANNING_DIRECTIONS: tuple[str, ...] = ("to_school", "from_school")
CANDIDATE_STATUSES: tuple[str, ...] = (
    "passed",
    "rejected",
    "infeasible",
    "unresolved",
    "failed",
    "partial",
)
PLANNING_CONTRACT_VERSION = 1


def _require_supported_version(payload: Mapping[str, Any]) -> None:
    try:
        version = int(payload.get("contract_version", PLANNING_CONTRACT_VERSION))
    except (TypeError, ValueError) as exc:
        raise ValueError("Planning contract version must be an integer.") from exc
    if version != PLANNING_CONTRACT_VERSION:
        raise ValueError(f"Unsupported planning contract version: {version}")


def normalize_direction(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "toschool": "to_school",
        "to_school": "to_school",
        "fromschool": "from_school",
        "from_school": "from_school",
    }
    direction = aliases.get(normalized)
    if direction is None:
        raise ValueError(f"Unsupported planning direction: {value!r}")
    return direction


@dataclass(frozen=True)
class PlanningPoint:
    node_id: int
    kind: str
    address: str
    latitude: float
    longitude: float
    demand: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.node_id) < 0:
            raise ValueError("Planning point node_id must be non-negative.")
        if self.kind not in {"depot", "service"}:
            raise ValueError("Planning point kind must be 'depot' or 'service'.")
        if not str(self.address).strip():
            raise ValueError("Planning point address must not be blank.")
        if not -90.0 <= float(self.latitude) <= 90.0:
            raise ValueError("Planning point latitude is outside the valid range.")
        if not -180.0 <= float(self.longitude) <= 180.0:
            raise ValueError("Planning point longitude is outside the valid range.")
        if int(self.demand) < 0:
            raise ValueError("Planning point demand must be non-negative.")
        if self.kind == "depot" and int(self.demand) != 0:
            raise ValueError("Depot demand must be zero.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": int(self.node_id),
            "kind": self.kind,
            "address": self.address,
            "latitude": float(self.latitude),
            "longitude": float(self.longitude),
            "demand": int(self.demand),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PlanningPoint:
        return cls(
            node_id=int(payload["node_id"]),
            kind=str(payload["kind"]),
            address=str(payload["address"]),
            latitude=float(payload["latitude"]),
            longitude=float(payload["longitude"]),
            demand=int(payload.get("demand", 0) or 0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class VehicleOption:
    vehicle_type: str
    capacity: int
    available_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.vehicle_type).strip():
            raise ValueError("Vehicle type must not be blank.")
        if int(self.capacity) <= 0:
            raise ValueError("Vehicle capacity must be positive.")
        if int(self.available_count) < 0:
            raise ValueError("Vehicle available_count must be non-negative.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_type": self.vehicle_type,
            "capacity": int(self.capacity),
            "available_count": int(self.available_count),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VehicleOption:
        return cls(
            vehicle_type=str(payload["vehicle_type"]),
            capacity=int(payload["capacity"]),
            available_count=int(payload["available_count"]),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PlanningConstraints:
    max_route_duration_s: int | None = None
    max_stops_per_route: int | None = None
    comfort_load_factor: float = 1.0
    stop_dwell_s: int = 0
    exact_vehicle_count: int | None = None

    def __post_init__(self) -> None:
        if self.max_route_duration_s is not None and int(self.max_route_duration_s) <= 0:
            raise ValueError("max_route_duration_s must be positive when provided.")
        if self.max_stops_per_route is not None and int(self.max_stops_per_route) <= 0:
            raise ValueError("max_stops_per_route must be positive when provided.")
        if not 0.0 < float(self.comfort_load_factor) <= 1.0:
            raise ValueError("comfort_load_factor must be in the range (0, 1].")
        if int(self.stop_dwell_s) < 0:
            raise ValueError("stop_dwell_s must be non-negative.")
        if self.exact_vehicle_count is not None and int(self.exact_vehicle_count) <= 0:
            raise ValueError("exact_vehicle_count must be positive when provided.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_route_duration_s": self.max_route_duration_s,
            "max_stops_per_route": self.max_stops_per_route,
            "comfort_load_factor": float(self.comfort_load_factor),
            "stop_dwell_s": int(self.stop_dwell_s),
            "exact_vehicle_count": self.exact_vehicle_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PlanningConstraints:
        return cls(
            max_route_duration_s=(
                int(payload["max_route_duration_s"])
                if payload.get("max_route_duration_s") is not None
                else None
            ),
            max_stops_per_route=(
                int(payload["max_stops_per_route"])
                if payload.get("max_stops_per_route") is not None
                else None
            ),
            comfort_load_factor=float(payload.get("comfort_load_factor", 1.0)),
            stop_dwell_s=int(payload.get("stop_dwell_s", 0) or 0),
            exact_vehicle_count=(
                int(payload["exact_vehicle_count"])
                if payload.get("exact_vehicle_count") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PlanningRequest:
    market: str
    direction: str
    points: tuple[PlanningPoint, ...]
    vehicles: tuple[VehicleOption, ...]
    constraints: PlanningConstraints
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_market = str(self.market or "").strip().upper()
        if not normalized_market:
            raise ValueError("Planning market must not be blank.")
        object.__setattr__(self, "market", normalized_market)
        object.__setattr__(self, "direction", normalize_direction(self.direction))
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "vehicles", tuple(self.vehicles))
        object.__setattr__(self, "metadata", dict(self.metadata))

        if len({point.node_id for point in self.points}) != len(self.points):
            raise ValueError("Planning point node_id values must be unique.")
        depots = [point for point in self.points if point.kind == "depot"]
        services = [point for point in self.points if point.kind == "service"]
        if len(depots) != 1:
            raise ValueError("A planning request must contain exactly one depot.")
        if not services:
            raise ValueError("A planning request must contain at least one service point.")
        if not self.vehicles or self.total_available_capacity <= 0:
            raise ValueError("A planning request must contain available vehicle capacity.")

    @property
    def depot(self) -> PlanningPoint:
        return next(point for point in self.points if point.kind == "depot")

    @property
    def service_points(self) -> tuple[PlanningPoint, ...]:
        return tuple(point for point in self.points if point.kind == "service")

    @property
    def total_demand(self) -> int:
        return sum(point.demand for point in self.service_points)

    @property
    def total_available_capacity(self) -> int:
        return sum(vehicle.capacity * vehicle.available_count for vehicle in self.vehicles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": PLANNING_CONTRACT_VERSION,
            "market": self.market,
            "direction": self.direction,
            "points": [point.to_dict() for point in self.points],
            "vehicles": [vehicle.to_dict() for vehicle in self.vehicles],
            "constraints": self.constraints.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PlanningRequest:
        _require_supported_version(payload)
        return cls(
            market=str(payload["market"]),
            direction=str(payload["direction"]),
            points=tuple(PlanningPoint.from_dict(point) for point in payload["points"]),
            vehicles=tuple(VehicleOption.from_dict(vehicle) for vehicle in payload["vehicles"]),
            constraints=PlanningConstraints.from_dict(payload.get("constraints") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidateRoute:
    route_id: str
    vehicle_type: str
    node_ids: tuple[int, ...]
    load: int
    capacity: int
    duration_s: int
    distance_m: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.route_id).strip():
            raise ValueError("Candidate route_id must not be blank.")
        if not str(self.vehicle_type).strip():
            raise ValueError("Candidate route vehicle_type must not be blank.")
        object.__setattr__(self, "node_ids", tuple(int(node_id) for node_id in self.node_ids))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if int(self.load) < 0 or int(self.capacity) <= 0:
            raise ValueError("Candidate route load/capacity values are invalid.")
        if int(self.duration_s) < 0 or float(self.distance_m) < 0:
            raise ValueError("Candidate route duration/distance values must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "vehicle_type": self.vehicle_type,
            "node_ids": list(self.node_ids),
            "load": int(self.load),
            "capacity": int(self.capacity),
            "duration_s": int(self.duration_s),
            "distance_m": float(self.distance_m),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CandidateRoute:
        return cls(
            route_id=str(payload["route_id"]),
            vehicle_type=str(payload["vehicle_type"]),
            node_ids=tuple(int(node_id) for node_id in payload.get("node_ids") or ()),
            load=int(payload.get("load", 0) or 0),
            capacity=int(payload["capacity"]),
            duration_s=int(payload.get("duration_s", 0) or 0),
            distance_m=float(payload.get("distance_m", 0.0) or 0.0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidatePlan:
    source: str
    status: str
    routes: tuple[CandidateRoute, ...]
    attempted_vehicle_count: int | None = None
    failure_reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise ValueError("Candidate plan source must not be blank.")
        normalized_status = str(self.status or "").strip().lower()
        if normalized_status not in CANDIDATE_STATUSES:
            raise ValueError(f"Unsupported candidate status: {self.status!r}")
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "routes", tuple(self.routes))
        object.__setattr__(
            self,
            "failure_reasons",
            tuple(str(reason).strip() for reason in self.failure_reasons if str(reason).strip()),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))
        if len({route.route_id for route in self.routes}) != len(self.routes):
            raise ValueError("Candidate plan route_id values must be unique.")
        if self.attempted_vehicle_count is not None and int(self.attempted_vehicle_count) < 0:
            raise ValueError("attempted_vehicle_count must be non-negative when provided.")

    @property
    def route_count(self) -> int:
        return len(self.routes)

    @property
    def total_load(self) -> int:
        return sum(route.load for route in self.routes)

    @property
    def total_duration_s(self) -> int:
        return sum(route.duration_s for route in self.routes)

    @property
    def total_distance_m(self) -> float:
        return sum(route.distance_m for route in self.routes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": PLANNING_CONTRACT_VERSION,
            "source": self.source,
            "status": self.status,
            "routes": [route.to_dict() for route in self.routes],
            "attempted_vehicle_count": self.attempted_vehicle_count,
            "failure_reasons": list(self.failure_reasons),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CandidatePlan:
        _require_supported_version(payload)
        return cls(
            source=str(payload["source"]),
            status=str(payload["status"]),
            routes=tuple(CandidateRoute.from_dict(route) for route in payload.get("routes") or ()),
            attempted_vehicle_count=(
                int(payload["attempted_vehicle_count"])
                if payload.get("attempted_vehicle_count") is not None
                else None
            ),
            failure_reasons=tuple(str(reason) for reason in payload.get("failure_reasons") or ()),
            metadata=dict(payload.get("metadata") or {}),
        )
