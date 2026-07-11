from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

import client_runtime as runtime
from distance_tool import compute_osrm_metrics_from_origin, compute_osrm_route_leg_details


HUGE_COST = 10**9
DEFAULT_TO_SCHOOL_ARRIVAL_MINUTES = 8 * 60
DEFAULT_FROM_SCHOOL_DEPARTURE_MINUTES = 15 * 60 + 40
DEFAULT_STOP_DWELL_SECONDS = 60.0


def _format_clock_minutes(minutes: float | int | None) -> str:
    if minutes is None:
        return ""
    total_minutes = int(round(float(minutes))) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _service_direction_label(service_direction: str) -> str:
    normalized = str(service_direction or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "To School" if normalized == "to_school" else "From School"


def _schedule_anchor(service_direction: str) -> tuple[int, str, str]:
    if _service_direction_label(service_direction) == "To School":
        return DEFAULT_TO_SCHOOL_ARRIVAL_MINUTES, "08:00", "School arrival"
    return DEFAULT_FROM_SCHOOL_DEPARTURE_MINUTES, "15:40", "School departure"


def _annotate_ordered_points_with_schedule(
    ordered_points: list[dict[str, Any]],
    leg_details: list[dict[str, Any]],
    *,
    route_duration_s: float,
    service_direction: str,
    dwell_seconds: float = DEFAULT_STOP_DWELL_SECONDS,
) -> list[dict[str, Any]]:
    anchor_minutes, anchor_label, anchor_kind = _schedule_anchor(service_direction)
    service_label = _service_direction_label(service_direction)
    cumulative_duration_s = 0.0
    cumulative_by_order: list[float] = []
    for index, _point in enumerate(ordered_points):
        if index > 0 and index - 1 < len(leg_details):
            cumulative_duration_s += float(dict(leg_details[index - 1]).get("duration_s", 0.0) or 0.0)
        cumulative_by_order.append(cumulative_duration_s)

    service_orders = [
        index
        for index, point in enumerate(ordered_points)
        if int(point.get("student_count", 0) or 0) > 0
    ]
    annotated: list[dict[str, Any]] = []
    for order, point in enumerate(ordered_points):
        item = dict(point)
        drive_elapsed_s = cumulative_by_order[order] if order < len(cumulative_by_order) else 0.0
        if service_label == "To School":
            if order not in service_orders:
                offset_s = 0.0
            else:
                downstream_dwell_count = len([item_order for item_order in service_orders if item_order >= order])
                remaining_drive_s = max(0.0, float(route_duration_s) - drive_elapsed_s)
                offset_s = -(remaining_drive_s + downstream_dwell_count * dwell_seconds)
        else:
            prior_dwell_count = len([item_order for item_order in service_orders if item_order < order])
            if order not in service_orders:
                prior_dwell_count = 0
            offset_s = drive_elapsed_s + prior_dwell_count * dwell_seconds
        scheduled_minutes = anchor_minutes + offset_s / 60.0
        item["schedule_anchor_label"] = anchor_label
        item["schedule_anchor_kind"] = anchor_kind
        item["scheduled_offset_s"] = offset_s
        item["scheduled_time_minutes"] = scheduled_minutes
        item["scheduled_time_label"] = _format_clock_minutes(scheduled_minutes)
        annotated.append(item)
    return annotated


def _point_payload(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "lat": float(point["lat"]),
        "lng": float(point["lng"]),
        "country": str(point.get("country", "")).strip(),
        "city": str(point.get("city", "")).strip(),
        "address": str(point.get("address", "")).strip(),
        "formatted_address": str(point.get("formatted_address", "")).strip(),
        "student_count": int(point.get("student_count", 0) or 0),
    }


def _build_osrm_matrix(
    points: list[dict[str, Any]],
) -> tuple[list[list[float]], list[list[float]]]:
    duration_matrix: list[list[float]] = []
    distance_matrix: list[list[float]] = []
    for origin_index, origin in enumerate(points):
        destinations = [point for index, point in enumerate(points) if index != origin_index]
        metrics = compute_osrm_metrics_from_origin(origin, destinations)
        duration_row: list[float] = []
        distance_row: list[float] = []
        metric_index = 0
        for destination_index in range(len(points)):
            if destination_index == origin_index:
                duration_row.append(0.0)
                distance_row.append(0.0)
                continue
            metric = metrics[metric_index]
            metric_index += 1
            duration_s = metric.get("duration_s")
            distance_m = metric.get("distance_m")
            duration_row.append(float(duration_s) if duration_s is not None else float(HUGE_COST))
            distance_row.append(float(distance_m) if distance_m is not None else float(HUGE_COST))
        duration_matrix.append(duration_row)
        distance_matrix.append(distance_row)
    return duration_matrix, distance_matrix


def _greedy_open_route_order(duration_matrix: list[list[float]], service_direction: str) -> list[int]:
    node_count = len(duration_matrix)
    if node_count <= 1:
        return [0]
    unvisited = set(range(1, node_count))
    direction = str(service_direction or "").strip().lower()
    if direction == "to_school":
        current = max(unvisited, key=lambda node: duration_matrix[node][0])
        order = [current]
        unvisited.remove(current)
        while unvisited:
            next_node = min(unvisited, key=lambda node: duration_matrix[current][node])
            order.append(next_node)
            unvisited.remove(next_node)
            current = next_node
        order.append(0)
        return order

    order = [0]
    current = 0
    while unvisited:
        next_node = min(unvisited, key=lambda node: duration_matrix[current][node])
        order.append(next_node)
        unvisited.remove(next_node)
        current = next_node
    return order


def _ortools_open_route_order(duration_matrix: list[list[float]], service_direction: str) -> tuple[list[int], str]:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except Exception:
        return _greedy_open_route_order(duration_matrix, service_direction), "greedy_fallback"

    node_count = len(duration_matrix)
    if node_count <= 2:
        return _greedy_open_route_order(duration_matrix, service_direction), "trivial"

    dummy_index = node_count
    extended_size = node_count + 1
    direction = str(service_direction or "").strip().lower()

    if direction == "to_school":
        start_index = dummy_index
        end_index = 0
    else:
        start_index = 0
        end_index = dummy_index

    extended_matrix: list[list[int]] = []
    for from_index in range(extended_size):
        row: list[int] = []
        for to_index in range(extended_size):
            if from_index == dummy_index:
                if direction == "to_school" and to_index != 0:
                    row.append(0)
                else:
                    row.append(HUGE_COST)
            elif to_index == dummy_index:
                if direction == "to_school":
                    row.append(HUGE_COST)
                else:
                    row.append(0)
            elif from_index == to_index:
                row.append(0)
            else:
                row.append(max(0, int(round(float(duration_matrix[from_index][to_index])))))
        extended_matrix.append(row)

    manager = pywrapcp.RoutingIndexManager(extended_size, 1, [start_index], [end_index])
    routing = pywrapcp.RoutingModel(manager)

    def transit_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return extended_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 2

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        return _greedy_open_route_order(duration_matrix, service_direction), "greedy_fallback"

    order: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != dummy_index:
            order.append(node)
        index = solution.Value(routing.NextVar(index))
    end_node = manager.IndexToNode(index)
    if end_node != dummy_index:
        order.append(end_node)
    return order, "ortools"


def _route_metrics_for_order(
    order: list[int],
    duration_matrix: list[list[float]],
    distance_matrix: list[list[float]],
) -> tuple[float, float]:
    total_duration_s = 0.0
    total_distance_m = 0.0
    for from_node, to_node in zip(order[:-1], order[1:]):
        total_duration_s += float(duration_matrix[from_node][to_node])
        total_distance_m += float(distance_matrix[from_node][to_node])
    return total_duration_s, total_distance_m


def _route_leg_details_for_order(
    points: list[dict[str, Any]],
    order: list[int],
) -> list[dict[str, Any]]:
    ordered_points = [points[index] for index in order]
    return compute_osrm_route_leg_details(ordered_points)


def build_osrm_route_preview(
    cluster_result: dict[str, Any],
    *,
    service_direction: str = "to_school",
    max_route_duration_minutes: int | None = None,
    traffic_profile_name: str = "Off-Peak",
    traffic_profile_context: str = "Unscaled OSRM candidate time",
) -> dict[str, Any]:
    school = dict(cluster_result.get("school") or {})
    if school.get("status") != "ok" or school.get("lat") is None or school.get("lng") is None:
        raise ValueError("School address must geocode successfully before route preview.")

    route_rows: list[dict[str, Any]] = []
    route_details: list[dict[str, Any]] = []
    for cluster in list(cluster_result.get("clusters") or []):
        cluster_points = list(cluster.get("points") or [])
        if not cluster_points:
            continue
        points = [_point_payload(school), *[_point_payload(point) for point in cluster_points]]
        duration_matrix, distance_matrix = _build_osrm_matrix(points)
        order, solver = _ortools_open_route_order(duration_matrix, service_direction)
        total_duration_s, total_distance_m = _route_metrics_for_order(order, duration_matrix, distance_matrix)
        selected_vehicle = dict(cluster.get("selected_vehicle") or {})
        duration_min = total_duration_s / 60.0
        warnings: list[str] = []
        if max_route_duration_minutes and duration_min > float(max_route_duration_minutes):
            warnings.append(f"exceeds {int(max_route_duration_minutes)} min target")
        if any(index == 0 for index in order[1:-1]):
            warnings.append("school appears inside route order")
        route_rows.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "solver": solver,
                "service_direction": service_direction,
                "students": cluster.get("student_count"),
                "stops": cluster.get("stop_count"),
                "vehicle": selected_vehicle.get("display_name", "No feasible vehicle"),
                "distance_km": round(total_distance_m / 1000.0, 2),
                "duration_min": round(duration_min, 1),
                "load_factor_pct": round(float(selected_vehicle.get("load_factor", 0.0) or 0.0) * 100.0, 1),
                "warnings": "; ".join(warnings),
            }
        )
        leg_details = _route_leg_details_for_order(points, order)
        ordered_points = _annotate_ordered_points_with_schedule(
            [points[index] for index in order],
            leg_details,
            route_duration_s=total_duration_s,
            service_direction=service_direction,
        )
        route_details.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "solver": solver,
                "service_direction": service_direction,
                "order": order,
                "ordered_points": ordered_points,
                "leg_details": leg_details,
                "duration_s": total_duration_s,
                "distance_m": total_distance_m,
                "selected_vehicle": selected_vehicle,
                "warnings": warnings,
            }
        )

    return {
        "school": school,
        "routes": route_details,
        "summary": {
            "route_count": len(route_rows),
            "total_distance_km": round(sum(float(row["distance_km"]) for row in route_rows), 2),
            "total_duration_min": round(sum(float(row["duration_min"]) for row in route_rows), 1),
            "service_direction": service_direction,
            "max_route_duration_minutes": max_route_duration_minutes,
            "traffic_profile_name": traffic_profile_name,
            "traffic_profile_context": traffic_profile_context,
        },
        "route_rows": route_rows,
    }


def route_preview_to_dataframe(route_preview: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(route_preview.get("route_rows") or []))


def route_preview_stop_detail_to_dataframe(route_preview: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for route in list(route_preview.get("routes") or []):
        vehicle = dict(route.get("selected_vehicle") or {})
        for stop_index, point in enumerate(list(route.get("ordered_points") or []), start=1):
            rows.append(
                {
                    "route_id": route.get("cluster_id"),
                    "stop_sequence": stop_index,
                    "bus_type": vehicle.get("display_name", "No feasible vehicle"),
                    "country": point.get("country"),
                    "city": point.get("city"),
                    "address": point.get("address"),
                    "formatted_address": point.get("formatted_address"),
                    "passenger_count": int(point.get("student_count", 0) or 0),
                    "estimated_pickup_dropoff_time": point.get("scheduled_time_label"),
                    "lat": point.get("lat"),
                    "lng": point.get("lng"),
                }
            )
    return pd.DataFrame(rows)


def _build_route_preview_workbook_bytes(
    route_preview: dict[str, Any],
    *,
    assignments_sheet_name: str,
    fleet_sheet_name: str,
) -> bytes:
    assignment_rows: list[dict[str, Any]] = []
    fleet_counts: dict[tuple[str, int], int] = {}
    service_direction = str((route_preview.get("summary") or {}).get("service_direction", "to_school"))
    for route in list(route_preview.get("routes") or []):
        route_id = str(route.get("cluster_id", "")).strip() or "R1"
        vehicle = dict(route.get("selected_vehicle") or {})
        bus_type = str(vehicle.get("display_name", "No feasible vehicle")).strip() or "No feasible vehicle"
        seat_count = int(vehicle.get("student_capacity", 0) or 0)
        fleet_counts[(bus_type, seat_count)] = fleet_counts.get((bus_type, seat_count), 0) + 1
        for stop_index, point in enumerate(list(route.get("ordered_points") or []), start=1):
            assignment_rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": stop_index,
                    "bus_type": bus_type,
                    "country": point.get("country"),
                    "city": point.get("city"),
                    "address": point.get("address"),
                    "passenger_count": int(point.get("student_count", 0) or 0),
                    "estimated_pickup_dropoff_time": point.get("scheduled_time_label"),
                }
            )

    fleet_rows = [
        {
            "bus_type": bus_type,
            "seat_count": seat_count,
            "vehicle_count": vehicle_count,
        }
        for (bus_type, seat_count), vehicle_count in sorted(fleet_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    notes_rows = [
        {
            "field": "service_direction",
            "value": "To School" if service_direction == "to_school" else "From School",
            "note": "Use this value when reviewing or submitting this auto-generated plan.",
        },
        {
            "field": "source",
            "value": "Fleet Planner Preview",
            "note": "Auto-generated from demand inputs, OSRM road metrics, and OR-Tools routing.",
        },
    ]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(assignment_rows).to_excel(writer, sheet_name=assignments_sheet_name, index=False)
        pd.DataFrame(fleet_rows).to_excel(writer, sheet_name=fleet_sheet_name, index=False)
        pd.DataFrame(notes_rows).to_excel(writer, sheet_name="template_notes", index=False)
    return output.getvalue()


def build_generated_plan_workbook_bytes(route_preview: dict[str, Any]) -> bytes:
    return _build_route_preview_workbook_bytes(
        route_preview,
        assignments_sheet_name="current_plan_assignments",
        fleet_sheet_name="current_plan_fleet",
    )


def build_legacy_route_plan_workbook_bytes(route_preview: dict[str, Any]) -> bytes:
    return _build_route_preview_workbook_bytes(
        route_preview,
        assignments_sheet_name="current_plan_assignments",
        fleet_sheet_name="current_plan_fleet",
    )


def _map_bounds_from_coordinates(coordinates: list[tuple[float, float]]) -> dict[str, float] | None:
    if not coordinates:
        return None
    lats = [float(item[0]) for item in coordinates]
    lngs = [float(item[1]) for item in coordinates]
    return {
        "south": min(lats),
        "west": min(lngs),
        "north": max(lats),
        "east": max(lngs),
    }


def _route_geometry_from_leg_details(leg_details: list[dict[str, Any]]) -> list[list[float]]:
    geometry: list[list[float]] = []
    for detail in leg_details:
        for raw_pair in list(dict(detail).get("geometry") or []):
            if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) < 2:
                continue
            lat = float(raw_pair[0])
            lng = float(raw_pair[1])
            point = [lng, lat]
            if not geometry or geometry[-1] != point:
                geometry.append(point)
    return geometry


def _route_connectors_from_leg_details(
    leg_details: list[dict[str, Any]],
    *,
    route_id: str,
    route_index: int,
) -> list[dict[str, Any]]:
    connectors: list[dict[str, Any]] = []
    for leg_index, detail in enumerate(leg_details):
        leg = dict(detail or {})
        for connector_index, raw_connector in enumerate(list(leg.get("snap_connectors") or [])):
            connector = dict(raw_connector or {})
            geometry: list[list[float]] = []
            for raw_pair in list(connector.get("geometry") or []):
                if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) < 2:
                    continue
                lat = float(raw_pair[0])
                lng = float(raw_pair[1])
                point = [lng, lat]
                if not geometry or geometry[-1] != point:
                    geometry.append(point)
            if len(geometry) < 2:
                continue
            connectors.append(
                {
                    "id": f"{route_id}:connector:{leg_index}:{connector_index}",
                    "route_id": route_id,
                    "route_index": route_index,
                    "from_node": leg.get("from_node"),
                    "to_node": leg.get("to_node"),
                    "connector_type": str(connector.get("type") or "snap"),
                    "distance_m": float(connector.get("distance_m", 0.0) or 0.0),
                    "geometry": geometry,
                }
            )
    return connectors


def build_route_preview_map_data(
    route_preview: dict[str, Any],
    *,
    scenario_key: str = "optimized_plan",
    scenario_name: str = "Optimized Plan",
) -> dict[str, Any]:
    routes = list(route_preview.get("routes") or [])
    summary = dict(route_preview.get("summary") or {})
    service_direction = str(summary.get("service_direction") or "to_school")
    service_direction_label = _service_direction_label(service_direction)
    route_payloads: list[dict[str, Any]] = []
    stop_payloads: list[dict[str, Any]] = []
    route_connectors: list[dict[str, Any]] = []
    all_coordinates: list[tuple[float, float]] = []

    for route_index, route in enumerate(routes):
        route_id = str(route.get("cluster_id") or f"Route {route_index + 1}")
        ordered_points = list(route.get("ordered_points") or [])
        leg_details = list(route.get("leg_details") or [])
        geometry = _route_geometry_from_leg_details(leg_details)
        for lng, lat in geometry:
            all_coordinates.append((lat, lng))
        connectors = _route_connectors_from_leg_details(
            leg_details,
            route_id=route_id,
            route_index=route_index,
        )
        route_connectors.extend(connectors)
        for connector in connectors:
            for lng, lat in list(connector.get("geometry") or []):
                all_coordinates.append((lat, lng))
        cumulative_duration_s = 0.0
        cumulative_distance_m = 0.0
        stop_ids: list[str] = []
        load = 0
        for order, point in enumerate(ordered_points):
            if point.get("lat") is None or point.get("lng") is None:
                continue
            if order > 0 and order - 1 < len(leg_details):
                leg = dict(leg_details[order - 1] or {})
                cumulative_duration_s += float(leg.get("duration_s", 0.0) or 0.0)
                cumulative_distance_m += float(leg.get("distance_m", 0.0) or 0.0)
            lat = float(point["lat"])
            lng = float(point["lng"])
            passenger_count = int(point.get("student_count", 0) or 0)
            load += passenger_count
            stop_id = f"{route_id}:{order}"
            stop_ids.append(stop_id)
            all_coordinates.append((lat, lng))
            stop_payloads.append(
                {
                    "id": stop_id,
                    "route_id": route_id,
                    "route_index": route_index,
                    "order": order,
                    "node_index": order,
                    "address": str(point.get("formatted_address") or point.get("address") or "").strip(),
                    "requested_address": str(point.get("address") or "").strip(),
                    "passenger_count": passenger_count,
                    "is_depot": passenger_count == 0,
                    "lat": lat,
                    "lng": lng,
                    "cumulative_duration_s": cumulative_duration_s,
                    "cumulative_distance_m": cumulative_distance_m,
                    "schedule_anchor_label": point.get("schedule_anchor_label"),
                    "schedule_anchor_kind": point.get("schedule_anchor_kind"),
                    "scheduled_offset_s": point.get("scheduled_offset_s"),
                    "scheduled_time_minutes": point.get("scheduled_time_minutes"),
                    "scheduled_time_label": point.get("scheduled_time_label"),
                }
            )
        vehicle = dict(route.get("selected_vehicle") or {})
        route_payloads.append(
            {
                "id": route_id,
                "route_index": route_index,
                "vehicle_id": route_index + 1,
                "bus_type_name": str(vehicle.get("display_name") or "Selected vehicle"),
                "load": load,
                "bus_capacity": int(vehicle.get("student_capacity", vehicle.get("capacity", 0)) or 0),
                "comfort_capacity": None,
                "stop_count": len([point for point in ordered_points if int(point.get("student_count", 0) or 0) > 0]),
                "max_stops": None,
                "distance_m": float(route.get("distance_m", 0.0) or 0.0),
                "duration_s": float(route.get("duration_s", 0.0) or 0.0),
                "raw_duration_s": float(route.get("duration_s", 0.0) or 0.0),
                "traffic_time_source": str(summary.get("traffic_profile_context") or ""),
                "geometry": geometry,
                "stop_ids": stop_ids,
            }
        )

    return {
        "job_id": "",
        "scenario_key": scenario_key,
        "scenario_name": scenario_name,
        "service_direction": service_direction_label,
        "traffic_profile_name": str(summary.get("traffic_profile_name") or ""),
        "bounds": _map_bounds_from_coordinates(all_coordinates),
        "routes": route_payloads,
        "stops": stop_payloads,
        "route_connectors": route_connectors,
        "private_links": [],
        "summary": {
            "route_count": len(route_payloads),
            "stop_count": len([stop for stop in stop_payloads if not stop.get("is_depot")]),
            "passenger_count": sum(int(route.get("load", 0) or 0) for route in route_payloads),
            "distance_m": sum(float(route.get("distance_m", 0.0) or 0.0) for route in route_payloads),
            "duration_s": max([float(route.get("duration_s", 0.0) or 0.0) for route in route_payloads] or [0.0]),
        },
    }


def build_route_preview_map_html(route_preview: dict[str, Any]) -> str:
    routes = list(route_preview.get("routes") or [])
    if not routes:
        return ""

    map_points: list[dict[str, Any]] = []
    map_routes: list[dict[str, Any]] = []
    school = dict(route_preview.get("school") or {})
    school_lat = float(school["lat"]) if school.get("lat") is not None else None
    school_lng = float(school["lng"]) if school.get("lng") is not None else None
    service_direction = str(dict(route_preview.get("summary") or {}).get("service_direction") or "to_school")
    service_direction_label = "To School" if service_direction == "to_school" else "From School"

    for route_index, route in enumerate(routes):
        node_indexes: list[int] = []
        ordered_points = list(route.get("ordered_points") or [])
        for point in ordered_points:
            if point.get("lat") is None or point.get("lng") is None:
                continue
            lat = float(point["lat"])
            lng = float(point["lng"])
            node_indexes.append(len(map_points))
            is_school = (
                school_lat is not None
                and school_lng is not None
                and abs(lat - school_lat) < 0.000001
                and abs(lng - school_lng) < 0.000001
            )
            map_points.append(
                {
                    "address": str(point.get("formatted_address") or point.get("address") or "School").strip(),
                    "plot_lat": lat,
                    "plot_lng": lng,
                    "is_depot": is_school,
                }
            )
        if len(node_indexes) < 2:
            continue

        vehicle = dict(route.get("selected_vehicle") or {})
        bus_capacity = int(vehicle.get("student_capacity", vehicle.get("capacity", 0)) or 0)
        load = sum(
            int(point.get("student_count", 0) or 0)
            for point in ordered_points
            if point.get("student_count") is not None
        )
        leg_details = []
        for leg_index, detail in enumerate(list(route.get("leg_details") or [])):
            if leg_index + 1 >= len(node_indexes):
                break
            leg_details.append(
                {
                    **dict(detail),
                    "from_node": node_indexes[leg_index],
                    "to_node": node_indexes[leg_index + 1],
                }
            )
        map_routes.append(
            {
                "route_id": str(route.get("cluster_id") or f"Route {route_index + 1}"),
                "vehicle_id": route_index + 1,
                "bus_type_name": str(vehicle.get("display_name") or "Selected vehicle"),
                "bus_capacity": bus_capacity,
                "load": load,
                "time_s": float(route.get("duration_s", 0.0) or 0.0),
                "distance_m": float(route.get("distance_m", 0.0) or 0.0),
                "nodes": node_indexes,
                "leg_details": leg_details,
            }
        )

    if not map_points or not map_routes:
        return ""

    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as temp_file:
        temp_path = temp_file.name
    try:
        runtime.render_map(
            map_points,
            map_routes,
            temp_path,
            service_direction=service_direction_label,
        )
        return Path(temp_path).read_text(encoding="utf-8")
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError:
            pass
