from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Callable

from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parent
CLIENT_DIR = BASE_DIR.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

try:
    from . import planner_core
except ImportError:  # pragma: no cover - direct worker execution
    import planner_core  # type: ignore

import distance_tool  # type: ignore


DEFAULT_ANALYSIS_CONFIG: dict[str, Any] = {
    "service_direction": "To School",
    "stop_service_minutes": 1.0,
    "time_window_start": "06:30",
    "time_window_end": "08:00",
    "from_school_departure_time": "15:40",
    "far_distance_km": 20.0,
    "far_duration_minutes": 45.0,
    "burden_minutes": 15.0,
    "bypass_candidate_limit": 10,
    "candidate_cluster_radius_km": 3.0,
    "provider_call_limit": 500,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _address_key(item: dict[str, Any]) -> str:
    parts = (
        _normalized_text(item.get("country")),
        _normalized_text(item.get("city")),
        _normalized_text(item.get("address") or item.get("display_address")),
    )
    return "|".join(parts)


def _stop_key(item: dict[str, Any]) -> str:
    return hashlib.sha1(_address_key(item).encode("utf-8")).hexdigest()[:16]


def _point_coordinates(point: dict[str, Any]) -> tuple[float, float] | None:
    lat = _safe_float(point.get("plot_lat", point.get("lat")), math.nan)
    lng = _safe_float(point.get("plot_lng", point.get("lng")), math.nan)
    if not math.isfinite(lat) or not math.isfinite(lng):
        return None
    return lat, lng


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def _clock_minutes(value: Any, default: int) -> int:
    parts = str(value or "").strip().split(":")
    if len(parts) < 2:
        return default
    try:
        return (int(parts[0]) * 60 + int(parts[1])) % (24 * 60)
    except ValueError:
        return default


def _clock_label(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _percentile(values: list[float], quantile: float) -> float | None:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(1.0, max(0.0, quantile))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _analysis_config(value: dict[str, Any] | None) -> dict[str, Any]:
    config = {**DEFAULT_ANALYSIS_CONFIG, **dict(value or {})}
    config["service_direction"] = (
        "To School" if str(config.get("service_direction")) == "To School" else "From School"
    )
    for key in (
        "stop_service_minutes",
        "far_distance_km",
        "far_duration_minutes",
        "burden_minutes",
        "candidate_cluster_radius_km",
    ):
        config[key] = max(0.0, _safe_float(config.get(key), _safe_float(DEFAULT_ANALYSIS_CONFIG[key])))
    config["bypass_candidate_limit"] = max(0, min(50, _safe_int(config.get("bypass_candidate_limit"), 10)))
    config["provider_call_limit"] = max(1, min(2000, _safe_int(config.get("provider_call_limit"), 500)))
    return config


def _point_lookup(
    input_records: list[dict[str, Any]],
    points: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for point in points:
        key_candidates = {
            _address_key(point),
            _normalized_text(point.get("display_address")),
        }
        for member in list(point.get("original_members") or []):
            key_candidates.add(_normalized_text(member))
        for key in key_candidates:
            if key:
                lookup.setdefault(key, dict(point))
    if len(input_records) == len(points):
        for record, point in zip(input_records, points):
            lookup.setdefault(_address_key(record), dict(point))
            lookup.setdefault(_normalized_text(record.get("address")), dict(point))
    return lookup


def _lookup_point(item: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    point = lookup.get(_address_key(item)) or lookup.get(_normalized_text(item.get("address")))
    return dict(point) if point else None


def _provider_for_records(input_records: list[dict[str, Any]]) -> str:
    country, _city = planner_core.infer_traffic_location(input_records)
    normalized = str(country or "").strip().upper()
    if normalized == "CHINA":
        return "amap"
    if normalized == "SOUTH KOREA":
        return "kakao_navi"
    return "none"


class FreshRouteProvider:
    def __init__(
        self,
        provider: str,
        *,
        departure_time: datetime | None,
        api_call_limit: int,
    ) -> None:
        self.provider = provider
        self.departure_time = departure_time
        self.planner = planner_core.load_legacy_planner()
        self.cache: dict[str, Any] = {}
        self.state: dict[str, int] = {
            "api_calls": 0,
            "api_call_limit": api_call_limit,
            "cache_hits": 0,
        }
        if provider == "amap" and not str(getattr(self.planner, "AMAP_KEY", "") or "").strip():
            raise RuntimeError("AMap route API is not configured for this deployment.")
        if provider == "kakao_navi" and not planner_core.KAKAO_NAVI_API_KEY:
            raise RuntimeError("Kakao Navi route API is not configured for this deployment.")
        if provider == "none":
            raise RuntimeError("No live route provider is configured for this workbook market.")

    def route(self, points: list[dict[str, Any]]) -> dict[str, Any]:
        request_points = [coords for point in points if (coords := _point_coordinates(point))]
        if len(request_points) != len(points) or len(request_points) < 2:
            raise RuntimeError("Route contains unresolved coordinates.")
        before_calls = int(self.state.get("api_calls", 0))
        called_at = utc_now_iso()
        if self.provider == "amap":
            result = planner_core._amap_route_stats(
                self.planner, request_points, self.cache, self.state
            )
        else:
            departure = self.departure_time or datetime.now(timezone.utc) + timedelta(minutes=10)
            result = planner_core._kakao_route_stats(
                request_points,
                self.cache,
                self.state,
                departure_time=departure,
            )
        if not result:
            raise RuntimeError("Live route provider returned no usable route.")
        return {
            **dict(result),
            "provider": self.provider,
            "called_at": called_at,
            "api_calls": int(self.state.get("api_calls", 0)) - before_calls,
            "in_run_reuse": int(self.state.get("api_calls", 0)) == before_calls,
        }


def _osrm_leg(
    origin: dict[str, Any],
    destination: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    origin_coords = _point_coordinates(origin)
    destination_coords = _point_coordinates(destination)
    if not origin_coords or not destination_coords:
        raise RuntimeError("OSRM route contains unresolved coordinates.")
    key = "|".join(
        f"{value:.6f}" for value in (*origin_coords, *destination_coords)
    )
    if key not in cache:
        detail = distance_tool.compute_osrm_route_leg_details([origin, destination])[0]
        geometry = [
            [float(lng), float(lat)]
            for lat, lng in list(detail.get("geometry") or [])
        ]
        cache[key] = {
            "duration_s": detail.get("duration_s"),
            "distance_m": detail.get("distance_m"),
            "geometry": geometry,
        }
    return deepcopy(cache[key])


def _rotate(items: list[Any], seed: str) -> list[Any]:
    if len(items) < 2:
        return list(items)
    offset = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16) % len(items)
    return [*items[offset:], *items[:offset]]


def _base_result(
    *,
    config: dict[str, Any],
    provider: str,
    school: dict[str, Any],
    rows: list[dict[str, Any]],
    route_count: int,
    logical_call_estimate: int,
    progress: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "analysis_version": 1,
        "analysis_type": "direct_school",
        "status": "running",
        "generated_at": utc_now_iso(),
        "provider": provider,
        "service_direction": config["service_direction"],
        "school": school,
        "parameters": config,
        "summary": {
            "address_count": len(rows),
            "route_count": route_count,
            "resolved_count": sum(1 for row in rows if row.get("provider_status") == "resolved"),
            "failed_count": sum(1 for row in rows if row.get("provider_status") == "failed"),
            "logical_call_estimate": logical_call_estimate,
        },
        "progress": progress,
        "stops": rows,
        "routes": [],
        "candidate_clusters": [],
        "errors": errors,
    }


def run_direct_school_analysis(
    prepared_payload: dict[str, Any],
    analysis_config: dict[str, Any] | None = None,
    *,
    scheduled_start_at: str | None = None,
    run_seed: str = "",
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    resume_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _analysis_config(analysis_config)
    input_records = [dict(item) for item in list(prepared_payload.get("input_records") or [])]
    points = [dict(item) for item in list(prepared_payload.get("original_points") or [])]
    current_plan = dict(prepared_payload.get("current_plan") or {})
    stops = [dict(item) for item in list(current_plan.get("stops") or [])]
    if not input_records or not points or not stops:
        raise ValueError("Direct-to-school analysis requires a validated Audit workbook.")

    provider_name = _provider_for_records(input_records)
    departure_time = None
    if scheduled_start_at:
        try:
            departure_time = datetime.fromisoformat(str(scheduled_start_at).replace("Z", "+00:00"))
        except ValueError:
            departure_time = None
    provider = FreshRouteProvider(
        provider_name,
        departure_time=departure_time,
        api_call_limit=int(config["provider_call_limit"]),
    )
    lookup = _point_lookup(input_records, points)
    school_record = input_records[0]
    school_point = _lookup_point(school_record, lookup)
    if not school_point:
        raise ValueError("The shared school address could not be geocoded.")
    school_coords = _point_coordinates(school_point)
    if not school_coords:
        raise ValueError("The shared school address could not be geocoded.")
    school = {
        "country": school_record.get("country"),
        "city": school_record.get("city"),
        "address": school_record.get("address"),
        "lat": school_coords[0],
        "lng": school_coords[1],
    }

    route_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unique_stops: dict[str, dict[str, Any]] = {}
    for stop in stops:
        route_id = str(stop.get("route_id") or "").strip()
        route_groups[route_id].append(stop)
        if bool(stop.get("is_depot")):
            continue
        key = _address_key(stop)
        item = unique_stops.setdefault(
            key,
            {
                "stop_key": _stop_key(stop),
                "country": stop.get("country"),
                "city": stop.get("city"),
                "address": stop.get("address"),
                "riders": 0,
                "route_ids": [],
                "occurrences": [],
                "point": _lookup_point(stop, lookup),
            },
        )
        item["riders"] += max(0, _safe_int(stop.get("passenger_count")))
        if route_id and route_id not in item["route_ids"]:
            item["route_ids"].append(route_id)
        item["occurrences"].append(
            {
                "route_id": route_id,
                "stop_sequence": _safe_int(stop.get("stop_sequence")),
                "passenger_count": max(0, _safe_int(stop.get("passenger_count"))),
            }
        )

    prior_rows = {
        str(row.get("stop_key") or ""): dict(row)
        for row in list(dict(resume_result or {}).get("stops") or [])
        if str(row.get("stop_key") or "")
    }
    rows: list[dict[str, Any]] = []
    for item in unique_stops.values():
        point = dict(item.pop("point") or {})
        coords = _point_coordinates(point)
        row = {
            **item,
            "lat": coords[0] if coords else None,
            "lng": coords[1] if coords else None,
            "provider_status": "pending" if coords else "failed",
            "quality_status": "ready" if coords else "geocode_failed",
            "recommendation": "data_review" if not coords else "pending",
            "reasons": ["Address could not be geocoded."] if not coords else [],
        }
        previous = prior_rows.get(str(row["stop_key"]))
        if previous and previous.get("provider_status") == "resolved":
            row.update(previous)
        row["_point"] = point
        rows.append(row)

    bypass_limit = min(int(config["bypass_candidate_limit"]), len(rows))
    logical_call_estimate = len(rows) + len(route_groups) + bypass_limit
    errors: list[dict[str, Any]] = list(dict(resume_result or {}).get("errors") or [])
    progress = {
        "phase": "direct_routes",
        "completed": sum(1 for row in rows if row.get("provider_status") == "resolved"),
        "total": logical_call_estimate,
        "provider_api_calls": int(provider.state.get("api_calls", 0)),
        "in_run_reuse_count": int(provider.state.get("cache_hits", 0)),
    }
    osrm_cache: dict[str, dict[str, Any]] = {}

    def save_checkpoint() -> None:
        if not checkpoint:
            return
        public_rows = [{key: value for key, value in row.items() if key != "_point"} for row in rows]
        checkpoint(
            _base_result(
                config=config,
                provider=provider_name,
                school=school,
                rows=public_rows,
                route_count=len(route_groups),
                logical_call_estimate=logical_call_estimate,
                progress=deepcopy(progress),
                errors=deepcopy(errors),
            )
        )

    direct_order = _rotate(rows, run_seed or scheduled_start_at or utc_now_iso())
    for row in direct_order:
        if row.get("provider_status") == "resolved":
            continue
        point = dict(row.get("_point") or {})
        if not point:
            progress["completed"] += 1
            save_checkpoint()
            continue
        request_points = [point, school_point]
        if config["service_direction"] == "From School":
            request_points.reverse()
        try:
            osrm = _osrm_leg(request_points[0], request_points[1], osrm_cache)
            live = provider.route(request_points)
            straight_km = _haversine_km(_point_coordinates(point), school_coords)  # type: ignore[arg-type]
            direct_distance_m = _safe_float(live.get("distance_m"))
            direct_duration_s = _safe_float(live.get("duration_s"))
            osrm_distance_m = _safe_float(osrm.get("distance_m"))
            osrm_duration_s = _safe_float(osrm.get("duration_s"))
            row.update(
                {
                    "provider_status": "resolved",
                    "quality_status": "ready",
                    "provider": provider_name,
                    "provider_called_at": live.get("called_at"),
                    "direct_distance_km": round(direct_distance_m / 1000.0, 3),
                    "direct_duration_min": round(direct_duration_s / 60.0, 2),
                    "direct_service_duration_min": round(
                        direct_duration_s / 60.0 + float(config["stop_service_minutes"]), 2
                    ),
                    "osrm_distance_km": round(osrm_distance_m / 1000.0, 3),
                    "osrm_duration_min": round(osrm_duration_s / 60.0, 2),
                    "straight_distance_km": round(straight_km, 3),
                    "congestion_increment_min": round((direct_duration_s - osrm_duration_s) / 60.0, 2),
                    "live_to_osrm_ratio": round(direct_duration_s / osrm_duration_s, 3) if osrm_duration_s > 0 else None,
                    "road_to_straight_ratio": round((direct_distance_m / 1000.0) / straight_km, 3) if straight_km > 0 else None,
                    "direct_geometry": osrm.get("geometry") or [],
                }
            )
            if config["service_direction"] == "To School":
                latest_arrival = _clock_minutes(config.get("time_window_end"), 8 * 60)
                row["latest_direct_departure"] = _clock_label(latest_arrival - direct_duration_s / 60.0)
            else:
                departure = _clock_minutes(config.get("from_school_departure_time"), 15 * 60 + 40)
                row["estimated_direct_arrival"] = _clock_label(departure + direct_duration_s / 60.0)
        except Exception as exc:
            row["provider_status"] = "failed"
            row["quality_status"] = "provider_failed"
            row["recommendation"] = "data_review"
            row["reasons"] = [str(exc)]
            errors.append({"scope": "direct_route", "stop_key": row["stop_key"], "address": row["address"], "error": str(exc)})
        progress["completed"] += 1
        progress["provider_api_calls"] = int(provider.state.get("api_calls", 0))
        progress["in_run_reuse_count"] = int(provider.state.get("cache_hits", 0))
        save_checkpoint()

    progress["phase"] = "current_routes"
    route_results: list[dict[str, Any]] = []
    route_contexts_by_stop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    route_runtime: dict[str, dict[str, Any]] = {}
    route_order = _rotate(sorted(route_groups), f"route|{run_seed or scheduled_start_at or ''}")
    for route_id in route_order:
        ordered = sorted(route_groups[route_id], key=lambda item: _safe_int(item.get("stop_sequence")))
        route_points = [_lookup_point(stop, lookup) for stop in ordered]
        if any(point is None for point in route_points):
            error = "One or more route stops could not be geocoded."
            route_results.append({"route_id": route_id, "status": "partial", "error": error})
            errors.append({"scope": "current_route", "route_id": route_id, "error": error})
            progress["completed"] += 1
            save_checkpoint()
            continue
        resolved_points = [dict(point) for point in route_points if point]
        try:
            leg_details = [
                _osrm_leg(origin, destination, osrm_cache)
                for origin, destination in zip(resolved_points[:-1], resolved_points[1:])
            ]
            osrm_drive_s = sum(_safe_float(leg.get("duration_s")) for leg in leg_details)
            osrm_distance_m = sum(_safe_float(leg.get("distance_m")) for leg in leg_details)
            live = provider.route(resolved_points)
            live_drive_s = _safe_float(live.get("duration_s"))
            live_distance_m = _safe_float(live.get("distance_m"))
            service_stop_count = sum(1 for stop in ordered if not bool(stop.get("is_depot")))
            dwell_s = service_stop_count * float(config["stop_service_minutes"]) * 60.0
            scale = live_drive_s / osrm_drive_s if osrm_drive_s > 0 else 1.0
            geometry = [coord for leg in leg_details for coord in list(leg.get("geometry") or [])]
            route_result = {
                "route_id": route_id,
                "status": "resolved",
                "stop_count": service_stop_count,
                "riders": sum(max(0, _safe_int(stop.get("passenger_count"))) for stop in ordered),
                "provider_duration_min": round(live_drive_s / 60.0, 2),
                "total_duration_min": round((live_drive_s + dwell_s) / 60.0, 2),
                "provider_distance_km": round(live_distance_m / 1000.0, 3),
                "osrm_duration_min": round(osrm_drive_s / 60.0, 2),
                "osrm_distance_km": round(osrm_distance_m / 1000.0, 3),
                "congestion_ratio": round(scale, 3),
                "provider_called_at": live.get("called_at"),
                "geometry": geometry,
            }
            route_results.append(route_result)
            route_runtime[route_id] = {
                "ordered": ordered,
                "points": resolved_points,
                "legs": leg_details,
                "live_drive_s": live_drive_s,
                "dwell_s": dwell_s,
                "scale": scale,
                "full_total_s": live_drive_s + dwell_s,
                "full_distance_m": live_distance_m,
            }
            service_indexes = [index for index, stop in enumerate(ordered) if not bool(stop.get("is_depot"))]
            for index in service_indexes:
                stop = ordered[index]
                key = _address_key(stop)
                if config["service_direction"] == "To School":
                    relevant_legs = leg_details[index:]
                    dwell_count = sum(1 for service_index in service_indexes if service_index >= index)
                else:
                    relevant_legs = leg_details[:index]
                    dwell_count = sum(1 for service_index in service_indexes if service_index <= index)
                ride_s = sum(_safe_float(leg.get("duration_s")) for leg in relevant_legs) * scale
                ride_s += dwell_count * float(config["stop_service_minutes"]) * 60.0
                route_contexts_by_stop[key].append(
                    {
                        "route_id": route_id,
                        "stop_sequence": _safe_int(stop.get("stop_sequence")),
                        "estimated_current_ride_min": round(ride_s / 60.0, 2),
                        "route_total_min": route_result["total_duration_min"],
                    }
                )
        except Exception as exc:
            route_results.append({"route_id": route_id, "status": "failed", "error": str(exc)})
            errors.append({"scope": "current_route", "route_id": route_id, "error": str(exc)})
        progress["completed"] += 1
        progress["provider_api_calls"] = int(provider.state.get("api_calls", 0))
        progress["in_run_reuse_count"] = int(provider.state.get("cache_hits", 0))
        save_checkpoint()

    for row in rows:
        contexts = route_contexts_by_stop.get(_address_key(row), [])
        row["route_contexts"] = contexts
        if contexts:
            worst = max(contexts, key=lambda item: _safe_float(item.get("estimated_current_ride_min")))
            row["primary_route_id"] = worst.get("route_id")
            row["estimated_current_ride_min"] = worst.get("estimated_current_ride_min")
            if row.get("direct_duration_min") is not None:
                row["rider_detour_min"] = round(
                    _safe_float(worst.get("estimated_current_ride_min")) - _safe_float(row.get("direct_duration_min")),
                    2,
                )

    progress["phase"] = "bypass_checks"
    bypass_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        for context in list(row.get("route_contexts") or []):
            score = _safe_float(row.get("direct_duration_min")) + max(0.0, _safe_float(row.get("rider_detour_min"))) * 1.5
            bypass_candidates.append((score, row, context))
    bypass_candidates.sort(key=lambda item: item[0], reverse=True)
    for _score, row, context in bypass_candidates[:bypass_limit]:
        route_id = str(context.get("route_id") or "")
        runtime = route_runtime.get(route_id)
        if not runtime:
            progress["completed"] += 1
            continue
        ordered = list(runtime["ordered"])
        sequence = _safe_int(context.get("stop_sequence"))
        remove_index = next(
            (index for index, stop in enumerate(ordered) if _safe_int(stop.get("stop_sequence")) == sequence),
            -1,
        )
        if remove_index < 0 or len(ordered) <= 2:
            progress["completed"] += 1
            continue
        bypass_points = [point for index, point in enumerate(runtime["points"]) if index != remove_index]
        try:
            live = provider.route(bypass_points)
            bypass_total_s = _safe_float(live.get("duration_s")) + max(
                0,
                sum(1 for stop in ordered if not bool(stop.get("is_depot"))) - 1,
            ) * float(config["stop_service_minutes"]) * 60.0
            burden_min = max(0.0, (_safe_float(runtime["full_total_s"]) - bypass_total_s) / 60.0)
            burden_km = max(0.0, (_safe_float(runtime["full_distance_m"]) - _safe_float(live.get("distance_m"))) / 1000.0)
            context["marginal_route_burden_min"] = round(burden_min, 2)
            context["marginal_route_burden_km"] = round(burden_km, 3)
            row["marginal_route_burden_min"] = round(max(_safe_float(row.get("marginal_route_burden_min")), burden_min), 2)
            row["marginal_route_burden_km"] = round(max(_safe_float(row.get("marginal_route_burden_km")), burden_km), 3)
        except Exception as exc:
            errors.append({"scope": "bypass_check", "route_id": route_id, "stop_key": row["stop_key"], "error": str(exc)})
        progress["completed"] += 1
        progress["provider_api_calls"] = int(provider.state.get("api_calls", 0))
        progress["in_run_reuse_count"] = int(provider.state.get("cache_hits", 0))
        save_checkpoint()

    far_distance = float(config["far_distance_km"])
    far_duration = float(config["far_duration_minutes"])
    burden_threshold = float(config["burden_minutes"])
    for row in rows:
        if row.get("provider_status") != "resolved":
            row["recommendation"] = "data_review"
            continue
        direct_far = _safe_float(row.get("direct_distance_km")) >= far_distance or _safe_float(row.get("direct_duration_min")) >= far_duration
        route_burden = max(
            _safe_float(row.get("rider_detour_min")),
            _safe_float(row.get("marginal_route_burden_min")),
        )
        reasons: list[str] = []
        if direct_far:
            reasons.append("Direct trip exceeds the configured distance or duration threshold.")
        if _safe_float(row.get("rider_detour_min")) >= burden_threshold:
            reasons.append("The rider's current in-vehicle detour exceeds the configured burden threshold.")
        if _safe_float(row.get("marginal_route_burden_min")) >= burden_threshold:
            reasons.append("Removing this stop materially reduces its current route duration.")
        if direct_far and route_burden >= burden_threshold:
            recommendation = "dedicated_candidate"
        elif route_burden >= burden_threshold:
            recommendation = "route_adjustment"
        elif direct_far:
            recommendation = "far_not_main_cause"
            reasons.append("The stop is remote, but its measured route burden is below the configured threshold.")
        else:
            recommendation = "within_range"
            reasons.append("Direct remoteness and current-route burden are both below the configured thresholds.")
        row["recommendation"] = recommendation
        row["reasons"] = reasons
        row["risk_score"] = round(
            (_safe_float(row.get("direct_duration_min")) / max(1.0, far_duration)) * 35
            + (_safe_float(row.get("direct_distance_km")) / max(1.0, far_distance)) * 20
            + (max(0.0, _safe_float(row.get("rider_detour_min"))) / max(1.0, burden_threshold)) * 25
            + (_safe_float(row.get("marginal_route_burden_min")) / max(1.0, burden_threshold)) * 20,
            1,
        )

    rows.sort(key=lambda row: (_safe_float(row.get("risk_score")), _safe_float(row.get("direct_duration_min"))), reverse=True)
    clusters = _cluster_candidates(rows, float(config["candidate_cluster_radius_km"]))
    resolved_rows = [row for row in rows if row.get("provider_status") == "resolved"]
    result = _base_result(
        config=config,
        provider=provider_name,
        school=school,
        rows=[{key: value for key, value in row.items() if key != "_point"} for row in rows],
        route_count=len(route_groups),
        logical_call_estimate=logical_call_estimate,
        progress={
            "phase": "complete",
            "completed": logical_call_estimate,
            "total": logical_call_estimate,
            "provider_api_calls": int(provider.state.get("api_calls", 0)),
            "in_run_reuse_count": int(provider.state.get("cache_hits", 0)),
        },
        errors=errors,
    )
    result["status"] = "partial" if errors else "complete"
    result["completed_at"] = utc_now_iso()
    result["routes"] = sorted(route_results, key=lambda row: str(row.get("route_id") or ""))
    result["candidate_clusters"] = clusters
    result["summary"].update(
        {
            "resolved_count": len(resolved_rows),
            "failed_count": len(rows) - len(resolved_rows),
            "dedicated_candidate_count": sum(1 for row in rows if row.get("recommendation") == "dedicated_candidate"),
            "route_adjustment_count": sum(1 for row in rows if row.get("recommendation") == "route_adjustment"),
            "far_not_main_cause_count": sum(1 for row in rows if row.get("recommendation") == "far_not_main_cause"),
            "candidate_cluster_count": len(clusters),
            "max_direct_duration_min": round(max((_safe_float(row.get("direct_duration_min")) for row in resolved_rows), default=0.0), 2),
            "max_direct_distance_km": round(max((_safe_float(row.get("direct_distance_km")) for row in resolved_rows), default=0.0), 3),
            "provider_api_calls": int(provider.state.get("api_calls", 0)),
            "in_run_reuse_count": int(provider.state.get("cache_hits", 0)),
        }
    )
    return result


def _cluster_candidates(rows: list[dict[str, Any]], radius_km: float) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row.get("recommendation") == "dedicated_candidate"
        and row.get("lat") is not None
        and row.get("lng") is not None
    ]
    unvisited = set(range(len(candidates)))
    clusters: list[dict[str, Any]] = []
    while unvisited:
        seed = unvisited.pop()
        members = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            current_coords = (_safe_float(candidates[current]["lat"]), _safe_float(candidates[current]["lng"]))
            nearby = {
                index
                for index in list(unvisited)
                if _haversine_km(
                    current_coords,
                    (_safe_float(candidates[index]["lat"]), _safe_float(candidates[index]["lng"])),
                ) <= radius_km
            }
            unvisited -= nearby
            members |= nearby
            frontier.extend(nearby)
        member_rows = [candidates[index] for index in sorted(members)]
        clusters.append(
            {
                "cluster_id": f"C{len(clusters) + 1}",
                "stop_keys": [row["stop_key"] for row in member_rows],
                "addresses": [row["address"] for row in member_rows],
                "stop_count": len(member_rows),
                "riders": sum(_safe_int(row.get("riders")) for row in member_rows),
                "max_direct_duration_min": round(max(_safe_float(row.get("direct_duration_min")) for row in member_rows), 2),
                "max_direct_distance_km": round(max(_safe_float(row.get("direct_distance_km")) for row in member_rows), 3),
            }
        )
    return clusters


def aggregate_direct_school_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    samples_by_stop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    run_ids: list[str] = []
    for record in records:
        result = dict(record.get("result") or {})
        if str(record.get("status") or "") != "succeeded" or not result:
            continue
        run_id = str(record.get("job_id") or "")
        run_ids.append(run_id)
        sample_at = result.get("completed_at") or record.get("finished_at") or record.get("scheduled_start_at")
        for row in list(result.get("stops") or []):
            if row.get("provider_status") != "resolved":
                continue
            samples_by_stop[str(row.get("stop_key") or "")].append(
                {
                    "job_id": run_id,
                    "sample_at": sample_at,
                    "stop_key": row.get("stop_key"),
                    "address": row.get("address"),
                    "direct_duration_min": row.get("direct_duration_min"),
                    "direct_distance_km": row.get("direct_distance_km"),
                    "rider_detour_min": row.get("rider_detour_min"),
                    "marginal_route_burden_min": row.get("marginal_route_burden_min"),
                    "recommendation": row.get("recommendation"),
                }
            )
    rows: list[dict[str, Any]] = []
    all_samples: list[dict[str, Any]] = []
    for stop_key, samples in samples_by_stop.items():
        all_samples.extend(samples)
        durations = [_safe_float(sample.get("direct_duration_min")) for sample in samples]
        distances = [_safe_float(sample.get("direct_distance_km")) for sample in samples]
        dedicated_count = sum(1 for sample in samples if sample.get("recommendation") == "dedicated_candidate")
        rows.append(
            {
                "stop_key": stop_key,
                "address": samples[0].get("address"),
                "sample_count": len(samples),
                "duration_median_min": round(statistics.median(durations), 2),
                "duration_p90_min": round(_percentile(durations, 0.9) or 0.0, 2),
                "duration_max_min": round(max(durations), 2),
                "duration_variability_min": round(statistics.pstdev(durations), 2) if len(durations) > 1 else 0.0,
                "distance_median_km": round(statistics.median(distances), 3),
                "dedicated_candidate_rate": round(dedicated_count / len(samples), 3),
                "persistent_candidate": len(samples) >= 2 and dedicated_count / len(samples) >= 0.6,
            }
        )
    rows.sort(key=lambda row: (_safe_float(row.get("dedicated_candidate_rate")), _safe_float(row.get("duration_p90_min"))), reverse=True)
    return {
        "run_count": len(set(run_ids)),
        "run_ids": list(dict.fromkeys(run_ids)),
        "stop_count": len(rows),
        "stops": rows,
        "samples": sorted(all_samples, key=lambda row: str(row.get("sample_at") or ""), reverse=True),
    }


def build_direct_school_workbook(
    record: dict[str, Any],
    multi_day: dict[str, Any] | None = None,
) -> bytes:
    result = dict(record.get("result") or {})
    if not result:
        raise ValueError("Direct-to-school analysis result is not available.")
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_sheet.append(["Field", "Value"])
    summary_values = {
        "Job ID": record.get("job_id"),
        "Title": dict(record.get("metadata") or {}).get("job_name"),
        "Status": result.get("status"),
        "Provider": result.get("provider"),
        "Service direction": result.get("service_direction"),
        "School": dict(result.get("school") or {}).get("address"),
        "Completed at": result.get("completed_at"),
        **dict(result.get("summary") or {}),
    }
    for key, value in summary_values.items():
        summary_sheet.append([key, _excel_value(value)])
    summary_sheet.append([])
    summary_sheet.append(["Parameter", "Value"])
    for key, value in dict(result.get("parameters") or {}).items():
        summary_sheet.append([key, _excel_value(value)])

    stop_columns = [
        "stop_key", "address", "city", "country", "riders", "route_ids",
        "primary_route_id", "recommendation", "risk_score", "direct_distance_km",
        "direct_duration_min", "direct_service_duration_min", "osrm_distance_km",
        "osrm_duration_min", "straight_distance_km", "congestion_increment_min",
        "live_to_osrm_ratio", "road_to_straight_ratio", "estimated_current_ride_min",
        "rider_detour_min", "marginal_route_burden_min", "marginal_route_burden_km",
        "latest_direct_departure", "estimated_direct_arrival", "provider_status",
        "provider_called_at", "quality_status", "reasons",
    ]
    _append_rows(workbook.create_sheet("Stop Results"), list(result.get("stops") or []), stop_columns)
    dedicated = [row for row in list(result.get("stops") or []) if row.get("recommendation") == "dedicated_candidate"]
    _append_rows(workbook.create_sheet("Dedicated Candidates"), dedicated, stop_columns)
    error_columns = ["scope", "stop_key", "address", "route_id", "error"]
    _append_rows(workbook.create_sheet("Errors"), list(result.get("errors") or []), error_columns)
    daily_columns = [
        "job_id", "sample_at", "stop_key", "address", "direct_duration_min",
        "direct_distance_km", "rider_detour_min", "marginal_route_burden_min", "recommendation",
    ]
    _append_rows(workbook.create_sheet("Daily Samples"), list(dict(multi_day or {}).get("samples") or []), daily_columns)
    from io import BytesIO

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _append_rows(sheet: Any, rows: list[dict[str, Any]], columns: list[str]) -> None:
    sheet.append(columns)
    for row in rows:
        sheet.append([_excel_value(row.get(column)) for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _excel_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
