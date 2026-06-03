from __future__ import annotations

import math
from typing import Any

import folium
from folium import Element
import pandas as pd

import client_runtime as runtime
from fleet_selector import select_vehicle_for_group
from planning_assumptions import get_planning_assumptions
from vehicle_catalog import get_vehicle_catalog


CLUSTER_COLORS: tuple[str, ...] = (
    "#e03131",
    "#2f9e44",
    "#1971c2",
    "#f08c00",
    "#862e9c",
    "#0c8599",
    "#5c940d",
    "#c2255c",
)
SECTOR_LABELS: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _bearing_degrees(origin_lat: float, origin_lng: float, point_lat: float, point_lng: float) -> float:
    lat1 = math.radians(origin_lat)
    lat2 = math.radians(point_lat)
    delta_lng = math.radians(point_lng - origin_lng)
    x = math.sin(delta_lng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lng)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _sector_index(bearing: float, sector_count: int) -> int:
    sector_width = 360.0 / max(1, sector_count)
    return int(((bearing + sector_width / 2.0) % 360.0) // sector_width)


def _sector_label(index: int, sector_count: int) -> str:
    if sector_count == len(SECTOR_LABELS):
        return SECTOR_LABELS[index % len(SECTOR_LABELS)]
    return f"S{index + 1}"


def _max_student_capacity(market: str, monitor_seats: int) -> int:
    capacities = [
        int(item.get("student_capacity", 0) or 0)
        for item in get_vehicle_catalog(market, monitor_seats=monitor_seats)
    ]
    return max(capacities or [0])


def build_demand_clusters(
    geocode_result: dict[str, Any],
    *,
    market: str = "KR",
    mode: str = "balanced",
    monitor_seats: int = 1,
    sector_count: int = 8,
) -> dict[str, Any]:
    school = dict(geocode_result.get("school") or {})
    if school.get("status") != "ok" or school.get("lat") is None or school.get("lng") is None:
        raise ValueError("School address must geocode successfully before clustering demand.")

    assumptions = get_planning_assumptions(market, mode=mode, monitor_seats=monitor_seats)
    max_capacity = _max_student_capacity(assumptions.market, assumptions.monitor_seats)
    if max_capacity <= 0:
        raise ValueError("Vehicle catalog has no usable student capacity.")

    school_lat = float(school["lat"])
    school_lng = float(school["lng"])
    points: list[dict[str, Any]] = []
    failed_points: list[dict[str, Any]] = []
    for point in list(geocode_result.get("demand_points") or []):
        if point.get("status") != "ok" or point.get("lat") is None or point.get("lng") is None:
            failed_points.append(dict(point))
            continue
        point_lat = float(point["lat"])
        point_lng = float(point["lng"])
        distance_km = runtime.haversine_distance_km(school_lat, school_lng, point_lat, point_lng)
        bearing = _bearing_degrees(school_lat, school_lng, point_lat, point_lng)
        sector = _sector_index(bearing, sector_count)
        points.append(
            {
                **dict(point),
                "distance_from_school_km": distance_km,
                "bearing_degrees": bearing,
                "sector": sector,
                "sector_label": _sector_label(sector, sector_count),
            }
        )

    points.sort(
        key=lambda item: (
            int(item["sector"]),
            -float(item["distance_from_school_km"]),
            str(item.get("address", "")).lower(),
        )
    )

    clusters: list[dict[str, Any]] = []
    cluster_sequence = 1
    for sector in sorted({int(item["sector"]) for item in points}):
        sector_points = [item for item in points if int(item["sector"]) == sector]
        current_points: list[dict[str, Any]] = []
        current_students = 0
        for point in sector_points:
            student_count = int(point.get("student_count", 0) or 0)
            should_start_new = (
                current_points
                and (
                    current_students + student_count > max_capacity
                    or len(current_points) + 1 > assumptions.max_stops_per_route
                )
            )
            if should_start_new:
                clusters.append(
                    _finalize_cluster(
                        cluster_sequence,
                        sector,
                        _sector_label(sector, sector_count),
                        current_points,
                        assumptions.market,
                        assumptions.mode,
                        assumptions.monitor_seats,
                    )
                )
                cluster_sequence += 1
                current_points = []
                current_students = 0

            current_points.append(point)
            current_students += student_count

        if current_points:
            clusters.append(
                _finalize_cluster(
                    cluster_sequence,
                    sector,
                    _sector_label(sector, sector_count),
                    current_points,
                    assumptions.market,
                    assumptions.mode,
                    assumptions.monitor_seats,
                )
            )
            cluster_sequence += 1

    return {
        "school": school,
        "clusters": clusters,
        "failed_points": failed_points,
        "summary": {
            "cluster_count": len(clusters),
            "resolved_points": len(points),
            "failed_points": len(failed_points),
            "resolved_students": sum(int(item.get("student_count", 0) or 0) for item in points),
            "failed_students": sum(int(item.get("student_count", 0) or 0) for item in failed_points),
            "max_vehicle_student_capacity": max_capacity,
            "market": assumptions.market,
            "mode": assumptions.mode,
            "monitor_seats": assumptions.monitor_seats,
        },
    }


def split_cluster_result_by_route_limit(
    cluster_result: dict[str, Any],
    overloaded_route_ids: set[str],
    *,
    market: str = "KR",
    mode: str = "balanced",
    monitor_seats: int = 1,
) -> dict[str, Any]:
    if not overloaded_route_ids:
        return cluster_result

    new_clusters: list[dict[str, Any]] = []
    cluster_sequence = 1
    for cluster in list(cluster_result.get("clusters") or []):
        cluster_id = str(cluster.get("cluster_id", "")).strip()
        points = list(cluster.get("points") or [])
        if cluster_id not in overloaded_route_ids or len(points) <= 1:
            copied = dict(cluster)
            copied["cluster_id"] = f"C{cluster_sequence:02d}"
            new_clusters.append(copied)
            cluster_sequence += 1
            continue

        sorted_points = sorted(
            points,
            key=lambda item: float(item.get("distance_from_school_km", 0.0) or 0.0),
            reverse=True,
        )
        midpoint = max(1, len(sorted_points) // 2)
        split_groups = [sorted_points[:midpoint], sorted_points[midpoint:]]
        for split_points in split_groups:
            if not split_points:
                continue
            sector = int(cluster.get("sector", 0) or 0)
            sector_label = str(cluster.get("sector_label", "") or sector)
            new_clusters.append(
                _finalize_cluster(
                    cluster_sequence,
                    sector,
                    sector_label,
                    split_points,
                    market,
                    mode,
                    monitor_seats,
                )
            )
            cluster_sequence += 1

    summary = dict(cluster_result.get("summary") or {})
    summary["cluster_count"] = len(new_clusters)
    summary["split_from_overlong_routes"] = sorted(overloaded_route_ids)
    return {
        **cluster_result,
        "clusters": new_clusters,
        "summary": summary,
    }


def _finalize_cluster(
    cluster_sequence: int,
    sector: int,
    sector_label: str,
    points: list[dict[str, Any]],
    market: str,
    mode: str,
    monitor_seats: int,
) -> dict[str, Any]:
    student_count = sum(int(point.get("student_count", 0) or 0) for point in points)
    stop_count = len(points)
    distances = [float(point.get("distance_from_school_km", 0.0) or 0.0) for point in points]
    avg_distance = sum(distances) / len(distances) if distances else 0.0
    max_distance = max(distances or [0.0])
    min_distance = min(distances or [0.0])
    selection = select_vehicle_for_group(
        student_count,
        market=market,
        mode=mode,
        monitor_seats=monitor_seats,
    )
    selected = selection.selected_vehicle or {}
    warnings: list[str] = []
    if not selected:
        warnings.append("No catalog vehicle can carry this cluster's student count.")
    elif selected.get("load_factor") is not None and float(selected["load_factor"]) < 0.45:
        warnings.append("Low load factor; this cluster may be too small unless comfort is the priority.")
    if max_distance - min_distance > 8.0:
        warnings.append("Wide distance spread inside the same direction sector; review before final routing.")

    return {
        "cluster_id": f"C{cluster_sequence:02d}",
        "sector": sector,
        "sector_label": sector_label,
        "student_count": student_count,
        "stop_count": stop_count,
        "avg_distance_from_school_km": avg_distance,
        "max_distance_from_school_km": max_distance,
        "distance_spread_km": max_distance - min_distance,
        "selected_vehicle": selected,
        "feasible_option_count": len(selection.feasible_options),
        "rejected_option_count": len(selection.rejected_options),
        "warnings": warnings,
        "points": points,
    }


def demand_clusters_to_dataframe(cluster_result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for cluster in list(cluster_result.get("clusters") or []):
        selected = dict(cluster.get("selected_vehicle") or {})
        load_factor = selected.get("load_factor")
        rows.append(
            {
                "Cluster": cluster.get("cluster_id"),
                "Sector": cluster.get("sector_label"),
                "Students": cluster.get("student_count"),
                "Stops": cluster.get("stop_count"),
                "Recommended Vehicle": selected.get("display_name", "No feasible vehicle"),
                "Student Capacity": selected.get("student_capacity", ""),
                "Load Factor": f"{float(load_factor) * 100.0:.1f}%" if load_factor is not None else "N/A",
                "Empty Seats": selected.get("empty_seats", ""),
                "Avg School Distance km": round(float(cluster.get("avg_distance_from_school_km", 0.0)), 2),
                "Max School Distance km": round(float(cluster.get("max_distance_from_school_km", 0.0)), 2),
                "Warnings": "; ".join(str(item) for item in list(cluster.get("warnings") or [])),
            }
        )
    return pd.DataFrame(rows)


def cluster_points_to_dataframe(cluster_result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for cluster in list(cluster_result.get("clusters") or []):
        for point in list(cluster.get("points") or []):
            rows.append(
                {
                    "Cluster": cluster.get("cluster_id"),
                    "Sector": cluster.get("sector_label"),
                    "Students": point.get("student_count"),
                    "Address": point.get("address"),
                    "Formatted Address": point.get("formatted_address"),
                    "Distance From School km": round(float(point.get("distance_from_school_km", 0.0)), 2),
                    "Lat": point.get("lat"),
                    "Lng": point.get("lng"),
                }
            )
    return pd.DataFrame(rows)


def build_demand_cluster_map_html(cluster_result: dict[str, Any]) -> str:
    school = dict(cluster_result.get("school") or {})
    if school.get("status") != "ok" or school.get("lat") is None or school.get("lng") is None:
        return ""

    school_location = [float(school["lat"]), float(school["lng"])]
    fmap = folium.Map(
        location=school_location,
        zoom_start=12,
        tiles="https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    )
    bounds: list[list[float]] = [school_location]
    folium.Marker(
        location=school_location,
        tooltip="School",
        popup=str(school.get("formatted_address") or school.get("address") or "School"),
        icon=folium.Icon(color="blue", icon="home"),
    ).add_to(fmap)

    for index, cluster in enumerate(list(cluster_result.get("clusters") or [])):
        color = CLUSTER_COLORS[index % len(CLUSTER_COLORS)]
        cluster_id = str(cluster.get("cluster_id", "Cluster"))
        selected = dict(cluster.get("selected_vehicle") or {})
        for point in list(cluster.get("points") or []):
            location = [float(point["lat"]), float(point["lng"])]
            bounds.append(location)
            students = int(point.get("student_count", 0) or 0)
            popup = (
                f"<b>{cluster_id}</b><br>"
                f"{students} student(s)<br>"
                f"{point.get('formatted_address') or point.get('address') or ''}<br>"
                f"Vehicle: {selected.get('display_name', 'No feasible vehicle')}"
            )
            folium.CircleMarker(
                location=location,
                radius=max(5, min(14, 4 + students)),
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.78,
                tooltip=f"{cluster_id}: {students} student(s)",
                popup=popup,
            ).add_to(fmap)

    if len(bounds) > 1:
        fmap.fit_bounds(bounds, padding=(20, 20))
    legend_rows = []
    for index, cluster in enumerate(list(cluster_result.get("clusters") or [])[:12]):
        color = CLUSTER_COLORS[index % len(CLUSTER_COLORS)]
        cluster_id = str(cluster.get("cluster_id", "Cluster"))
        legend_rows.append(
            "<div style='display:flex;align-items:center;gap:8px;margin-top:4px;'>"
            f"<span style='display:inline-block;width:12px;height:12px;border-radius:999px;background:{color};"
            "border:1px solid rgba(17,24,39,0.22);'></span>"
            f"<span>{cluster_id}: {int(cluster.get('student_count', 0) or 0)} students</span>"
            "</div>"
        )
    panel = (
        "<div style='position:fixed;top:12px;right:12px;z-index:9999;width:320px;max-height:72vh;overflow:auto;"
        "background:rgba(255,255,255,0.94);padding:12px 14px;border-radius:10px;box-shadow:0 6px 18px rgba(0,0,0,0.15);"
        "font-family:Arial,sans-serif;font-size:14px;line-height:1.45;'>"
        "<h3 style='margin:0 0 8px 0;'>Demand Group Summary</h3>"
        f"<div><b>Groups:</b> {len(list(cluster_result.get('clusters') or []))}</div>"
        f"<div><b>Students:</b> {sum(int(cluster.get('student_count', 0) or 0) for cluster in list(cluster_result.get('clusters') or []))}</div>"
        "<div style='margin-top:8px;'><b>Group colors</b></div>"
        f"{''.join(legend_rows)}"
        "</div>"
    )
    fmap.get_root().html.add_child(Element(panel))
    return fmap._repr_html_()
