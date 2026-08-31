from __future__ import annotations

from typing import Any


AMAP_DRIVING_ENDPOINT = "/v5/direction/driving"
AMAP_DRIVING_STRATEGY = "32"
AMAP_DRIVING_VERSION = "amap-v5-strategy32-v1"


def build_amap_driving_params(
    request_points: list[tuple[float, float]],
    *,
    include_geometry: bool,
) -> dict[str, str]:
    if len(request_points) < 2:
        return {}
    origin_lat, origin_lng = request_points[0]
    destination_lat, destination_lng = request_points[-1]
    params = {
        "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
        "destination": f"{destination_lng:.6f},{destination_lat:.6f}",
        "strategy": AMAP_DRIVING_STRATEGY,
        "show_fields": "cost,navi" if include_geometry else "cost",
    }
    waypoints = [
        f"{lng:.6f},{lat:.6f}" for lat, lng in request_points[1:-1]
    ]
    if waypoints:
        params["waypoints"] = ";".join(waypoints)
    return params


def first_amap_driving_path(payload: dict[str, Any]) -> dict[str, Any] | None:
    paths = list(dict(payload.get("route") or {}).get("paths") or [])
    if not paths:
        return None
    return dict(paths[0] or {})


def amap_driving_path_stats(path: dict[str, Any]) -> dict[str, float]:
    cost = dict(path.get("cost") or {})
    return {
        "duration_s": float(cost.get("duration") or path.get("duration") or 0.0),
        "distance_m": float(path.get("distance") or 0.0),
    }


def amap_driving_path_polylines(path: dict[str, Any]) -> list[str]:
    polylines: list[str] = []
    for step in list(path.get("steps") or []):
        polyline = str(dict(step or {}).get("polyline") or "").strip()
        if polyline:
            polylines.append(polyline)
    if not polylines:
        polyline = str(path.get("polyline") or "").strip()
        if polyline:
            polylines.append(polyline)
    return polylines


def amap_distance_is_anomalous(
    provider_distance_m: float | int | None,
    expected_distance_m: float | int | None,
    *,
    ratio: float = 1.45,
    minimum_excess_m: float = 3000.0,
) -> bool:
    provider_distance = float(provider_distance_m or 0.0)
    expected_distance = float(expected_distance_m or 0.0)
    if provider_distance <= 0.0 or expected_distance <= 0.0:
        return False
    return (
        provider_distance >= expected_distance * max(1.0, float(ratio))
        and provider_distance - expected_distance >= max(0.0, float(minimum_excess_m))
    )
