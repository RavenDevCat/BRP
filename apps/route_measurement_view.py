"""Read-only adapters for saved measurements in legacy map exports."""
from copy import deepcopy
from typing import Any


def measured_route_views(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    views = deepcopy(routes)
    for route in views:
        evidence = dict(route.get("route_evidence") or {})
        if not evidence:
            continue
        for key in ("limit_stop_node", "limit_stop_order", "limit_stop_elapsed_s"):
            route.pop(key, None)
        route["leg_details"] = [
            {**leg, "geometry": [list(reversed(pair)) for pair in leg.get("geometry") or []]}
            for leg in evidence.get("legs") or []
        ]
        if evidence.get("complete"):
            route["time_s"] = float(evidence["duration_s"]) + float(route.get("stop_service_time_s") or 0)
            route["distance_m"] = float(evidence["distance_m"])
    return views


def measurement_note(route: dict[str, Any]) -> str:
    evidence = dict(route.get("route_evidence") or {})
    gate = dict(route.get("final_route_traffic_gate") or {})
    if evidence.get("status") in {"needs_review", "unavailable"} or gate.get("status") == "unavailable":
        return "Route measurement needs review; time-window compliance is not verified."
    if gate.get("status") == "failed":
        return "Measured route exceeds the time window."
    if evidence.get("called_at"):
        return "Measured at: " + str(evidence["called_at"])
    return "Historical or estimated route: map and timing were not saved as one measurement."
