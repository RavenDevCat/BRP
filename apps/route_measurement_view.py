"""Read-only adapters for saved measurements in legacy map exports."""
from copy import deepcopy
import math
from typing import Any


def _finite_metric(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def route_display_metrics(route: dict[str, Any]) -> dict[str, Any]:
    """Select presentation values without changing the saved solver references."""
    evidence = dict(route.get("route_evidence") or {})
    gate = dict(route.get("final_route_traffic_gate") or route.get("am_arrival_gate") or {})
    unavailable = {"duration_s": None, "distance_m": None, "source": "unavailable"}
    if evidence:
        if evidence.get("status") != "verified" or evidence.get("complete") is not True or evidence.get("issues"):
            return unavailable
        drive = _finite_metric(evidence.get("duration_s"))
        distance = _finite_metric(evidence.get("distance_m"))
        if drive is None or distance is None:
            return unavailable
        dwell = _finite_metric(route.get("stop_service_time_s"))
        total = drive + dwell if dwell is not None else None
        for key, value in (("verified_drive_duration_s", drive), ("verified_distance_m", distance), ("verified_total_duration_s", total)):
            saved = _finite_metric(gate.get(key))
            if value is not None and saved is not None and not math.isclose(value, saved, abs_tol=.01, rel_tol=0):
                return unavailable
        return {"duration_s": total,
                "distance_m": distance, "source": "measurement"}
    if gate.get("status") == "unavailable" or route.get("evidence_status") in {"needs_review", "unavailable"}:
        return unavailable
    if gate.get("status") in {"passed", "failed"}:
        return {"duration_s": _finite_metric(gate.get("verified_total_duration_s")),
                "distance_m": _finite_metric(gate.get("verified_distance_m")), "source": "historical_measurement"}
    duration = _finite_metric(route.get("time_s"))
    distance = _finite_metric(route.get("distance_m"))
    return {"duration_s": duration if duration is not None else _finite_metric(route.get("duration_s")),
            "distance_m": distance if distance is not None else _finite_metric(route.get("total_distance_m")),
            "source": "planning_reference"}


def scenario_display_summary(routes: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [route_display_metrics(route) for route in routes]
    def average(key):
        values = [item[key] for item in metrics]
        return sum(values) / len(values) if values and None not in values else None
    return {"avg_route_duration_s": average("duration_s"), "avg_route_distance_m": average("distance_m"),
            "measurement_unavailable_route_count": sum(item["duration_s"] is None or item["distance_m"] is None for item in metrics)}


def measured_route_views(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    views = deepcopy(routes)
    for route in views:
        metrics = route_display_metrics(route)
        if metrics["source"] != "planning_reference" or metrics["duration_s"] is None or metrics["distance_m"] is None:
            for key in ("limit_stop_node", "limit_stop_order", "limit_stop_elapsed_s"):
                route.pop(key, None)
        evidence = dict(route.get("route_evidence") or {})
        if not evidence:
            continue
        route["leg_details"] = [
            {**leg, "geometry": [list(reversed(pair)) for pair in leg.get("geometry") or []]}
            for leg in evidence.get("legs") or []
        ]
        if metrics["duration_s"] is not None:
            route["time_s"] = metrics["duration_s"]
        if metrics["distance_m"] is not None:
            route["distance_m"] = metrics["distance_m"]
    return views


def evidence_notice(evidence: dict[str, Any]) -> str:
    if evidence.get("status") != "verified" or evidence.get("complete") is not True or evidence.get("issues"):
        codes = "; ".join(str(item.get("code") or "unknown") for item in evidence.get("issues") or [])
        return "Route measurement needs review; time-window compliance is not verified." + (" " + codes if codes else "")
    warnings = evidence.get("warnings") or []
    if not warnings:
        return ""
    labels = {"provider_distance_disagreement": "AMap distance differs from the OSRM reference",
              "large_direct_detour_needs_review": "driving distance is unusually long relative to straight-line distance"}
    details = "; ".join("Segment " + str(item["leg_index"] + 1) + ": " + labels.get(item["code"], item["code"])
                        for item in warnings if isinstance(item.get("leg_index"), int))
    return "Suspected detour; provider measurements remain in use. " + details


def measurement_note(route: dict[str, Any]) -> str:
    evidence = dict(route.get("route_evidence") or {})
    gate = dict(route.get("final_route_traffic_gate") or route.get("am_arrival_gate") or {})
    metrics = route_display_metrics(route)
    if metrics["source"] == "unavailable" or metrics["duration_s"] is None or metrics["distance_m"] is None:
        codes = "; ".join(str(item.get("code") or "unknown") for item in evidence.get("issues") or [])
        return "Route measurement needs review; time-window compliance is not verified." + (" " + codes if codes else "")
    if gate.get("status") == "failed":
        note = "Measured route exceeds the time window."
    elif metrics["source"] == "measurement":
        note = ("Measured at: " + str(evidence["called_at"]) if evidence.get("called_at")
                else "Measured route: saved road evidence.")
    else:
        note = ""
    if note:
        notice = evidence_notice(evidence) if evidence else ""
        if notice:
            note += " " + notice
        return note
    if metrics["source"] == "historical_measurement":
        return "Historical measurement: map and timing were not saved as one measurement."
    return "Planning reference: estimated route, not a verified measurement."
