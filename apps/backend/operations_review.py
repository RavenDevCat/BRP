from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

try:
    from .ai_audit import build_ai_audit_payload
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from ai_audit import build_ai_audit_payload  # type: ignore


SCHEDULE_TOLERANCE_MINUTES = 5.0
SCENARIO_RESULT_KEYS = {
    "time_constrained": "time_constrained_optimization",
    "exception_preserving": "exception_preserving_optimization",
}


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _json_hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _current_plan_scenario(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    return dict(result.get("current_plan_scenario") or structured.get("current_plan") or {})


def _scenario_record(job: dict[str, Any], scenario_key: str) -> dict[str, Any]:
    result = dict(job.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    result_key = SCENARIO_RESULT_KEYS.get(scenario_key, scenario_key)
    return dict(result.get(result_key) or structured.get(scenario_key) or {})


def _input_stop_signature(job: dict[str, Any]) -> str:
    stops = []
    for point in list(_current_plan_scenario(job).get("points") or []):
        point = dict(point or {})
        if bool(point.get("is_depot")):
            continue
        stops.append(
            (
                _normalized_text(
                    point.get("requested_address")
                    or point.get("address")
                    or point.get("formatted_address")
                ),
                int(point.get("passenger_count", 0) or 0),
            )
        )
    return _json_hash(sorted(stops)) if stops else ""


def _compatibility_parts(job: dict[str, Any]) -> dict[str, Any]:
    config = dict(job.get("config") or dict(job.get("metadata") or {}).get("planner_config") or {})
    summary = dict(job.get("prepared_payload_summary") or {})
    return {
        "input_stops": _input_stop_signature(job),
        "service_direction": config.get("service_direction"),
        "market": {
            "countries": sorted(str(value) for value in list(summary.get("country_samples") or [])),
            "cities": sorted(str(value) for value in list(summary.get("city_samples") or [])),
        },
        "time_window": {
            "start": config.get("time_window_start"),
            "end": config.get("time_window_end"),
            "traffic_profile": config.get("traffic_profile_name"),
        },
        "solver_inputs": {
            key: config.get(key)
            for key in (
                "max_route_duration_minutes",
                "time_impact_limit_minutes",
                "minimum_vehicle_reduction",
                "route_stop_limit",
                "comfort_load_factor",
                "stop_service_minutes",
                "large_bus_name",
                "large_bus_capacity",
                "large_bus_max_count",
                "mid_bus_name",
                "mid_bus_capacity",
                "mid_bus_max_count",
                "small_bus_name",
                "small_bus_capacity",
                "small_bus_max_count",
                "include_nearby_aggregation_scenario",
                "include_subway_aggregation_scenario",
            )
        },
    }


def _compatibility(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = _compatibility_parts(jobs[0])
    issues = []
    for job in jobs[1:]:
        current = _compatibility_parts(job)
        fields = [key for key in baseline if current.get(key) != baseline.get(key)]
        if fields:
            issues.append({"job_id": job.get("job_id"), "fields": fields})
    if not baseline.get("input_stops"):
        issues.append({"job_id": jobs[0].get("job_id"), "fields": ["input_stops_unavailable"]})
    config = dict(jobs[0].get("config") or {})
    return {
        "compatible": not issues,
        "issues": issues,
        "profile": {
            "service_direction": config.get("service_direction"),
            "traffic_profile": config.get("traffic_profile_name"),
            "time_window_start": config.get("time_window_start"),
            "time_window_end": config.get("time_window_end"),
        },
    }


def _plan_fingerprint(scenario: dict[str, Any]) -> str:
    points = [dict(item or {}) for item in list(scenario.get("points") or [])]
    point_keys: dict[int, str] = {}
    for index, point in enumerate(points):
        if bool(point.get("is_depot")):
            key = "depot"
        else:
            key = _json_hash(
                {
                    "address": _normalized_text(
                        point.get("requested_address")
                        or point.get("address")
                        or point.get("formatted_address")
                    ),
                    "lat": round(float(point.get("lat", 0.0) or 0.0), 6),
                    "lng": round(float(point.get("lng", 0.0) or 0.0), 6),
                }
            )
        point_keys[index] = key
        try:
            point_keys[int(point.get("node_id"))] = key
        except (TypeError, ValueError):
            pass

    routes = []
    for route in list(scenario.get("routes") or []):
        route = dict(route or {})
        ordered_stops = []
        for node in list(route.get("nodes") or []):
            try:
                node_key = int(node)
            except (TypeError, ValueError):
                node_key = -1
            ordered_stops.append(point_keys.get(node_key, str(node)))
        routes.append(
            {
                "vehicle": _normalized_text(route.get("bus_type_name") or route.get("bus_type")),
                "stops": ordered_stops,
            }
        )
    return _json_hash(sorted(routes, key=lambda item: json.dumps(item, sort_keys=True)))


def _sample_qualification(
    job: dict[str, Any], recommended: dict[str, Any] | None
) -> tuple[list[str], float | None, str | None]:
    reasons: list[str] = []
    if str(job.get("status") or "") != "succeeded":
        reasons.append("job_not_succeeded")

    scheduled = _parse_datetime(
        job.get("scheduled_start_at")
        or dict(job.get("metadata") or {}).get("scheduled_start_at")
    )
    started = _parse_datetime(job.get("started_at"))
    if scheduled is None:
        reasons.append("not_scheduled")
    elif scheduled.weekday() >= 5:
        reasons.append("weekend_sample")

    delay_minutes = None
    if scheduled is not None and started is not None:
        try:
            delay_minutes = round(abs((started - scheduled).total_seconds()) / 60.0, 1)
        except TypeError:
            delay_minutes = None
    if scheduled is not None and delay_minutes is None:
        reasons.append("missing_actual_start")
    elif delay_minutes is not None and delay_minutes > SCHEDULE_TOLERANCE_MINUTES:
        reasons.append("started_outside_schedule_tolerance")

    if not recommended:
        reasons.append("no_recommended_plan")
    elif not bool(dict(recommended.get("decision_metrics") or {}).get("evidence_complete")):
        reasons.append("incomplete_provider_evidence")
    return reasons, delay_minutes, scheduled.isoformat() if scheduled else None


def _daily_evidence(job: dict[str, Any]) -> dict[str, Any]:
    audit = build_ai_audit_payload(job)
    recommended = dict(audit.get("recommended_scenario") or {}) or None
    reasons, delay_minutes, scheduled_at = _sample_qualification(job, recommended)
    metrics = dict((recommended or {}).get("decision_metrics") or {})
    scenario_key = str((recommended or {}).get("key") or "")
    scenario = _scenario_record(job, scenario_key) if scenario_key else {}
    metadata = dict(job.get("metadata") or {})
    return {
        "job_id": str(job.get("job_id") or ""),
        "job_name": metadata.get("job_name") or metadata.get("source_label") or job.get("job_id"),
        "scheduled_at": scheduled_at,
        "started_at": job.get("started_at"),
        "schedule_delay_minutes": delay_minutes,
        "qualified": not reasons,
        "exclusion_reasons": reasons,
        "scenario_key": scenario_key or None,
        "scenario_name": (recommended or {}).get("name"),
        "recommendation_type": (recommended or {}).get("recommendation_type"),
        "adoption_ready": bool((recommended or {}).get("adoption_ready")),
        "route_count": (recommended or {}).get("route_count"),
        "affected_rider_count": metrics.get("affected_rider_count"),
        "worst_over_limit_minutes": metrics.get("worst_over_limit_minutes"),
        "worst_source": metrics.get("worst_source"),
        "excess_rider_minutes": metrics.get("excess_rider_minutes"),
        "provider_total_duration_minutes": round(
            float((recommended or {}).get("provider_total_duration_s", 0.0) or 0.0) / 60.0,
            1,
        ),
        "plan_fingerprint": _plan_fingerprint(scenario) if scenario else None,
    }


def _number(item: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = item.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _candidate_group(items: list[dict[str, Any]], valid_sample_count: int) -> dict[str, Any]:
    representative = min(
        items,
        key=lambda item: (
            _number(item, "excess_rider_minutes", float("inf")),
            _number(item, "affected_rider_count", float("inf")),
            _number(item, "worst_over_limit_minutes", float("inf")),
            _number(item, "provider_total_duration_minutes", float("inf")),
            -(_parse_datetime(item.get("scheduled_at")).timestamp() if _parse_datetime(item.get("scheduled_at")) else 0),
        ),
    )
    adoption_count = sum(bool(item.get("adoption_ready")) for item in items)
    if adoption_count == len(items):
        status = "operationally_ready"
    elif adoption_count:
        status = "conditionally_viable"
    else:
        status = "review_reference"
    count = len(items)
    return {
        "candidate_id": str(items[0].get("plan_fingerprint") or "")[:12],
        "status": status,
        "sample_count": count,
        "valid_sample_count": valid_sample_count,
        "recurrence_ratio": round(count / valid_sample_count, 3) if valid_sample_count else 0,
        "adoption_ready_day_count": adoption_count,
        "route_count": representative.get("route_count"),
        "scenario_key": representative.get("scenario_key"),
        "scenario_name": representative.get("scenario_name"),
        "representative_job_id": representative.get("job_id"),
        "representative_job_name": representative.get("job_name"),
        "sample_job_ids": [item.get("job_id") for item in items],
        "sample_dates": [item.get("scheduled_at") for item in items],
        "max_affected_rider_count": int(max(_number(item, "affected_rider_count") for item in items)),
        "max_over_limit_minutes": round(max(_number(item, "worst_over_limit_minutes") for item in items), 1),
        "average_excess_rider_minutes": round(sum(_number(item, "excess_rider_minutes") for item in items) / count, 1),
        "average_provider_duration_minutes": round(sum(_number(item, "provider_total_duration_minutes") for item in items) / count, 1),
    }


def build_operations_review(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(jobs) < 2:
        raise ValueError("Select at least two jobs for operations review.")

    compatibility = _compatibility(jobs)
    evidence = sorted(
        [_daily_evidence(job) for job in jobs],
        key=lambda item: str(item.get("scheduled_at") or item.get("job_id") or ""),
    )
    valid = [item for item in evidence if item.get("qualified")]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if compatibility["compatible"]:
        for item in valid:
            fingerprint = str(item.get("plan_fingerprint") or "")
            if fingerprint:
                groups[fingerprint].append(item)

    candidates = [_candidate_group(items, len(valid)) for items in groups.values()]
    status_order = {"operationally_ready": 0, "conditionally_viable": 1, "review_reference": 2}
    candidates.sort(
        key=lambda item: (
            status_order.get(str(item.get("status")), 9),
            -int(item.get("sample_count", 0) or 0),
            _number(item, "average_excess_rider_minutes", float("inf")),
            _number(item, "max_affected_rider_count", float("inf")),
            _number(item, "max_over_limit_minutes", float("inf")),
            int(item.get("route_count", 10**9) or 10**9),
            _number(item, "average_provider_duration_minutes", float("inf")),
        )
    )

    recommendation = candidates[0] if candidates else None
    if not compatibility["compatible"] or len(valid) < 2 or not recommendation:
        overall_status = "insufficient_evidence"
    elif int(recommendation.get("sample_count", 0) or 0) < 2:
        overall_status = "conditionally_viable"
    else:
        overall_status = str(recommendation.get("status") or "review_reference")

    public_evidence = [
        {key: value for key, value in item.items() if key != "plan_fingerprint"}
        for item in evidence
    ]
    return {
        "compatibility": compatibility,
        "selected_job_count": len(jobs),
        "qualified_sample_count": len(valid),
        "excluded_sample_count": len(evidence) - len(valid),
        "status": overall_status,
        "recommendation": recommendation,
        "candidates": candidates,
        "daily_evidence": public_evidence,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
