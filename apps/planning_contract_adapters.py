"""Pure adapters and parity checks for BRP's planning contract shadow path.

The functions in this module normalize already-computed solver inputs and
outputs. They never geocode, build routing matrices, call traffic providers,
or mutate the legacy result dictionaries.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Mapping, Sequence

try:
    from planning_contract import (
        CANDIDATE_STATUSES,
        CandidatePlan,
        CandidateRoute,
        PlanningConstraints,
        PlanningPoint,
        PlanningRequest,
        VehicleOption,
        normalize_direction,
    )
except ImportError:  # pragma: no cover - package import used by backend tests.
    from apps.planning_contract import (
        CANDIDATE_STATUSES,
        CandidatePlan,
        CandidateRoute,
        PlanningConstraints,
        PlanningPoint,
        PlanningRequest,
        VehicleOption,
        normalize_direction,
    )


LOGGER = logging.getLogger("brp.planning_contract.shadow")
SHADOW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlanningContractShadow:
    """A normalized request/plan pair plus non-invasive parity evidence."""

    request: PlanningRequest
    candidate_plan: CandidatePlan
    source_metrics: Mapping[str, Any]
    normalized_metrics: Mapping[str, Any]
    parity_violations: tuple[str, ...] = ()
    constraint_violations: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_metrics", dict(self.source_metrics))
        object.__setattr__(self, "normalized_metrics", dict(self.normalized_metrics))
        object.__setattr__(self, "parity_violations", tuple(self.parity_violations))
        object.__setattr__(self, "constraint_violations", tuple(self.constraint_violations))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def parity_passed(self) -> bool:
        return not self.parity_violations

    @property
    def constraints_passed(self) -> bool:
        return not self.constraint_violations

    def summary(self) -> dict[str, Any]:
        return {
            "shadow_schema_version": SHADOW_SCHEMA_VERSION,
            "source": self.candidate_plan.source,
            "status": self.candidate_plan.status,
            "parity_passed": self.parity_passed,
            "constraints_passed": self.constraints_passed,
            "source_metrics": dict(self.source_metrics),
            "normalized_metrics": dict(self.normalized_metrics),
            "parity_violations": list(self.parity_violations),
            "constraint_violations": list(self.constraint_violations),
            "metadata": dict(self.metadata),
        }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(*values: Any, default: str = "") -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return default


def _coordinates(point: Mapping[str, Any]) -> tuple[float, float]:
    for latitude_key, longitude_key in (
        ("lat", "lng"),
        ("latitude", "longitude"),
        ("plot_lat", "plot_lng"),
    ):
        if point.get(latitude_key) is None or point.get(longitude_key) is None:
            continue
        latitude = _float(point.get(latitude_key))
        longitude = _float(point.get(longitude_key))
        if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
            return latitude, longitude
    return 0.0, 0.0


def _address(point: Mapping[str, Any], index: int) -> str:
    return _text(
        point.get("display_address"),
        point.get("formatted_address"),
        point.get("address"),
        point.get("requested_address"),
        default=f"Planning node {index}",
    )


def _vehicle_type(vehicle: Mapping[str, Any], fallback: str) -> str:
    return _text(
        vehicle.get("vehicle_type"),
        vehicle.get("bus_type_name"),
        vehicle.get("display_name"),
        vehicle.get("name"),
        fallback,
        default="vehicle",
    )


def _infer_market(points: Sequence[Mapping[str, Any]], fallback: str = "UNKNOWN") -> str:
    values = " ".join(
        _text(point.get("country"), point.get("provider"), point.get("city"))
        for point in points
    ).upper()
    if any(token in values for token in ("CHINA", "中国", "AMAP")):
        return "CN"
    if any(token in values for token in ("KOREA", "韩国", "한국", "KAKAO")):
        return "KR"
    if any(token in values for token in ("THAILAND", "泰国", "BANGKOK")):
        return "TH"
    return _text(fallback, default="UNKNOWN").upper()


def _points_from_normalized(
    points: Sequence[Mapping[str, Any]],
    *,
    depot_index: int,
    demand_key: str,
    source: str,
) -> tuple[PlanningPoint, ...]:
    normalized: list[PlanningPoint] = []
    for index, raw_point in enumerate(points):
        point = dict(raw_point or {})
        latitude, longitude = _coordinates(point)
        normalized.append(
            PlanningPoint(
                node_id=index,
                kind="depot" if index == depot_index else "service",
                address=_address(point, index),
                latitude=latitude,
                longitude=longitude,
                demand=0 if index == depot_index else max(0, _int(point.get(demand_key))),
                metadata={
                    "source": source,
                    "source_node_id": point.get("node_id"),
                    "coordinate_missing": latitude == 0.0 and longitude == 0.0,
                },
            )
        )
    return tuple(normalized)


def _fleet_vehicle_options(vehicle_pool: Sequence[Mapping[str, Any]]) -> tuple[VehicleOption, ...]:
    grouped: Counter[tuple[str, int, str]] = Counter()
    for index, raw_vehicle in enumerate(vehicle_pool):
        vehicle = dict(raw_vehicle or {})
        capacity = max(0, _int(vehicle.get("capacity") or vehicle.get("student_capacity")))
        if capacity <= 0:
            continue
        vehicle_type = _vehicle_type(vehicle, f"fleet_vehicle_{index + 1}")
        category = _text(vehicle.get("category"), default="unknown")
        grouped[(vehicle_type, capacity, category)] += 1
    return tuple(
        VehicleOption(
            vehicle_type=vehicle_type,
            capacity=capacity,
            available_count=count,
            metadata={"category": category, "source": "fleet_planner"},
        )
        for (vehicle_type, capacity, category), count in sorted(grouped.items())
    )


def _audit_vehicle_options(bus_type_configs: Sequence[Mapping[str, Any]]) -> tuple[VehicleOption, ...]:
    options: list[VehicleOption] = []
    for index, raw_config in enumerate(bus_type_configs):
        config = dict(raw_config or {})
        capacity = max(0, _int(config.get("capacity") or config.get("student_capacity")))
        available_count = max(0, _int(config.get("max_count") or config.get("available_count")))
        if capacity <= 0 or available_count <= 0:
            continue
        options.append(
            VehicleOption(
                vehicle_type=_vehicle_type(config, f"audit_vehicle_{index + 1}"),
                capacity=capacity,
                available_count=available_count,
                metadata={"source": "route_audit"},
            )
        )
    return tuple(options)


def _fleet_candidate_routes(result: Mapping[str, Any]) -> tuple[CandidateRoute, ...]:
    rows_by_id = {
        _text(row.get("cluster_id")): dict(row)
        for row in list(result.get("route_rows") or [])
    }
    routes: list[CandidateRoute] = []
    for index, raw_route in enumerate(list(result.get("routes") or []), start=1):
        route = dict(raw_route or {})
        route_id = _text(route.get("cluster_id"), route.get("route_id"), default=f"G{index:02d}")
        row = rows_by_id.get(route_id, {})
        selected_vehicle = dict(route.get("selected_vehicle") or {})
        capacity = max(
            1,
            _int(selected_vehicle.get("student_capacity") or selected_vehicle.get("capacity"), 1),
        )
        ordered_points = list(route.get("ordered_points") or [])
        load = _int(row.get("students"))
        if load <= 0:
            load = sum(max(0, _int(dict(point or {}).get("student_count"))) for point in ordered_points)
        routes.append(
            CandidateRoute(
                route_id=route_id,
                vehicle_type=_vehicle_type(selected_vehicle, _text(row.get("vehicle"), default="vehicle")),
                node_ids=tuple(_int(node) for node in list(route.get("order") or [])),
                load=max(0, load),
                capacity=capacity,
                duration_s=max(0, _int(round(_float(route.get("duration_s"))))),
                distance_m=max(0.0, _float(route.get("distance_m"))),
                metadata={"source_solver": _text(route.get("solver"), default="global_ortools")},
            )
        )
    return tuple(routes)


def _canonical_audit_status(result: Mapping[str, Any]) -> str:
    explicit = _text(result.get("scenario_status")).lower()
    if explicit in CANDIDATE_STATUSES:
        return explicit
    if explicit == "skipped":
        return "unresolved"
    report_status = _text(dict(result.get("feasibility_report") or {}).get("status")).lower()
    if report_status == "passed":
        return "passed"
    if report_status == "failed":
        return "rejected"
    gate_status = _text(dict(result.get("traffic_gate") or {}).get("status")).lower()
    if gate_status in {"unavailable", "unresolved"}:
        return "unresolved"
    return "passed" if list(result.get("routes") or []) else "infeasible"


def _audit_candidate_routes(result: Mapping[str, Any]) -> tuple[CandidateRoute, ...]:
    routes: list[CandidateRoute] = []
    for index, raw_route in enumerate(list(result.get("routes") or []), start=1):
        route = dict(raw_route or {})
        capacity = max(
            1,
            _int(
                route.get("bus_capacity")
                or route.get("capacity")
                or route.get("comfort_capacity"),
                1,
            ),
        )
        routes.append(
            CandidateRoute(
                route_id=_text(route.get("route_id"), route.get("id"), default=f"Bus {index}"),
                vehicle_type=_vehicle_type(route, f"audit_vehicle_{index}"),
                node_ids=tuple(_int(node) for node in list(route.get("nodes") or [])),
                load=max(0, _int(route.get("load") or route.get("passenger_count"))),
                capacity=capacity,
                duration_s=max(0, _int(round(_float(route.get("time_s") or route.get("duration_s"))))),
                distance_m=max(0.0, _float(route.get("distance_m"))),
                metadata={
                    "exception_role": _text(route.get("exception_role")),
                    "display_role": _text(route.get("display_role")),
                },
            )
        )
    return tuple(routes)


def _candidate_metrics(request: PlanningRequest, plan: CandidatePlan) -> dict[str, Any]:
    return {
        "point_count": len(request.points),
        "service_point_count": len(request.service_points),
        "total_demand": request.total_demand,
        "route_count": plan.route_count,
        "total_load": plan.total_load,
        "total_duration_s": plan.total_duration_s,
        "total_distance_m": plan.total_distance_m,
        "direction": request.direction,
        "market": request.market,
        "status": plan.status,
    }


def _metric_parity_violations(
    source_metrics: Mapping[str, Any], normalized_metrics: Mapping[str, Any]
) -> tuple[str, ...]:
    violations: list[str] = []
    for key, expected in source_metrics.items():
        if key not in normalized_metrics:
            violations.append(f"missing normalized metric: {key}")
            continue
        actual = normalized_metrics[key]
        if isinstance(expected, float) or isinstance(actual, float):
            if not math.isclose(_float(expected), _float(actual), rel_tol=1e-9, abs_tol=0.5):
                violations.append(f"metric {key}: source={expected!r}, normalized={actual!r}")
        elif expected != actual:
            violations.append(f"metric {key}: source={expected!r}, normalized={actual!r}")
    return tuple(violations)


def evaluate_contract_constraints(
    request: PlanningRequest, plan: CandidatePlan
) -> tuple[str, ...]:
    """Evaluate only constraints represented by the shared planning contract."""

    violations: list[str] = []
    point_by_id = {point.node_id: point for point in request.points}
    depot_id = request.depot.node_id
    service_ids = {point.node_id for point in request.service_points}
    served_service_ids: list[int] = []
    vehicle_use: Counter[tuple[str, int]] = Counter()

    for route in plan.routes:
        unknown = sorted(set(route.node_ids) - set(point_by_id))
        if unknown:
            violations.append(f"route {route.route_id} has unknown node(s): {unknown}")
        depot_occurrences = route.node_ids.count(depot_id)
        if depot_occurrences != 1:
            violations.append(
                f"route {route.route_id} contains depot {depot_occurrences} time(s)"
            )
        elif request.direction == "to_school" and route.node_ids[-1] != depot_id:
            violations.append(f"route {route.route_id} does not end at the school")
        elif request.direction == "from_school" and route.node_ids[0] != depot_id:
            violations.append(f"route {route.route_id} does not start at the school")

        route_service_ids = [node_id for node_id in route.node_ids if node_id in service_ids]
        served_service_ids.extend(route_service_ids)
        expected_load = sum(point_by_id[node_id].demand for node_id in route_service_ids)
        if route.load != expected_load:
            violations.append(
                f"route {route.route_id} load {route.load} != node demand {expected_load}"
            )
        effective_capacity = math.floor(
            route.capacity * request.constraints.comfort_load_factor + 1e-9
        )
        if route.load > effective_capacity:
            violations.append(
                f"route {route.route_id} load {route.load} exceeds effective capacity {effective_capacity}"
            )
        if (
            request.constraints.max_stops_per_route is not None
            and len(route_service_ids) > request.constraints.max_stops_per_route
        ):
            violations.append(
                f"route {route.route_id} has {len(route_service_ids)} service stops, "
                f"limit {request.constraints.max_stops_per_route}"
            )
        if (
            request.constraints.max_route_duration_s is not None
            and route.duration_s > request.constraints.max_route_duration_s
        ):
            violations.append(
                f"route {route.route_id} duration {route.duration_s}s exceeds "
                f"{request.constraints.max_route_duration_s}s"
            )
        vehicle_use[(route.vehicle_type, route.capacity)] += 1

    counts = Counter(served_service_ids)
    missing = sorted(service_ids - set(counts))
    duplicated = sorted(node_id for node_id, count in counts.items() if count > 1)
    if missing:
        violations.append(f"missing service node(s): {missing}")
    if duplicated:
        violations.append(f"duplicated service node(s): {duplicated}")
    if plan.total_load != request.total_demand:
        violations.append(
            f"candidate total load {plan.total_load} != request demand {request.total_demand}"
        )
    if (
        request.constraints.exact_vehicle_count is not None
        and plan.route_count != request.constraints.exact_vehicle_count
    ):
        violations.append(
            f"candidate route count {plan.route_count} != exact vehicle count "
            f"{request.constraints.exact_vehicle_count}"
        )

    available = Counter(
        {
            (vehicle.vehicle_type, vehicle.capacity): vehicle.available_count
            for vehicle in request.vehicles
        }
    )
    for key, used_count in vehicle_use.items():
        if key in available and used_count > available[key]:
            violations.append(
                f"vehicle {key[0]} ({key[1]} seats) used {used_count}, available {available[key]}"
            )
    return tuple(violations)


def _build_shadow(
    request: PlanningRequest,
    candidate_plan: CandidatePlan,
    source_metrics: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> PlanningContractShadow:
    normalized_metrics = _candidate_metrics(request, candidate_plan)
    return PlanningContractShadow(
        request=request,
        candidate_plan=candidate_plan,
        source_metrics=source_metrics,
        normalized_metrics=normalized_metrics,
        parity_violations=_metric_parity_violations(source_metrics, normalized_metrics),
        constraint_violations=evaluate_contract_constraints(request, candidate_plan),
        metadata=dict(metadata or {}),
    )


def fleet_shadow_from_normalized(
    *,
    points: Sequence[Mapping[str, Any]],
    vehicle_pool: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    market: str,
    direction: str,
    max_route_duration_s: int,
    max_stops_per_route: int,
) -> PlanningContractShadow:
    """Normalize Fleet Planner's already-routed state without calling providers."""

    normalized_points = _points_from_normalized(
        points,
        depot_index=0,
        demand_key="student_count",
        source="fleet_planner",
    )
    request = PlanningRequest(
        market=market,
        direction=direction,
        points=normalized_points,
        vehicles=_fleet_vehicle_options(vehicle_pool),
        constraints=PlanningConstraints(
            max_route_duration_s=max(1, _int(max_route_duration_s)),
            max_stops_per_route=max(1, _int(max_stops_per_route)),
        ),
        metadata={"source": "fleet_planner_legacy"},
    )
    routes = _fleet_candidate_routes(result)
    candidate = CandidatePlan(
        source="fleet_planner_legacy",
        status="passed",
        routes=routes,
        attempted_vehicle_count=len(vehicle_pool),
        metadata={"source_solver": "global_ortools"},
    )
    source_metrics = {
        "point_count": len(points),
        "service_point_count": max(0, len(points) - 1),
        "total_demand": sum(max(0, _int(point.get("student_count"))) for point in points[1:]),
        "route_count": len(list(result.get("routes") or [])),
        "total_load": sum(route.load for route in routes),
        "total_duration_s": sum(route.duration_s for route in routes),
        "total_distance_m": sum(route.distance_m for route in routes),
        "direction": normalize_direction(direction),
        "market": _text(market, default="UNKNOWN").upper(),
        "status": "passed",
    }
    return _build_shadow(
        request,
        candidate,
        source_metrics,
        metadata={"adapter": "fleet_normalized_v1"},
    )


def audit_shadow_from_legacy(
    *,
    points: Sequence[Mapping[str, Any]],
    bus_type_configs: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    config: Any,
    attempted_vehicle_count: int | None = None,
    market: str | None = None,
) -> PlanningContractShadow:
    """Normalize an Audit exact-N candidate after its existing gate checks."""

    source_points = [dict(point or {}) for point in points]
    depot_indices = [
        index for index, point in enumerate(source_points) if bool(point.get("is_depot"))
    ]
    depot_index = depot_indices[0] if len(depot_indices) == 1 else 0
    normalized_points = _points_from_normalized(
        source_points,
        depot_index=depot_index,
        demand_key="passenger_count",
        source="route_audit",
    )
    status = _canonical_audit_status(result)
    report = dict(result.get("feasibility_report") or {})
    failure_reasons = list(report.get("failure_reasons") or [])
    for key in ("infeasible_reason", "unresolved_reason"):
        reason = _text(result.get(key))
        if reason and reason not in failure_reasons:
            failure_reasons.append(reason)
    exact_vehicle_count = (
        max(1, _int(attempted_vehicle_count))
        if attempted_vehicle_count is not None and _int(attempted_vehicle_count) > 0
        else None
    )
    request = PlanningRequest(
        market=_text(market, default=_infer_market(source_points)),
        direction=_text(getattr(config, "service_direction", None), default="from_school"),
        points=normalized_points,
        vehicles=_audit_vehicle_options(bus_type_configs),
        constraints=PlanningConstraints(
            max_route_duration_s=max(
                1, _int(round(_float(getattr(config, "max_route_duration_minutes", 60)) * 60))
            ),
            max_stops_per_route=(
                max(1, _int(getattr(config, "route_stop_limit", None)))
                if getattr(config, "route_stop_limit", None) not in (None, "", 0)
                else None
            ),
            comfort_load_factor=min(
                1.0, max(0.01, _float(getattr(config, "comfort_load_factor", 1.0), 1.0))
            ),
            stop_dwell_s=max(
                0, _int(round(_float(getattr(config, "stop_service_minutes", 0)) * 60))
            ),
            exact_vehicle_count=exact_vehicle_count,
        ),
        metadata={"source": "route_audit_legacy"},
    )
    routes = _audit_candidate_routes(result)
    candidate = CandidatePlan(
        source="route_audit_legacy",
        status=status,
        routes=routes,
        attempted_vehicle_count=exact_vehicle_count,
        failure_reasons=tuple(failure_reasons),
        metadata={"scenario_label": _text(result.get("scenario_label"))},
    )
    source_metrics = {
        "point_count": len(source_points),
        "service_point_count": sum(
            1 for point in source_points if not bool(point.get("is_depot"))
        ),
        "total_demand": sum(
            max(0, _int(point.get("passenger_count")))
            for point in source_points
            if not bool(point.get("is_depot"))
        ),
        "route_count": _int(result.get("bus_count"), len(list(result.get("routes") or []))),
        "total_load": sum(route.load for route in routes),
        "total_duration_s": sum(route.duration_s for route in routes),
        "total_distance_m": sum(route.distance_m for route in routes),
        "direction": normalize_direction(
            _text(getattr(config, "service_direction", None), default="from_school")
        ),
        "market": request.market,
        "status": status,
    }
    return _build_shadow(
        request,
        candidate,
        source_metrics,
        metadata={"adapter": "audit_legacy_v1"},
    )


def observe_planning_shadow(
    shadow: PlanningContractShadow,
    *,
    logger: logging.Logger | None = None,
) -> PlanningContractShadow:
    """Emit an operational warning only when normalization parity diverges."""

    active_logger = logger or LOGGER
    if not shadow.parity_passed:
        active_logger.warning(
            "Planning contract shadow parity mismatch: %s",
            shadow.summary(),
        )
    return shadow
