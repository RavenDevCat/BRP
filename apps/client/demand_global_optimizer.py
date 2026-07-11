from __future__ import annotations

import math
from typing import Any

from demand_routing import (
    _annotate_ordered_points_with_schedule,
    _build_osrm_matrix,
    _point_payload,
    _route_leg_details_for_order,
    _route_metrics_for_order,
)
from planning_assumptions import PlanningAssumptions, get_planning_assumptions
from vehicle_catalog import get_vehicle_catalog


HUGE_COST = 10**9


def _vehicle_category_rank(vehicle: dict[str, Any]) -> int:
    return {
        "van": 1,
        "mini_bus": 2,
        "mid_bus": 3,
        "large_bus": 4,
    }.get(str(vehicle.get("category", "")).strip(), 5)


def _vehicle_fixed_cost(vehicle: dict[str, Any], assumptions: PlanningAssumptions) -> int:
    category = str(vehicle.get("category", "")).strip()
    propulsion = str(vehicle.get("propulsion", "")).strip().lower()
    capacity = int(vehicle.get("student_capacity", 0) or 0)
    category_cost = {
        "van": 14_000,
        "mini_bus": 22_000,
        "mid_bus": 30_000,
        "large_bus": 38_000,
    }.get(category, 45_000)
    mode_base = {
        "cost_saver": 105_000,
        "balanced": 62_000,
        "comfort_saver": 28_000,
    }.get(assumptions.mode, 62_000)
    electric_cost = 5_000 if propulsion == "electric" else 0
    capacity_cost = int(capacity * (90 if assumptions.mode == "comfort_saver" else 55))
    return mode_base + category_cost + electric_cost + capacity_cost


def _candidate_vehicle_pool(
    total_students: int,
    assumptions: PlanningAssumptions,
    custom_catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    vehicles = []
    for vehicle in get_vehicle_catalog(
        assumptions.market,
        monitor_seats=assumptions.monitor_seats,
        custom_catalog=custom_catalog,
    ):
        capacity = int(vehicle.get("student_capacity", 0) or 0)
        if capacity <= 0:
            continue
        if str(vehicle.get("category", "")).strip() == "van" and not assumptions.allow_vans:
            continue
        if str(vehicle.get("propulsion", "")).strip().lower() == "electric" and not assumptions.allow_electric:
            continue
        available_count = int(vehicle.get("available_count", assumptions.default_max_vehicles_per_type) or 0)
        if available_count <= 0:
            continue
        count = min(
            available_count,
            int(assumptions.default_max_vehicles_per_type),
            max(1, math.ceil(max(1, total_students) / capacity) + 2),
        )
        for index in range(count):
            vehicles.append(
                {
                    **vehicle,
                    "vehicle_index": index + 1,
                    "capacity": capacity,
                    "fixed_cost": _vehicle_fixed_cost(vehicle, assumptions),
                    "category_rank": _vehicle_category_rank(vehicle),
                }
            )
    vehicles.sort(
        key=lambda item: (
            int(item["fixed_cost"]),
            int(item["capacity"]),
            str(item.get("display_name", "")).lower(),
        )
    )
    return vehicles


def _expand_large_demands(
    demand_points: list[dict[str, Any]],
    *,
    max_capacity: int,
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for point in demand_points:
        student_count = int(point.get("student_count", 0) or 0)
        if student_count <= max_capacity:
            expanded.append(dict(point))
            continue
        remaining = student_count
        chunk_index = 1
        while remaining > 0:
            chunk_size = min(max_capacity, remaining)
            expanded.append(
                {
                    **dict(point),
                    "student_count": chunk_size,
                    "address": f"{point.get('address', '')} (group {chunk_index})",
                    "formatted_address": str(point.get("formatted_address", "") or point.get("address", "")),
                }
            )
            remaining -= chunk_size
            chunk_index += 1
    return expanded


def _build_extended_matrices(
    duration_matrix: list[list[float]],
    distance_matrix: list[list[float]],
    service_direction: str,
) -> tuple[list[list[int]], list[list[float]], int]:
    node_count = len(duration_matrix)
    dummy_index = node_count
    extended_size = node_count + 1
    direction = str(service_direction or "").strip().lower()
    extended_duration: list[list[int]] = []
    extended_distance: list[list[float]] = []
    for from_index in range(extended_size):
        duration_row: list[int] = []
        distance_row: list[float] = []
        for to_index in range(extended_size):
            if from_index == dummy_index:
                allowed = direction == "to_school" and to_index != dummy_index
                duration_row.append(0 if allowed else HUGE_COST)
                distance_row.append(0.0 if allowed else float(HUGE_COST))
            elif to_index == dummy_index:
                allowed = direction != "to_school"
                duration_row.append(0 if allowed else HUGE_COST)
                distance_row.append(0.0 if allowed else float(HUGE_COST))
            elif from_index == to_index:
                duration_row.append(0)
                distance_row.append(0.0)
            else:
                duration_row.append(max(0, int(round(float(duration_matrix[from_index][to_index])))))
                distance_row.append(float(distance_matrix[from_index][to_index]))
        extended_duration.append(duration_row)
        extended_distance.append(distance_row)
    return extended_duration, extended_distance, dummy_index


def _format_infeasible_diagnostics(
    points: list[dict[str, Any]],
    demand_points: list[dict[str, Any]],
    duration_matrix: list[list[float]],
    *,
    service_direction: str,
    max_route_duration_minutes: int,
) -> str:
    direction = str(service_direction or "").strip().lower()
    max_duration_s = float(max_route_duration_minutes * 60)
    unreachable: list[str] = []
    over_limit: list[tuple[float, str]] = []

    for point_index, point in enumerate(demand_points, start=1):
        single_stop_duration_s = (
            float(duration_matrix[point_index][0])
            if direction == "to_school"
            else float(duration_matrix[0][point_index])
        )
        address = str(
            point.get("formatted_address")
            or point.get("address")
            or points[point_index].get("address")
            or f"Demand point {point_index}"
        ).strip()
        if single_stop_duration_s >= HUGE_COST / 2:
            unreachable.append(address)
            continue
        if single_stop_duration_s > max_duration_s:
            over_limit.append((single_stop_duration_s, address))

    hints = []
    if unreachable:
        sample = "; ".join(unreachable[:3])
        hints.append(f"OSRM could not route {len(unreachable)} demand point(s), e.g. {sample}.")
    if over_limit:
        over_limit.sort(reverse=True, key=lambda item: item[0])
        sample = "; ".join(
            f"{address} ({duration_s / 60.0:.1f} min one-stop)"
            for duration_s, address in over_limit[:3]
        )
        hints.append(
            f"{len(over_limit)} demand point(s) exceed the {max_route_duration_minutes} min route limit even alone, e.g. {sample}."
        )
    if not hints:
        hints.append(
            f"All single-stop routes fit within {max_route_duration_minutes} min, but the combined capacity/time/stop constraints still had no solution."
        )
    hints.append("Try Cost Saver mode, reduce bus monitor seats, or review far/high-density addresses.")
    return " ".join(hints)


def build_global_ortools_plan(
    geocode_result: dict[str, Any],
    *,
    market: str = "KR",
    mode: str = "balanced",
    monitor_seats: int = 1,
    max_route_duration_minutes: int | None = None,
    custom_catalog: list[dict[str, Any]] | None = None,
    service_direction: str = "to_school",
    traffic_profile_name: str = "Off-Peak",
    traffic_profile_context: str = "Unscaled OSRM candidate time",
) -> dict[str, Any]:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except Exception as exc:
        raise RuntimeError("OR-Tools is required for global demand planning.") from exc

    school = dict(geocode_result.get("school") or {})
    if school.get("status") != "ok" or school.get("lat") is None or school.get("lng") is None:
        raise ValueError("School address must geocode successfully before global planning.")

    assumptions = get_planning_assumptions(
        market,
        mode=mode,
        monitor_seats=monitor_seats,
        max_route_duration_minutes=max_route_duration_minutes,
    )
    raw_demand_points = [
        dict(point)
        for point in list(geocode_result.get("demand_points") or [])
        if point.get("status") == "ok" and point.get("lat") is not None and point.get("lng") is not None
    ]
    if not raw_demand_points:
        raise ValueError("No geocoded demand points are available for global planning.")

    catalog_capacities = [
        int(vehicle.get("student_capacity", 0) or 0)
        for vehicle in get_vehicle_catalog(
            assumptions.market,
            monitor_seats=assumptions.monitor_seats,
            custom_catalog=custom_catalog,
        )
    ]
    max_capacity = max(catalog_capacities or [0])
    if max_capacity <= 0:
        raise ValueError("Vehicle catalog has no usable student capacity.")

    demand_points = _expand_large_demands(raw_demand_points, max_capacity=max_capacity)
    total_students = sum(int(point.get("student_count", 0) or 0) for point in demand_points)
    vehicle_pool = _candidate_vehicle_pool(total_students, assumptions, custom_catalog=custom_catalog)
    if not vehicle_pool:
        raise ValueError("No candidate vehicles are available for global planning.")

    points = [_point_payload(school), *[_point_payload(point) for point in demand_points]]
    duration_matrix, distance_matrix = _build_osrm_matrix(points)
    extended_duration, extended_distance, dummy_index = _build_extended_matrices(
        duration_matrix,
        distance_matrix,
        service_direction,
    )
    vehicle_count = len(vehicle_pool)
    direction = str(service_direction or "").strip().lower()
    if direction == "to_school":
        starts = [dummy_index] * vehicle_count
        ends = [0] * vehicle_count
    else:
        starts = [0] * vehicle_count
        ends = [dummy_index] * vehicle_count

    manager = pywrapcp.RoutingIndexManager(len(extended_duration), vehicle_count, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def transit_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(extended_duration[from_node][to_node])

    transit_callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    for vehicle_index, vehicle in enumerate(vehicle_pool):
        routing.SetFixedCostOfVehicle(int(vehicle["fixed_cost"]), vehicle_index)

    def demand_callback(from_index: int) -> int:
        node = manager.IndexToNode(from_index)
        if node <= 0 or node == dummy_index:
            return 0
        return int(demand_points[node - 1].get("student_count", 0) or 0)

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        [int(vehicle["capacity"]) for vehicle in vehicle_pool],
        True,
        "Capacity",
    )

    routing.AddDimension(
        transit_callback_index,
        0,
        int(assumptions.max_route_duration_minutes * 60),
        True,
        "Time",
    )

    def stop_callback(from_index: int, to_index: int) -> int:
        to_node = manager.IndexToNode(to_index)
        return 1 if 1 <= to_node < dummy_index else 0

    stop_callback_index = routing.RegisterTransitCallback(stop_callback)
    routing.AddDimension(
        stop_callback_index,
        0,
        int(assumptions.max_stops_per_route),
        True,
        "Stops",
    )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 8

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        diagnostics = _format_infeasible_diagnostics(
            points,
            demand_points,
            duration_matrix,
            service_direction=service_direction,
            max_route_duration_minutes=assumptions.max_route_duration_minutes,
        )
        raise RuntimeError(f"Global OR-Tools plan was infeasible. {diagnostics}")

    route_rows: list[dict[str, Any]] = []
    route_details: list[dict[str, Any]] = []
    for vehicle_index, vehicle in enumerate(vehicle_pool):
        index = routing.Start(vehicle_index)
        order: list[int] = []
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != dummy_index:
                order.append(node)
            index = solution.Value(routing.NextVar(index))
        end_node = manager.IndexToNode(index)
        if end_node != dummy_index:
            order.append(end_node)
        customer_order = [node for node in order if node != 0]
        if not customer_order:
            continue

        total_duration_s, total_distance_m = _route_metrics_for_order(order, duration_matrix, distance_matrix)
        leg_details = _route_leg_details_for_order(points, order)
        ordered_points = _annotate_ordered_points_with_schedule(
            [points[node] for node in order],
            leg_details,
            route_duration_s=total_duration_s,
            service_direction=service_direction,
        )
        student_count = sum(int(points[node].get("student_count", 0) or 0) for node in customer_order)
        load_factor = student_count / max(1, int(vehicle["capacity"]))
        route_id = f"G{len(route_rows) + 1:02d}"
        warnings: list[str] = []
        if total_duration_s > assumptions.max_route_duration_minutes * 60:
            warnings.append(f"exceeds {assumptions.max_route_duration_minutes} min target")
        route_rows.append(
            {
                "cluster_id": route_id,
                "solver": "global_ortools",
                "service_direction": service_direction,
                "students": student_count,
                "stops": len(customer_order),
                "vehicle": vehicle.get("display_name", "Unknown"),
                "distance_km": round(total_distance_m / 1000.0, 2),
                "duration_min": round(total_duration_s / 60.0, 1),
                "load_factor_pct": round(load_factor * 100.0, 1),
                "warnings": "; ".join(warnings),
            }
        )
        route_details.append(
            {
                "cluster_id": route_id,
                "solver": "global_ortools",
                "service_direction": service_direction,
                "order": order,
                "ordered_points": ordered_points,
                "leg_details": leg_details,
                "duration_s": total_duration_s,
                "distance_m": total_distance_m,
                "selected_vehicle": {
                    **vehicle,
                    "student_capacity": int(vehicle["capacity"]),
                    "load_factor": load_factor,
                    "empty_seats": max(0, int(vehicle["capacity"]) - student_count),
                },
                "warnings": warnings,
            }
        )

    route_rows.sort(key=lambda row: str(row["cluster_id"]))
    route_details.sort(key=lambda route: str(route["cluster_id"]))
    return {
        "school": school,
        "routes": route_details,
        "summary": {
            "route_count": len(route_rows),
            "total_distance_km": round(sum(float(row["distance_km"]) for row in route_rows), 2),
            "total_duration_min": round(sum(float(row["duration_min"]) for row in route_rows), 1),
            "service_direction": service_direction,
            "max_route_duration_minutes": assumptions.max_route_duration_minutes,
            "candidate_vehicle_count": len(vehicle_pool),
            "solver": "global_ortools",
            "traffic_profile_name": traffic_profile_name,
            "traffic_profile_context": traffic_profile_context,
        },
        "route_rows": route_rows,
    }
