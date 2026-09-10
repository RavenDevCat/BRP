"""Read-only presentation of unverified provider measurements, never solver input."""
from copy import deepcopy
import math


REVIEW_CODES = {"provider_distance_disagreement", "large_direct_detour_needs_review"}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def review_measurement(route):
    evidence = route.get("route_evidence") or {}
    codes = {item.get("code") for item in evidence.get("issues") or []}
    durations = evidence.get("leg_durations_s") or []
    distances = evidence.get("leg_distances_m") or []
    # Only complete, provider-native captures with diagnostic distance warnings
    # may expose reference values. Integrity/continuity failures stay unavailable.
    valid = (evidence.get("complete") is True
             and evidence.get("provider") == "amap"
             and evidence.get("source") == "amap_continuous_waypoint_legs"
             and bool(codes) and codes <= REVIEW_CODES
             and len(durations) > 0 and len(durations) == len(distances)
             and evidence.get("point_count") == len(durations) + 1
             and all(_positive(v) for v in durations + distances)
             and _positive(evidence.get("duration_s")) and _positive(evidence.get("distance_m"))
             and abs(sum(durations) - evidence["duration_s"]) <= 1
             and abs(sum(distances) - evidence["distance_m"]) <= 1)
    return evidence if valid else None


def route_coverage(result):
    routes = result.get("routes") or []
    expected = (result.get("summary") or {}).get("route_count")
    total = max(len(routes), expected if isinstance(expected, int) and expected >= 0 else 0)
    verified = sum(r.get("status") == "resolved" for r in routes)
    review = sum(r.get("status") != "resolved" and review_measurement(r) is not None for r in routes)
    failed = total - verified - review
    return {"route_measurement_verified_count": verified,
            "route_measurement_review_count": review,
            "route_measurement_failed_count": failed,
            "route_measurement_total_count": total}


def present_direct_school_result(raw):
    result = deepcopy(raw or {})
    if not result:
        return result
    coverage = route_coverage(result)
    result.setdefault("summary", {}).update(coverage)
    warnings = []
    snapshots = [("Direct", ", ".join(stop.get("route_ids") or []), stop.get("address"), stop.get("route_evidence"))
                 for stop in result.get("stops") or []]
    snapshots.extend(("Current route", route.get("route_id"), "", route.get("route_evidence"))
                     for route in result.get("routes") or [])
    for route in result.get("route_window_analysis") or []:
        snapshots.extend((stage, route.get("route_id"), "", route.get(key)) for stage, key in (
            ("After first removal", "post_primary_evidence"), ("Final", "final_evidence")))
    for stage, route_id, address, snapshot in snapshots:
        snapshot = snapshot or {}
        if snapshot.get("status") == "verified" and snapshot.get("complete") is True and not snapshot.get("issues"):
            warnings.extend({**deepcopy(item), "stage": stage, "route_id": route_id, "address": address}
                            for item in snapshot.get("warnings") or [])
    result["measurement_warnings"] = warnings
    result["summary"]["measurement_warning_count"] = len(warnings)
    if result.get("status") in {"complete", "partial"} and (
            coverage["route_measurement_review_count"] or coverage["route_measurement_failed_count"]):
        result["status"] = "partial"
    contexts = {}
    for stop in result.get("stops") or []:
        stop["measurement_warnings"] = deepcopy((stop.get("route_evidence") or {}).get("warnings") or [])
        for context in stop.get("route_contexts") or []:
            contexts.setdefault(str(context.get("route_id")), []).append(context)
    dwell = (result.get("parameters") or {}).get("stop_service_minutes")
    direction = result.get("service_direction")
    for route in result.get("routes") or []:
        if route.get("status") == "resolved":
            route["measurement_status"] = "verified"
            for context in contexts.get(str(route.get("route_id")), []):
                context["measurement_status"] = "verified"
                context["measurement_warnings"] = deepcopy((route.get("route_evidence") or {}).get("warnings") or [])
            continue
        evidence = review_measurement(route)
        state = "needs_review" if evidence else "unavailable"
        route["measurement_status"] = state
        route["review_issues"] = deepcopy((route.get("route_evidence") or {}).get("issues") or [])
        members = contexts.get(str(route.get("route_id")), [])
        for context in members:
            context["measurement_status"] = state
            context["measurement_error"] = route.get("error") or "Route measurement unavailable"
            context["review_codes"] = sorted({str(i.get("code")) for i in route["review_issues"]})
        if not evidence:
            continue
        route["status"] = "needs_review"
        route["provisional_provider_duration_min"] = round(evidence["duration_s"] / 60, 2)
        route["provisional_distance_km"] = round(evidence["distance_m"] / 1000, 3)
        route["provider_called_at"] = evidence.get("called_at")
        durations = evidence["leg_durations_s"]
        sequences = [c.get("stop_sequence") for c in members]
        if (len(members) != len(durations) or any(not isinstance(s, int) or isinstance(s, bool) or s < 0 for s in sequences)
                or len(set(sequences)) != len(sequences)
                or not isinstance(dwell, (int, float)) or not math.isfinite(dwell) or dwell < 0
                or direction not in {"To School", "From School"}):
            continue
        route["provisional_total_duration_min"] = round(evidence["duration_s"] / 60 + len(members) * dwell, 2)
        for index, context in enumerate(sorted(members, key=lambda c: c["stop_sequence"])):
            legs = durations[index:] if direction == "To School" else durations[:index + 1]
            count = len(members) - index if direction == "To School" else index + 1
            context["provisional_current_ride_min"] = round(sum(legs) / 60 + count * dwell, 2)
            context["measurement_called_at"] = evidence.get("called_at")
    return result
