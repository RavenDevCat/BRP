from __future__ import annotations

import io
from typing import Any

import folium
import pandas as pd

from distance_tool import compute_osrm_metrics_from_origin


HUGE_COST = 10**9


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


def _build_osrm_matrix(points: list[dict[str, Any]]) -> tuple[list[list[float]], list[list[float]]]:
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


def build_osrm_route_preview(
    cluster_result: dict[str, Any],
    *,
    service_direction: str = "to_school",
    max_route_duration_minutes: int | None = None,
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
        ordered_points = [points[index] for index in order]
        route_details.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "solver": solver,
                "service_direction": service_direction,
                "order": order,
                "ordered_points": ordered_points,
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
        assignments_sheet_name="generated_plan_assignments",
        fleet_sheet_name="generated_plan_fleet",
    )


def build_legacy_route_plan_workbook_bytes(route_preview: dict[str, Any]) -> bytes:
    return _build_route_preview_workbook_bytes(
        route_preview,
        assignments_sheet_name="current_plan_assignments",
        fleet_sheet_name="current_plan_fleet",
    )


def build_route_preview_map_html(route_preview: dict[str, Any]) -> str:
    school = dict(route_preview.get("school") or {})
    if school.get("status") != "ok" or school.get("lat") is None or school.get("lng") is None:
        return ""

    colors = ("#e03131", "#2f9e44", "#1971c2", "#f08c00", "#862e9c", "#0c8599")
    school_location = [float(school["lat"]), float(school["lng"])]
    fmap = folium.Map(location=school_location, zoom_start=12, tiles="OpenStreetMap")
    bounds = [school_location]
    folium.Marker(
        location=school_location,
        tooltip="School",
        popup=str(school.get("formatted_address") or school.get("address") or "School"),
        icon=folium.Icon(color="blue", icon="home"),
    ).add_to(fmap)

    for route_index, route in enumerate(list(route_preview.get("routes") or [])):
        color = colors[route_index % len(colors)]
        route_id = str(route.get("cluster_id", f"Route {route_index + 1}"))
        ordered_points = list(route.get("ordered_points") or [])
        polyline_points: list[list[float]] = []
        for stop_index, point in enumerate(ordered_points):
            location = [float(point["lat"]), float(point["lng"])]
            bounds.append(location)
            polyline_points.append(location)
            folium.CircleMarker(
                location=location,
                radius=7 if stop_index else 9,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.78,
                tooltip=f"{route_id} stop {stop_index + 1}",
                popup=str(point.get("address", "")),
            ).add_to(fmap)
        if len(polyline_points) >= 2:
            folium.PolyLine(
                polyline_points,
                color=color,
                weight=4,
                opacity=0.72,
                tooltip=f"{route_id} preview order",
            ).add_to(fmap)

    if len(bounds) > 1:
        fmap.fit_bounds(bounds, padding=(20, 20))
    return fmap._repr_html_()
