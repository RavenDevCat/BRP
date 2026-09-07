"""Bounded, append-only remeasurement contracts; never rerun the route solver."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Callable

try:
    from . import direct_school_analysis as analysis
    from .route_evidence import EVIDENCE_VERSION
except ImportError:
    import direct_school_analysis as analysis
    from route_evidence import EVIDENCE_VERSION


REVIEW_VERSION = "route-measurement-review-v1"
MAX_REVIEW_ROUTES = 20
MAX_REVIEW_CALLS = 500


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def route_risk_reasons(route: dict[str, Any]) -> list[str]:
    evidence = dict(route.get("route_evidence") or {})
    if not evidence:
        return ["missing_unified_measurement"]
    reasons = []
    if evidence.get("evidence_version") != EVIDENCE_VERSION:
        reasons.append("outdated_measurement_contract")
    if evidence.get("status") != "verified" or not evidence.get("complete"):
        reasons.append("unresolved_measurement_quality")
    if evidence.get("issues"):
        reasons.append("saved_quality_flags")
    return reasons


def _scope(key: str, route: dict[str, Any], points: list[dict[str, Any]],
           direction: str, dwell_s: Any, window_s: Any, before: dict[str, Any],
           references: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    issues = []
    if not str(route.get("route_id") or "").strip():
        issues.append("source_route_identity_missing")
    if len(points) < 2 or any(not point for point in points):
        issues.append("source_stop_coordinates_missing")
    return {
        "route_key": key, "route_id": str(route.get("route_id") or ""),
        "vehicle_id": route.get("vehicle_id"), "service_direction": direction,
        "points": deepcopy(points), "stop_service_time_s": _number(dwell_s),
        "window_limit_s": _number(window_s), "before": deepcopy(before),
        "before_evidence": deepcopy(route.get("route_evidence")),
        "reference_legs": deepcopy(references or []),
        "risk_reasons": route_risk_reasons(route), "input_issues": issues,
    }


def source_route_scopes(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Read saved inputs only. No geocoding, routing, mutation or implicit stop replacement."""
    result = dict(record.get("result") or {})
    kind = str(dict(record.get("metadata") or {}).get("job_kind") or "route_audit")
    scopes = []
    if kind == "direct_school_analysis":
        if result.get("provider") != "amap":
            raise ValueError("Historical road remeasurement currently requires a China AMap source.")
        prepared = dict(record.get("prepared_payload") or {})
        current = dict(prepared.get("current_plan") or {})
        lookup = analysis._point_lookup(list(prepared.get("input_records") or []),
                                        list(prepared.get("original_points") or []))
        parameters = dict(result.get("parameters") or {})
        dwell = _number(parameters.get("stop_service_minutes"))
        window = _number(dict(result.get("operational_conclusion") or {}).get("route_window_min"))
        for route in result.get("routes") or []:
            route_id = str(route.get("route_id") or "")
            ordered = sorted((stop for stop in current.get("stops") or []
                              if str(stop.get("route_id") or "") == route_id),
                             key=lambda stop: int(stop.get("stop_sequence", 0)))
            points = []
            for stop in ordered:
                point = analysis._lookup_point(stop, lookup)
                points.append({**point, "passenger_count": stop.get("passenger_count"),
                               "is_depot": bool(stop.get("is_depot"))} if point else {})
            count = sum(not stop.get("is_depot") for stop in ordered)
            before = {"drive_duration_s": _minutes(route.get("provider_duration_min")),
                      "total_duration_s": _minutes(route.get("total_duration_min")),
                      "distance_m": _kilometres(route.get("provider_distance_km")),
                      "captured_at": route.get("provider_called_at")}
            scopes.append(_scope(f"current_plan:{route_id}", route, points,
                                  str(result.get("service_direction") or ""),
                                  dwell * count * 60 if dwell is not None else None,
                                  window * 60 if window is not None else None, before))
        return scopes
    if kind != "route_audit":
        raise ValueError("This saved-result type does not have a registered review adapter.")
    config = dict(record.get("config") or {})
    scenarios = dict(result.get("structured_results") or {})
    for scenario_key in ("current_plan", "time_constrained", "exception_preserving"):
        scenario = dict(scenarios.get(scenario_key) or {})
        pool = list(scenario.get("points") or [])
        policy = dict(dict(scenario.get("traffic_gate") or {}).get("traffic_policy") or {})
        for route in scenario.get("routes") or []:
            points = []
            for node in route.get("nodes") or []:
                if isinstance(node, int) and not isinstance(node, bool) and 0 <= node < len(pool):
                    points.append(pool[node])
                else:
                    points.append({})
            gate = dict(route.get("final_route_traffic_gate") or {})
            provider = str(gate.get("provider") or policy.get("provider") or "").lower()
            valid_points = [point for point in points if point]
            if provider != "amap" and not (valid_points and all(
                str(point.get("country") or "").lower() == "china" for point in valid_points
            )):
                continue
            before = {"drive_duration_s": _number(gate.get("verified_drive_duration_s")),
                      "total_duration_s": _number(gate.get("verified_total_duration_s")),
                      "distance_m": _number(gate.get("verified_distance_m")),
                      "captured_at": dict(route.get("route_evidence") or {}).get("called_at")}
            # OSRM planning values remain labelled references, not historical live measurements.
            before["planned_total_duration_s"] = _number(route.get("time_s"))
            before["planned_distance_m"] = _number(route.get("distance_m"))
            scopes.append(_scope(f"{scenario_key}:{route.get('route_id', '')}", route, points,
                                  str(config.get("service_direction") or ""),
                                  route.get("stop_service_time_s"), gate.get("target_duration_s"), before,
                                  list(route.get("leg_details") or [])))
    return scopes


def _minutes(value: Any) -> float | None:
    value = _number(value)
    return value * 60 if value is not None else None


def _kilometres(value: Any) -> float | None:
    value = _number(value)
    return value * 1000 if value is not None else None


def historical_risk_summary(record: dict[str, Any]) -> dict[str, Any]:
    unavailable = {"source_job_id": record.get("job_id"), "source_supported": False,
                   "status": "unavailable", "routes": []}
    if str(record.get("status") or "") not in {"succeeded", "failed"}:
        return {**unavailable, "reason": "source_not_finished"}
    if not record.get("result"):
        return {**unavailable, "reason": "no_saved_result"}
    try:
        scopes = source_route_scopes(record)
    except ValueError:
        return {**unavailable, "reason": "unsupported_source"}
    if not scopes:
        return {**unavailable, "reason": "no_saved_china_routes"}
    return {
        "source_job_id": record.get("job_id"), "source_supported": True, "status": "ready",
        "routes": [{key: deepcopy(scope[key]) for key in
                    ("route_key", "route_id", "vehicle_id", "risk_reasons", "input_issues")}
                   for scope in scopes if scope["risk_reasons"]],
        "scope": "saved_current_routes" if str(dict(record.get("metadata") or {}).get("job_kind")) == "direct_school_analysis"
                 else "saved_audit_scenarios",
    }


def build_review_request(record: dict[str, Any], route_keys: list[str], *,
                         requested_by: str, request_key: str,
                         provider_call_limit: int = 100) -> dict[str, Any]:
    if str(record.get("status") or "") not in {"succeeded", "failed"} or not record.get("result"):
        raise ValueError("A historical review requires a finished saved result.")
    if not record.get("job_id") or not requested_by.strip() or not 1 <= len(request_key) <= 80:
        raise ValueError("Source, requesting administrator and request key are required.")
    if not route_keys or len(set(route_keys)) != len(route_keys) or len(route_keys) > MAX_REVIEW_ROUTES:
        raise ValueError("Choose 1-20 distinct saved routes.")
    if isinstance(provider_call_limit, bool) or not isinstance(provider_call_limit, int) or not 1 <= provider_call_limit <= MAX_REVIEW_CALLS:
        raise ValueError("Provider call limit must be an integer from 1 to 500.")
    scopes = source_route_scopes(record)
    lookup = {scope["route_key"]: scope for scope in scopes}
    if len(lookup) != len(scopes):
        raise ValueError("Saved route identities are ambiguous; do not guess a matching route.")
    if any(key not in lookup or not lookup[key]["risk_reasons"] for key in route_keys):
        raise ValueError("The selection includes a missing route or a route outside the risk pool.")
    selected = [lookup[key] for key in sorted(route_keys)]
    return {
        "review_version": REVIEW_VERSION, "evidence_version": EVIDENCE_VERSION,
        "source_job_id": str(record["job_id"]),
        "owner_email": str(record.get("owner_email") or "").strip().lower(),
        "requested_by": requested_by.strip().lower(), "request_key": request_key,
        "source_finished_at": record.get("finished_at"),
        "source_result_digest": _digest(record["result"]),
        "input_digest": _digest(selected), "routes": selected,
        "provider_call_limit": provider_call_limit,
    }


def run_measurement_review(request: dict[str, Any], *,
                           provider_factory: Callable[..., Any] = analysis.FreshRouteProvider,
                           checkpoint: Callable[[dict[str, Any]], None] | None = None,
                           canceled: Callable[[], bool] | None = None,
                           resume_result: dict[str, Any] | None = None,
                           api_calls_used: int = 0,
                           reserve_calls: Callable[[int], bool] | None = None) -> dict[str, Any]:
    routes = deepcopy(request.get("routes") or [])
    limit = request.get("provider_call_limit")
    if (request.get("review_version") != REVIEW_VERSION
            or request.get("evidence_version") != EVIDENCE_VERSION
            or request.get("input_digest") != _digest(routes)
            or not 1 <= len(routes) <= MAX_REVIEW_ROUTES
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_REVIEW_CALLS):
        raise ValueError("The saved review request is invalid or no longer uses the current contract.")
    previous = deepcopy(dict(resume_result or {}).get("routes") or [])
    if previous and (
        resume_result.get("source_result_digest") != request["source_result_digest"]
        or resume_result.get("source_job_id") != request["source_job_id"]
        or len(previous) > len(routes)
        or any(old.get("route_key") != scope["route_key"] or old.get("points") != scope["points"]
               for old, scope in zip(previous, routes))
    ):
        raise ValueError("The saved checkpoint does not match the selected routes.")
    if isinstance(api_calls_used, bool) or not isinstance(api_calls_used, int) or not 0 <= api_calls_used <= limit:
        raise ValueError("Invalid persisted provider usage.")
    result = {"review_version": REVIEW_VERSION, "source_job_id": request.get("source_job_id"),
              "source_result_digest": request.get("source_result_digest"), "routes": previous,
              "status": "running", "provider_api_calls": api_calls_used,
              "comparison_basis": "fresh_traffic_not_controlled_before_after",
              "scope": "selected_routes_only", "student_classification_recomputed": False,
              "time_window_revalidated": False}
    provider = None
    for scope in routes[len(previous):]:
        if canceled and canceled():
            result["status"] = "canceled"
            break
        evidence = None
        error = None
        if provider is not None:
            provider.state.pop("last_route_evidence", None)
        try:
            if scope["input_issues"]:
                raise ValueError("Unresolved source route inputs")
            if provider is None:
                provider = provider_factory("amap", departure_time=None, api_call_limit=limit)
                provider.state = ReviewCallState({**provider.state, "api_calls": api_calls_used}, reserve_calls)
            provider.state.pop("last_route_evidence", None)
            evidence = provider.route(scope["points"], reference_legs=scope["reference_legs"])
        except ReviewClaimLost:
            raise
        except Exception as exc:
            evidence = deepcopy(provider.state.get("last_route_evidence")) if provider else None
            # Provider exceptions may contain credential-bearing URLs. Persist only the type.
            error = exc.__class__.__name__
        evidence = dict(evidence or {})
        certified = (not error and evidence.get("evidence_version") == EVIDENCE_VERSION
                     and evidence.get("status") == "verified" and evidence.get("complete") is True
                     and len(evidence.get("legs") or []) == len(scope["points"]) - 1
                     and _number(evidence.get("duration_s")) is not None
                     and _number(evidence.get("distance_m")) is not None)
        after = {"drive_duration_s": _number(evidence.get("duration_s")) if certified else None,
                 "distance_m": _number(evidence.get("distance_m")) if certified else None,
                 "captured_at": evidence.get("called_at"), "total_duration_s": None}
        dwell = scope["stop_service_time_s"]
        if after["drive_duration_s"] is not None and dwell is not None:
            after["total_duration_s"] = after["drive_duration_s"] + dwell
        changes = {}
        for key in ("drive_duration_s", "distance_m", "total_duration_s"):
            old, new = _number(scope["before"].get(key)), after[key]
            changes[key] = new - old if old is not None and new is not None else None
        result["routes"].append({
            "route_key": scope["route_key"], "route_id": scope["route_id"],
            "vehicle_id": scope["vehicle_id"], "service_direction": scope["service_direction"],
            "points": deepcopy(scope["points"]), "status": "verified" if certified else "needs_review",
            "before": deepcopy(scope["before"]), "after": after, "delta": changes,
            "risk_reasons": scope["risk_reasons"], "input_issues": scope["input_issues"],
            "route_evidence": evidence or None, "error_type": error,
        })
        result["provider_api_calls"] = int(provider.state.get("api_calls", 0)) if provider else api_calls_used
        if checkpoint:
            checkpoint(deepcopy(result))
    if result["status"] != "canceled":
        result["status"] = "complete" if all(route["status"] == "verified" for route in result["routes"]) else "partial"
    return result


class ReviewClaimLost(RuntimeError):
    pass


class ReviewCallState(dict):
    def __init__(self, initial: dict[str, Any], reserve: Callable[[int], bool] | None):
        super().__init__(initial)
        self.reserve = reserve

    def __setitem__(self, key, value):
        if key == "api_calls":
            amount = int(value) - int(self.get(key, 0))
            if amount < 0 or (amount > 0 and self.reserve and not self.reserve(amount)):
                raise ReviewClaimLost("Review stopped or provider budget unavailable.")
        super().__setitem__(key, value)


def execute_saved_review(store: Any, review_id: str, worker_token: str, *,
                         provider_factory: Callable[..., Any] = analysis.FreshRouteProvider,
                         preclaimed: bool = False) -> dict[str, Any] | None:
    """Worker entry point; the scheduler must acquire its shared concurrency slot first."""
    record = (store.claimed_route_measurement_review(review_id, worker_token) if preclaimed
              else store.claim_route_measurement_review(review_id, worker_token))
    if not record:
        return store.get_route_measurement_review(review_id)
    request = record["request"]

    def canceled() -> bool:
        current = store.get_route_measurement_review(review_id)
        return not current or current["status"] != "running"

    def checkpoint(result: dict[str, Any]) -> None:
        if not store.save_route_measurement_review(review_id, worker_token, result):
            raise ReviewClaimLost("Review was stopped before its checkpoint could be saved.")

    try:
        result = run_measurement_review(request, provider_factory=provider_factory,
                                        checkpoint=checkpoint, canceled=canceled,
                                        resume_result=record.get("result"), api_calls_used=int(record.get("api_calls") or 0),
                                        reserve_calls=lambda count: store.reserve_route_measurement_calls(review_id, worker_token, count))
        store.save_route_measurement_review(review_id, worker_token, result, terminal=True)
    except ReviewClaimLost:
        pass
    except Exception as exc:
        current = store.get_route_measurement_review(review_id) or {}
        result = dict(current.get("result") or {
            "review_version": request["review_version"], "source_job_id": request["source_job_id"],
            "source_result_digest": request["source_result_digest"], "routes": [],
        })
        result.update(status="failed", error_type=type(exc).__name__,
                      provider_api_calls=int(current.get("api_calls") or 0))
        store.save_route_measurement_review(review_id, worker_token, result, terminal=True)
    return store.get_route_measurement_review(review_id)
