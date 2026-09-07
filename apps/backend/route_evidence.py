"""Job-scoped AMap measurements shared by planning and report consumers."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Any, Callable

try:
    from .amap_driving import (
        AMAP_DRIVING_ENDPOINT, AMAP_DRIVING_VERSION, amap_driving_path_polylines,
        amap_driving_path_stats, build_amap_driving_params, first_amap_driving_path,
        gcj02_to_wgs84,
    )
except ImportError:
    from amap_driving import (
        AMAP_DRIVING_ENDPOINT, AMAP_DRIVING_VERSION, amap_driving_path_polylines,
        amap_driving_path_stats, build_amap_driving_params, first_amap_driving_path,
        gcj02_to_wgs84,
    )


EVIDENCE_VERSION = "amap-adjacent-evidence-v2"
CACHE_MAX_AGE_SECONDS = 600


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    value = math.sin((lat2 - lat1) / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 12742017.6 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def _key(points: list[tuple[float, float]]) -> str:
    content = [EVIDENCE_VERSION, AMAP_DRIVING_VERSION, [[round(x, 6) for x in p] for p in points]]
    return EVIDENCE_VERSION + "|" + hashlib.sha256(json.dumps(content).encode()).hexdigest()


def _geometry(path: dict[str, Any]) -> list[list[float]]:
    result: list[list[float]] = []
    for polyline in amap_driving_path_polylines(path):
        for value in polyline.split(";"):
            lng, lat = map(float, value.split(","))
            if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat) > 90 or abs(lng) > 180:
                raise ValueError("Invalid AMap geometry coordinate")
            lat, lng = gcj02_to_wgs84(lat, lng)
            pair = [lng, lat]
            if not result or result[-1] != pair:
                result.append(pair)
    return result


def fetch_amap_leg(planner: Any, points: list[tuple[float, float]]) -> dict[str, Any]:
    if len(points) != 2:
        raise ValueError("AMap evidence requires exactly two consecutive stops")
    return fetch_amap_itinerary(planner, points)


def fetch_amap_itinerary(planner: Any, points: list[tuple[float, float]]) -> dict[str, Any]:
    params = build_amap_driving_params(points, include_geometry=True)
    payload = planner.amap_request_json(AMAP_DRIVING_ENDPOINT, params, planner.AMAP_ROUTING_LIMITER)
    path = first_amap_driving_path(payload)
    if not path:
        raise ValueError("AMap returned no route")
    stats = amap_driving_path_stats(path)
    geometry = _geometry(path)
    if (any(not math.isfinite(value) or value <= 0 for value in stats.values())
            or len(geometry) < 2):
        raise ValueError("AMap returned incomplete distance, time or geometry")
    return {**stats, "geometry": geometry, "request": params,
            "roads": [str(step.get("road_name") or step.get("road") or "")
                      for step in path.get("steps") or []]}


def _leg_issues(leg: dict[str, Any], reference_distance_m: float | None) -> list[str]:
    issues: list[str] = []
    geometry = leg["geometry"]
    origin = tuple(leg["origin_wgs84"])
    destination = tuple(leg["destination_wgs84"])
    if distance_m(origin, tuple(reversed(geometry[0]))) > 150:
        issues.append("origin_road_snap_needs_review")
    if distance_m(destination, tuple(reversed(geometry[-1]))) > 150:
        issues.append("destination_road_snap_needs_review")
    measured = leg["distance_m"]
    # OSRM and straight distance are diagnostics, never replacement measurements.
    if reference_distance_m and reference_distance_m > 0:
        excess = measured - reference_distance_m
        if ((measured >= reference_distance_m * 1.8 and excess >= 500)
                or (measured >= reference_distance_m * 1.3 and excess >= 1000)):
            issues.append("provider_distance_disagreement")
    straight = leg["straight_distance_m"]
    if straight - measured > 200 and measured < straight * 0.9:
        issues.append("reported_distance_shorter_than_direct")
    if straight >= 20 and measured >= straight * 4 and measured - straight >= 1000:
        issues.append("large_direct_detour_needs_review")
    return issues


def _bearing(a: list[float], b: list[float]) -> float:
    lng1, lat1, lng2, lat2 = map(math.radians, (*a, *b))
    return math.degrees(math.atan2(math.sin(lng2 - lng1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lng2 - lng1)))


def _junction_issues(previous: dict[str, Any], following: dict[str, Any]) -> list[str]:
    before, after = previous["geometry"], following["geometry"]
    issues: list[str] = []
    if distance_m(tuple(reversed(before[-1])), tuple(reversed(after[0]))) > 50:
        issues.append("stop_road_continuity_needs_review")
    if len(before) >= 2 and len(after) >= 2:
        arrival = _bearing(before[-2], before[-1])
        departure = _bearing(after[0], after[1])
        difference = abs((departure - arrival + 180) % 360 - 180)
        if difference >= 135:
            issues.append("stop_turnaround_needs_review")
    return issues


def measure_amap_route(
    planner: Any,
    points: list[tuple[float, float]],
    cache: dict[str, Any],
    state: dict[str, Any],
    *,
    fetch_leg: Callable[[Any, list[tuple[float, float]]], dict[str, Any]] = fetch_amap_leg,
    fetch_context: Callable[[Any, list[tuple[float, float]]], dict[str, Any]] = fetch_amap_itinerary,
) -> dict[str, Any]:
    """Measure ordered directed edges, preserving raw evidence even on failure."""
    if len(points) < 2 or any(len(p) != 2 or not all(math.isfinite(x) for x in p)
                              or abs(p[0]) > 90 or abs(p[1]) > 180 for p in points):
        raise ValueError("Route contains unresolved coordinates")
    references = list(state.get("expected_leg_distances_m") or [])
    reference_times = list(state.get("expected_leg_durations_s") or [])
    legs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, (origin, destination) in enumerate(zip(points[:-1], points[1:])):
        key = _key([origin, destination])
        cached = dict(cache.get(key) or {})
        leg: dict[str, Any] = {}
        reference = float(references[index] or 0) if index < len(references) else None
        if distance_m(origin, destination) < 0.2:
            mapped = list(reversed(gcj02_to_wgs84(*origin)))
            leg = {"duration_s": 0.0, "distance_m": 0.0, "geometry": [mapped, mapped],
                   "origin_wgs84": list(reversed(mapped)), "destination_wgs84": list(reversed(mapped)),
                   "origin": list(origin), "destination": list(destination), "straight_distance_m": 0.0,
                   "called_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "source": "co_located_stops", "evidence_version": EVIDENCE_VERSION}
        elif (cached.get("evidence_version") == EVIDENCE_VERSION
                and 0 <= time.time() - float(cached.get("measured_at_epoch", 0)) <= CACHE_MAX_AGE_SECONDS):
            leg = deepcopy(cached)
            state["cache_hits"] = int(state.get("cache_hits", 0)) + 1
            leg["in_run_reuse"] = True
        else:
            attempts: list[dict[str, Any]] = []
            for attempt in range(2):
                if int(state.get("api_calls", 0)) >= int(state.get("api_call_limit", 0)):
                    issues.append({"leg_index": index, "code": "provider_call_budget_exhausted"})
                    break
                state["api_calls"] = int(state.get("api_calls", 0)) + 1
                called_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    measured = fetch_leg(planner, [origin, destination])
                    if (not measured.get("geometry") or len(measured["geometry"]) < 2
                            or not all(math.isfinite(float(measured.get(k, 0))) and float(measured.get(k, 0)) > 0
                                       for k in ("duration_s", "distance_m"))):
                        raise ValueError("Incomplete AMap segment evidence")
                    leg = {
                        **deepcopy(measured), "evidence_version": EVIDENCE_VERSION,
                        "provider": "amap", "coordinate_system": "WGS84",
                        "request_coordinate_system": "GCJ02", "origin": list(origin),
                        "destination": list(destination), "origin_wgs84": list(gcj02_to_wgs84(*origin)),
                        "destination_wgs84": list(gcj02_to_wgs84(*destination)),
                        "straight_distance_m": distance_m(origin, destination),
                        "called_at": called_at, "measured_at_epoch": time.time(),
                        "source": "amap_adjacent_legs", "in_run_reuse": False,
                    }
                    attempts.append({"called_at": called_at, "duration_s": leg["duration_s"],
                                     "distance_m": leg["distance_m"], "issues": _leg_issues(leg, reference)})
                    if not attempts[-1]["issues"] or attempt == 1:
                        break
                except Exception as exc:
                    # Provider exceptions can include request URLs and keys.
                    attempts.append({"called_at": called_at, "error_type": type(exc).__name__})
                    if attempt == 1:
                        issues.append({"leg_index": index, "code": "provider_leg_unavailable"})
            if leg:
                leg["attempts"] = attempts
                cache[key] = deepcopy(leg)
        if not leg:
            break
        leg["leg_index"] = index
        leg["osrm_reference_distance_m"] = reference
        leg["osrm_reference_duration_s"] = reference_times[index] if index < len(reference_times) else None
        leg["issues"] = _leg_issues(leg, reference)
        issues.extend({"leg_index": index, "code": code} for code in leg["issues"])
        if legs and leg["distance_m"] > 0 and legs[-1]["distance_m"] > 0:
            issues.extend({"leg_index": index, "code": code} for code in _junction_issues(legs[-1], leg))
        legs.append(leg)
    complete = len(legs) == len(points) - 1
    # Pair requests reset the approach to a stop. Preserve a continuous-waypoint
    # comparison for questionable junctions; never distribute its total time
    # across stops to invent missing segment measurements.
    context_checks: list[dict[str, Any]] = []
    junctions = sorted({int(issue["leg_index"]) for issue in issues
                        if issue["code"] in {"stop_road_continuity_needs_review", "stop_turnaround_needs_review"}})
    for index in junctions:
        check: dict[str, Any] = {"stop_index": index, "status": "unavailable"}
        context_checks.append(check)
        if len(context_checks) > 4 or int(state.get("api_calls", 0)) >= int(state.get("api_call_limit", 0)):
            check["reason"] = "context_check_budget_exhausted"
            continue
        context_points = points[index - 1:index + 2]
        context_key = "context|" + _key(context_points)
        cached_context = cache.get(context_key) or {}
        try:
            if (cached_context.get("evidence_version") == EVIDENCE_VERSION
                    and 0 <= time.time() - float(cached_context.get("measured_at_epoch", 0)) <= CACHE_MAX_AGE_SECONDS):
                snapshot = deepcopy(cached_context)
                state["cache_hits"] = int(state.get("cache_hits", 0)) + 1
            else:
                state["api_calls"] = int(state.get("api_calls", 0)) + 1
                called_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                snapshot = {**fetch_context(planner, context_points), "called_at": called_at,
                            "measured_at_epoch": time.time(), "evidence_version": EVIDENCE_VERSION}
                cache[context_key] = deepcopy(snapshot)
            check.update({"status": "measured", "measurement": snapshot,
                          "adjacent_duration_s": legs[index - 1]["duration_s"] + legs[index]["duration_s"],
                          "adjacent_distance_m": legs[index - 1]["distance_m"] + legs[index]["distance_m"]})
        except Exception as exc:
            check["error_type"] = type(exc).__name__
    status = "unavailable" if not complete else "needs_review" if issues else "verified"
    geometry: list[list[float]] = []
    for leg in legs:
        for pair in leg["geometry"]:
            if not geometry or geometry[-1] != pair:
                geometry.append(pair)
    evidence = {
        "evidence_version": EVIDENCE_VERSION, "routing_version": AMAP_DRIVING_VERSION,
        "provider": "amap", "source": "amap_adjacent_legs", "status": status,
        "complete": complete, "legs": legs, "issues": issues,
        "context_checks": context_checks,
        "point_count": len(points), "segment_count": len(legs),
        "called_at": max((leg["called_at"] for leg in legs), default=None),
        "measurement_started_at": min((leg["called_at"] for leg in legs), default=None),
        "duration_s": sum(leg["duration_s"] for leg in legs) if complete else None,
        "distance_m": sum(leg["distance_m"] for leg in legs) if complete else None,
        "geometry": geometry, "geometry_segments": [leg["geometry"] for leg in legs],
        "leg_durations_s": [leg["duration_s"] for leg in legs],
        "leg_distances_m": [leg["distance_m"] for leg in legs],
    }
    state["last_route_evidence"] = evidence
    return evidence
