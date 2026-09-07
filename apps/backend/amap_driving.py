from __future__ import annotations

import math
from typing import Any


AMAP_DRIVING_ENDPOINT = "/v5/direction/driving"
AMAP_DRIVING_STRATEGY = "32"
AMAP_DRIVING_VERSION = "amap-v5-strategy32-v2"


def _coordinate_delta(lat: float, lng: float) -> tuple[float, float]:
    if not (73.66 < lng < 135.05 and 3.86 < lat < 53.55):
        return 0.0, 0.0
    x, y = lng - 105.0, lat - 35.0
    dlat = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlat += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
    dlat += (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3
    dlng = 300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    dlng += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlng += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
    dlng += (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3
    rad = math.radians(lat)
    magic = 1 - 0.00669342162296594323 * math.sin(rad)**2
    root = math.sqrt(magic)
    return (dlat*180 / ((6378245*(1-0.00669342162296594323))/(magic*root)*math.pi),
            dlng*180 / (6378245/root*math.cos(rad)*math.pi))


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    dlat, dlng = _coordinate_delta(lat, lng)
    return lat + dlat, lng + dlng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    dlat, dlng = _coordinate_delta(lat, lng)
    return lat - dlat, lng - dlng


def amap_request_point(point: dict[str, Any]) -> tuple[float, float] | None:
    def coordinates(lat: Any, lng: Any) -> tuple[float, float] | None:
        try:
            a, b = float(lat), float(lng)
        except (TypeError, ValueError):
            return None
        return (a, b) if math.isfinite(a) and math.isfinite(b) and abs(a) <= 90 and abs(b) <= 180 else None

    raw = coordinates(point.get("lat"), point.get("lng"))
    system = str(point.get("coordinate_system") or "").upper().replace("-", "")
    provider = str(point.get("provider") or point.get("geocode_provider") or "").lower()
    if raw and (system == "GCJ02" or (system != "WGS84" and (provider == "amap" or point.get("adcode")))):
        return raw
    plot = coordinates(point.get("plot_lat"), point.get("plot_lng"))
    value = plot or raw
    return wgs84_to_gcj02(*value) if value else None


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
        "show_fields": "cost,navi,polyline" if include_geometry else "cost",
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
