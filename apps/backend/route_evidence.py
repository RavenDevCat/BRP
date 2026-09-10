"""Job-scoped AMap measurements shared by planning and report consumers."""
from __future__ import annotations

from copy import deepcopy
from bisect import bisect_right
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


EVIDENCE_VERSION = "amap-route-evidence-v7"
CACHE_MAX_AGE_SECONDS = 600
CONTEXT_GEOMETRY_TOLERANCE_M = 1.0
DISTANCE_WARNING_CODES = frozenset({
    "provider_distance_disagreement", "large_direct_detour_needs_review",
})


def _classify_findings(evidence: dict[str, Any]) -> dict[str, Any]:
    """Distance heuristics flag investigation, not measurement integrity failures."""
    findings = evidence["issues"]
    evidence["warnings"] = [item for item in findings if item["code"] in DISTANCE_WARNING_CODES]
    evidence["issues"] = [item for item in findings if item["code"] not in DISTANCE_WARNING_CODES]
    for leg in evidence["legs"]:
        codes = leg.get("issues") or []
        leg["warnings"] = [code for code in codes if code in DISTANCE_WARNING_CODES]
        leg["issues"] = [code for code in codes if code not in DISTANCE_WARNING_CODES]
    evidence["status"] = ("unavailable" if not evidence["complete"] else
                          "needs_review" if evidence["issues"] else "verified")
    evidence["geometry_diagnostics"] = [
        {**deepcopy(item), "leg_index": leg["leg_index"]}
        for leg in evidence["legs"] for item in leg.get("geometry_diagnostics") or []
    ]
    evidence["geometry_status"] = "discontinuous" if evidence["geometry_diagnostics"] else "complete"
    return evidence


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


def _native_step_geometry(steps: list[dict[str, Any]], start: int = 0) -> dict[str, Any]:
    """Keep provider step gaps separate from metrics and never bridge them on maps."""
    geometry: list[list[float]] = []
    segments: list[list[list[float]]] = []
    diagnostics: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start):
        trace = _geometry({"steps": [step]})
        # Missing traces cannot establish the approach or waypoint correspondence.
        if not trace or len(trace) == 1 and float(step.get("step_distance", 0)) > 1:
            raise ValueError("Incomplete native step geometry")
        gap = distance_m(tuple(reversed(geometry[-1])), tuple(reversed(trace[0]))) if geometry else 0
        if not segments or gap > 50:
            segments.append([])
        if gap > 50:
            diagnostics.append({"code": "native_step_geometry_gap", "step_index": index,
                                "gap_m": gap, "from": geometry[-1], "to": trace[0]})
        for pair in trace:
            if not geometry or geometry[-1] != pair:
                geometry.append(pair)
            if not segments[-1] or segments[-1][-1] != pair:
                segments[-1].append(pair)
    if len(geometry) == 1:
        geometry.append(list(geometry[0]))
    return {"geometry": geometry,
            "geometry_segments": [segment for segment in segments if len(segment) >= 2],
            "geometry_diagnostics": diagnostics}


def _native_waypoint_legs(path: dict[str, Any], points: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Split only at provider navigation boundaries, never by distance ratios."""
    steps = path.get("steps") or []
    arrival = "\u5230\u8fbe\u9014\u7ecf\u5730"
    destination = "\u5230\u8fbe\u76ee\u7684\u5730"
    actions = [dict(step.get("navi") or {}).get("assistant_action") for step in steps]
    boundaries = [i for i, action in enumerate(actions) if action == arrival]
    if (len(points) < 2 or not steps or len(boundaries) != len(points) - 2
            or boundaries and boundaries[-1] == len(steps) - 1
            or [i for i, action in enumerate(actions) if action == destination] != [len(steps) - 1]):
        raise ValueError("Incomplete native waypoint boundaries")
    legs: list[dict[str, Any]] = []
    start = 0
    for index, end in enumerate([*boundaries, len(steps) - 1]):
        segment = steps[start:end + 1]
        durations = [float(step["cost"]["duration"]) for step in segment]
        distances = [float(step["step_distance"]) for step in segment]
        if not all(math.isfinite(value) and value >= 0 for value in durations + distances):
            raise ValueError("Invalid native step metrics")
        drawing = _native_step_geometry(segment, start)
        if sum(distances) <= 0 or sum(durations) <= 0:
            raise ValueError("Ambiguous zero-length native waypoint leg")
        legs.append({"duration_s": sum(durations), "distance_m": sum(distances),
                     **drawing, "native_step_start": start, "native_step_end": end,
                     "boundary_action": actions[end], "native_steps": deepcopy(segment),
                     "origin": list(points[index]), "destination": list(points[index + 1])})
        start = end + 1
    totals = amap_driving_path_stats(path)
    if any(not math.isfinite(totals[key]) or abs(sum(leg[key] for leg in legs) - totals[key]) > 1
           for key in ("duration_s", "distance_m")):
        raise ValueError("Native step totals disagree with route totals")
    return legs


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
    result = {**stats, "geometry": geometry, "request": params,
              "roads": [str(step.get("road_name") or step.get("road") or "")
                        for step in path.get("steps") or []]}
    if len(points) == 2:
        result.update(_native_step_geometry(path.get("steps") or []))
    if len(points) > 2:
        try:
            result["waypoint_legs"] = _native_waypoint_legs(path, points)
            result["segmentation"] = "native_navigation_boundaries"
        except (ValueError, KeyError, TypeError, IndexError, OverflowError):
            result["segmentation"] = "native_boundaries_unavailable"
    return result


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


def _geometry_profile(raw: Any) -> tuple[list[list[float]], list[float]]:
    if not isinstance(raw, list) or not 2 <= len(raw) <= 10000:
        raise ValueError("Invalid continuous-route geometry")
    points: list[list[float]] = []
    cumulative: list[float] = []
    for pair in raw:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(math.isfinite(float(value)) for value in pair)
                or abs(float(pair[0])) > 180 or abs(float(pair[1])) > 90):
            raise ValueError("Invalid continuous-route coordinate")
        point = list(map(float, pair))
        if not points or point != points[-1]:
            cumulative.append(cumulative[-1] + distance_m(tuple(reversed(points[-1])), tuple(reversed(point))) if points else 0.0)
            points.append(point)
    if len(points) < 2 or cumulative[-1] <= 0:
        raise ValueError("Empty continuous-route geometry")
    return points, cumulative


def _geometry_position(points: list[list[float]], cumulative: list[float], fraction: float) -> list[float]:
    along = cumulative[-1] * fraction
    index = min(len(points) - 2, max(0, bisect_right(cumulative, along) - 1))
    part = (along - cumulative[index]) / (cumulative[index + 1] - cumulative[index])
    return [a + (b - a) * part for a, b in zip(points[index], points[index + 1])]


def _continuous_turn_resolution(previous: dict[str, Any], following: dict[str, Any],
                                context: dict[str, Any]) -> dict[str, Any]:
    """Certify only the same directed road trace, never the shorter alternative."""
    result: dict[str, Any] = {"status": "unresolved", "policy": "same-directed-trace-v1"}
    try:
        if distance_m(tuple(reversed(previous["geometry"][-1])), tuple(reversed(following["geometry"][0]))) > CONTEXT_GEOMETRY_TOLERANCE_M:
            return {**result, "reason": "adjacent_road_gap"}
        before, a = _geometry_profile(previous["geometry"] + following["geometry"])
        continuous, b = _geometry_profile(context.get("geometry"))
        lengths = [float(previous["distance_m"]) + float(following["distance_m"]), float(context["distance_m"])]
        durations = [float(previous["duration_s"]) + float(following["duration_s"]), float(context["duration_s"])]
        if not all(math.isfinite(value) and value > 0 for value in lengths + durations):
            return {**result, "reason": "incomplete_context_metrics"}
        result.update(distance_delta_m=lengths[1] - lengths[0], duration_delta_s=durations[1] - durations[0])
        if abs(lengths[1] - lengths[0]) > 5 or abs(a[-1] - b[-1]) > 5:
            return {**result, "reason": "continuous_distance_differs"}
        if abs(durations[1] - durations[0]) > max(30.0, min(durations) * 0.1):
            return {**result, "reason": "continuous_duration_differs"}
        # Compare in travel order at every corner and every five metres. Unlike
        # an unordered corridor test, this cannot equate a loop with a shortcut.
        steps = max(1, math.ceil(max(a[-1], b[-1]) / 5))
        if steps > 10000:
            return {**result, "reason": "geometry_comparison_limit"}
        fractions = {i / steps for i in range(steps + 1)} | {value / a[-1] for value in a} | {value / b[-1] for value in b}
        maximum = max(distance_m(tuple(reversed(_geometry_position(before, a, fraction))),
                                 tuple(reversed(_geometry_position(continuous, b, fraction)))) for fraction in fractions)
        result["maximum_separation_m"] = maximum
        if maximum > CONTEXT_GEOMETRY_TOLERANCE_M:
            return {**result, "reason": "continuous_geometry_differs"}
        return {**result, "status": "confirmed", "reason": "same_directed_route"}
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        return {**result, "reason": "invalid_context_evidence"}


def _recover_continuous_legs(planner: Any, points: list[tuple[float, float]], cache: dict[str, Any],
                             state: dict[str, Any], legs: list[dict[str, Any]],
                             fetch_context: Callable) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    recovery: dict[str, Any] = {"status": "unavailable", "policy": "native-waypoint-boundaries-v1"}
    if not 3 <= len(points) <= 18:
        return [], [], {**recovery, "reason": "waypoint_limit"}
    key = "context|" + _key(points)
    cached = cache.get(key) or {}
    try:
        if (cached.get("evidence_version") == EVIDENCE_VERSION
                and 0 <= time.time() - float(cached.get("measured_at_epoch", 0)) <= CACHE_MAX_AGE_SECONDS):
            snapshot = deepcopy(cached)
            state["cache_hits"] = int(state.get("cache_hits", 0)) + 1
        else:
            if int(state.get("api_calls", 0)) >= int(state.get("api_call_limit", 0)):
                return [], [], {**recovery, "reason": "provider_call_budget_exhausted"}
            state["api_calls"] = int(state.get("api_calls", 0)) + 1
            snapshot = {**fetch_context(planner, points), "called_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "measured_at_epoch": time.time(), "evidence_version": EVIDENCE_VERSION}
            cache[key] = deepcopy(snapshot)
        recovery["measurement"] = snapshot
        native = snapshot.get("waypoint_legs") or []
        if snapshot.get("segmentation") != "native_navigation_boundaries" or len(native) != len(points) - 1:
            return [], [], {**recovery, "reason": "native_boundaries_unavailable"}
        corrected: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        for index, (measured, original) in enumerate(zip(native, legs)):
            if measured["origin"] != list(points[index]) or measured["destination"] != list(points[index + 1]):
                return [], [], {**recovery, "reason": "waypoint_order_mismatch"}
            leg = {**deepcopy(measured), "leg_index": index, "provider": "amap", "coordinate_system": "WGS84",
                   "request_coordinate_system": "GCJ02", "source": "amap_continuous_waypoint_legs",
                   "evidence_version": EVIDENCE_VERSION, "called_at": snapshot["called_at"],
                   "measured_at_epoch": snapshot["measured_at_epoch"], "request": snapshot.get("request"),
                   "origin_wgs84": original["origin_wgs84"], "destination_wgs84": original["destination_wgs84"],
                   "straight_distance_m": original["straight_distance_m"],
                   "osrm_reference_distance_m": original.get("osrm_reference_distance_m"),
                   "osrm_reference_duration_s": original.get("osrm_reference_duration_s")}
            leg["issues"] = _leg_issues(leg, leg["osrm_reference_distance_m"])
            issues.extend({"leg_index": index, "code": code} for code in leg["issues"])
            if corrected:
                gap = distance_m(tuple(reversed(corrected[-1]["geometry"][-1])), tuple(reversed(leg["geometry"][0])))
                if gap > 50:
                    leg.setdefault("geometry_diagnostics", []).append({
                        "code": "native_waypoint_geometry_gap", "gap_m": gap,
                        "from": corrected[-1]["geometry"][-1], "to": leg["geometry"][0]})
            corrected.append(leg)
        return corrected, issues, {**recovery, "status": "applied", "reason": "provider_continuous_approach"}
    except Exception as exc:
        return [], [], {**recovery, "error_type": type(exc).__name__}


def _measure_adjacent_route(
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
                    if not set(attempts[-1]["issues"]) - DISTANCE_WARNING_CODES or attempt == 1:
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
    observations: list[dict[str, Any]] = []
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
            resolution = _continuous_turn_resolution(legs[index - 1], legs[index], snapshot)
            check["resolution"] = resolution
            if resolution["status"] == "confirmed":
                resolved = [issue for issue in issues if issue["leg_index"] == index and issue["code"] == "stop_turnaround_needs_review"]
                observations.extend({**issue, "resolution": "continuous_route_confirmed", "context_stop_index": index} for issue in resolved)
                issues = [issue for issue in issues if issue not in resolved]
        except Exception as exc:
            check["error_type"] = type(exc).__name__
    recovery: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    source = "amap_adjacent_legs"
    if complete and any(issue["code"] in {"stop_road_continuity_needs_review", "stop_turnaround_needs_review"} for issue in issues):
        corrected, corrected_issues, recovery = _recover_continuous_legs(planner, points, cache, state, legs, fetch_context)
        if corrected:
            comparison = {"legs": deepcopy(legs), "issues": deepcopy(issues),
                          "duration_s": sum(leg["duration_s"] for leg in legs),
                          "distance_m": sum(leg["distance_m"] for leg in legs)}
            legs, issues = corrected, corrected_issues
            source = "amap_continuous_waypoint_legs"
    status = "unavailable" if not complete else "needs_review" if issues else "verified"
    geometry: list[list[float]] = []
    for leg in legs:
        for pair in leg["geometry"]:
            if not geometry or geometry[-1] != pair:
                geometry.append(pair)
    evidence = {
        "evidence_version": EVIDENCE_VERSION, "routing_version": AMAP_DRIVING_VERSION,
        "provider": "amap", "source": source, "status": status,
        "complete": complete, "legs": legs, "issues": issues,
        "context_checks": context_checks, "observations": observations,
        "point_count": len(points), "segment_count": len(legs),
        "called_at": max((leg["called_at"] for leg in legs), default=None),
        "measurement_started_at": min((leg["called_at"] for leg in legs), default=None),
        "duration_s": sum(leg["duration_s"] for leg in legs) if complete else None,
        "distance_m": sum(leg["distance_m"] for leg in legs) if complete else None,
        "geometry": geometry, "geometry_segments": [segment for leg in legs
            for segment in leg.get("geometry_segments", [leg["geometry"]])],
        "leg_durations_s": [leg["duration_s"] for leg in legs],
        "leg_distances_m": [leg["distance_m"] for leg in legs],
    }
    if recovery is not None:
        evidence["continuous_recovery"] = recovery
    if comparison is not None:
        evidence["adjacent_comparison"] = comparison
    _classify_findings(evidence)
    state["last_route_evidence"] = evidence
    return evidence


def measure_amap_route(
    planner: Any, points: list[tuple[float, float]], cache: dict[str, Any], state: dict[str, Any], *,
    fetch_leg: Callable[[Any, list[tuple[float, float]]], dict[str, Any]] = fetch_amap_leg,
    fetch_context: Callable[[Any, list[tuple[float, float]]], dict[str, Any]] = fetch_amap_itinerary,
) -> dict[str, Any]:
    """Prefer one continuous itinerary; independent edges are diagnostic fallback."""
    if len(points) <= 2:
        return _measure_adjacent_route(planner, points, cache, state, fetch_leg=fetch_leg, fetch_context=fetch_context)
    if any(len(p) != 2 or not all(math.isfinite(x) for x in p)
           or abs(p[0]) > 90 or abs(p[1]) > 180 for p in points):
        raise ValueError("Route contains unresolved coordinates")
    references = list(state.get("expected_leg_distances_m") or [])
    reference_times = list(state.get("expected_leg_durations_s") or [])
    bases = [{"origin_wgs84": list(gcj02_to_wgs84(*a)), "destination_wgs84": list(gcj02_to_wgs84(*b)),
              "straight_distance_m": distance_m(a, b),
              "osrm_reference_distance_m": references[i] if i < len(references) else None,
              "osrm_reference_duration_s": reference_times[i] if i < len(reference_times) else None}
             for i, (a, b) in enumerate(zip(points, points[1:]))]
    legs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(points) - 1:
        end = min(len(points), start + 18)
        chunk, chunk_issues, record = _recover_continuous_legs(
            planner, points[start:end], cache, state, bases[start:end - 1], fetch_context)
        record.update(point_start=start, point_end=end - 1)
        chunks.append(record)
        if not chunk:
            break
        skip = 0
        if start:
            # Re-request two complete incoming legs so a chunk cannot reset the
            # approach at its join. An unproven join remains diagnostic only.
            overlap = {"geometry": chunk[0]["geometry"] + chunk[1]["geometry"],
                       "duration_s": chunk[0]["duration_s"] + chunk[1]["duration_s"],
                       "distance_m": chunk[0]["distance_m"] + chunk[1]["distance_m"]}
            resolution = _continuous_turn_resolution(legs[-2], legs[-1], overlap)
            record["overlap_resolution"] = resolution
            if resolution["status"] != "confirmed":
                record.update(status="unavailable", reason="continuous_chunk_join_unverified")
                break
            skip = 2
        for leg in chunk[skip:]:
            leg["leg_index"] += start
            legs.append(leg)
        issues.extend({**item, "leg_index": item["leg_index"] + start}
                      for item in chunk_issues if item["leg_index"] >= skip)
        if end == len(points):
            geometry: list[list[float]] = []
            for leg in legs:
                for pair in leg["geometry"]:
                    if not geometry or pair != geometry[-1]:
                        geometry.append(pair)
            result = {"evidence_version": EVIDENCE_VERSION, "routing_version": AMAP_DRIVING_VERSION,
                      "provider": "amap", "source": "amap_continuous_waypoint_legs",
                      "status": "needs_review" if issues else "verified", "complete": True,
                      "legs": legs, "issues": issues, "context_checks": [], "observations": [],
                      "continuous_measurement": {"status": "complete", "chunks": chunks},
                      "point_count": len(points), "segment_count": len(legs),
                      "called_at": max(leg["called_at"] for leg in legs),
                      "measurement_started_at": min(leg["called_at"] for leg in legs),
                      "duration_s": sum(leg["duration_s"] for leg in legs),
                      "distance_m": sum(leg["distance_m"] for leg in legs), "geometry": geometry,
                      "geometry_segments": [segment for leg in legs
                          for segment in leg.get("geometry_segments", [leg["geometry"]])],
                      "leg_durations_s": [leg["duration_s"] for leg in legs],
                      "leg_distances_m": [leg["distance_m"] for leg in legs]}
            _classify_findings(result)
            state["last_route_evidence"] = result
            return result
        start = end - 3
    result = _measure_adjacent_route(planner, points, cache, state, fetch_leg=fetch_leg, fetch_context=fetch_context)
    result["continuous_measurement"] = {"status": "unavailable", "chunks": chunks}
    result["issues"].append({"leg_index": 0, "code": "continuous_itinerary_unverified"})
    if result["status"] == "verified":
        result["status"] = "needs_review"
    return result
