from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any

import requests

from api_rate_limit import CrossProcessRateLimiter


DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
AI_AUDIT_LANGUAGE = os.environ.get("BRP_AI_AUDIT_LANGUAGE", "English").strip() or "English"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except ValueError:
        return default


AI_AUDIT_TIMEOUT_SECONDS = _int_env("BRP_AI_AUDIT_TIMEOUT_SECONDS", 90)
AI_AUDIT_MAX_TOKENS = _int_env("BRP_AI_AUDIT_MAX_TOKENS", 1600)
DEEPSEEK_MAX_QPS = _float_env("BRP_DEEPSEEK_MAX_QPS", 1.0)
DEEPSEEK_LIMITER = CrossProcessRateLimiter("deepseek-chat-completions", DEEPSEEK_MAX_QPS)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _float_minutes(value: Any) -> float:
    return round(float(value or 0.0) / 60.0, 1)


def _float_km(value: Any) -> float:
    return round(float(value or 0.0) / 1000.0, 1)


def _take(items: Any, limit: int) -> list[Any]:
    return list(items or [])[:limit]


def _finite_int(value: Any) -> int | None:
    try:
        numeric_value = int(value)
    except Exception:
        return None
    return numeric_value


def _route_service_stop_count(route: dict[str, Any]) -> int | None:
    explicit_service_count = _finite_int(route.get("service_stop_count") or route.get("student_stop_count"))
    if explicit_service_count is not None:
        return explicit_service_count
    explicit_stop_count = _finite_int(route.get("stop_count") or route.get("stops"))
    if explicit_stop_count is None:
        nodes = list(route.get("nodes") or [])
        return max(0, len(nodes) - 1) if nodes else None
    if _finite_int(route.get("scheduled_stop_count") or route.get("all_stop_count")) is not None:
        return explicit_stop_count
    nodes = list(route.get("nodes") or [])
    if nodes:
        return max(0, len(nodes) - 1)
    return max(0, explicit_stop_count - 1)


def _assessment_service_stop_count(assessment: dict[str, Any]) -> Any:
    explicit_service_count = _finite_int(assessment.get("service_stop_count") or assessment.get("student_stop_count"))
    if explicit_service_count is not None:
        return explicit_service_count
    route_summaries = [dict(item) for item in list(assessment.get("route_summaries") or [])]
    if route_summaries:
        return sum(_route_service_stop_count(item) or 0 for item in route_summaries)
    explicit_stop_count = _finite_int(assessment.get("stop_count"))
    if explicit_stop_count is None:
        return assessment.get("stop_count")
    route_count = _finite_int(assessment.get("route_count")) or 0
    return max(0, explicit_stop_count - route_count)


def _assessment_assignment_count(assessment: dict[str, Any]) -> Any:
    explicit_assignment_count = _finite_int(assessment.get("assignment_count"))
    if explicit_assignment_count is None:
        return assessment.get("assignment_count")
    if _finite_int(assessment.get("scheduled_assignment_count")) is not None:
        return explicit_assignment_count
    route_count = _finite_int(assessment.get("route_count")) or 0
    return max(0, explicit_assignment_count - route_count)


def _scenario_service_stop_count(scenario: dict[str, Any]) -> Any:
    explicit_service_count = _finite_int(scenario.get("service_stop_count") or scenario.get("student_stop_count"))
    if explicit_service_count is not None:
        return explicit_service_count
    points = [dict(item) for item in list(scenario.get("points") or [])]
    if points:
        return len([point for point in points if not bool(point.get("is_depot"))])
    return scenario.get("stop_count")


def _compact_traffic_gate(scenario: dict[str, Any]) -> dict[str, Any]:
    gate = dict(scenario.get("traffic_gate") or {})
    if not gate:
        return {"available": False, "status": "not_applicable"}
    return {
        "available": True,
        "status": gate.get("status"),
        "checked_route_count": gate.get("checked_route_count"),
        "failed_route_count": gate.get("failed_route_count"),
        "unavailable_route_count": gate.get("unavailable_route_count"),
        "max_time_window_overrun_min": gate.get("max_time_window_overrun_minutes")
        or gate.get("max_estimated_arrival_delay_minutes"),
        "earliest_departure": "06:00",
        "latest_arrival": gate.get("target_arrival_label") or "08:00",
        "vehicle_saving_target": gate.get("vehicle_saving_target")
        or scenario.get("vehicle_saving_target"),
    }


def _compact_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_id": route.get("route_id"),
        "bus_type": route.get("bus_type"),
        "stop_count": _route_service_stop_count(route),
        "passenger_count": route.get("passenger_count"),
        "load_factor_pct": round(float(route.get("load_factor", 0.0) or 0.0) * 100.0, 1),
        "distance_km": _float_km(route.get("distance_m")),
        "duration_min": _float_minutes(route.get("duration_s")),
    }


def _compact_move(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": item.get("route_action_label") or item.get("recommendation_type"),
        "from_route_id": item.get("from_route_id"),
        "to_route_id": item.get("to_route_id"),
        "stop_count": item.get("stop_count"),
        "time_saving_min": _float_minutes(item.get("network_total_duration_saving_s")),
        "distance_saving_km": _float_km(item.get("network_total_distance_saving_m")),
        "explanation": str(item.get("explanation", "") or "")[:420],
    }


def _ratio_pct(value: Any) -> float:
    return round(float(value or 0.0) * 100.0, 1)


def _compact_time_impact(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {"available": False, "decision": "incomplete"}
    available = bool(summary.get("available"))
    if not available:
        return {
            "available": False,
            "decision": "incomplete",
            "service_stop_count": summary.get("service_stop_count"),
            "unavailable_stop_count": summary.get("unavailable_stop_count"),
        }

    over_stop_count = int(summary.get("over_acceptance_stop_count", 0) or 0)
    over_rider_count = int(summary.get("over_acceptance_rider_count", 0) or 0)
    high_risk_stop_count = int(summary.get("high_risk_stop_count", 0) or 0)
    high_risk_rider_count = int(summary.get("high_risk_rider_count", 0) or 0)
    decision = "acceptable"
    if high_risk_stop_count or high_risk_rider_count:
        decision = "high_risk"
    elif over_stop_count or over_rider_count:
        decision = "review_needed"

    return {
        "available": True,
        "decision": decision,
        "acceptance_threshold_minutes": summary.get("acceptance_threshold_minutes"),
        "compared_stop_count": summary.get("compared_stop_count"),
        "compared_rider_count": summary.get("compared_rider_count"),
        "acceptance_rider_pct": _ratio_pct(summary.get("acceptance_rider_ratio")),
        "over_acceptance_stop_count": over_stop_count,
        "over_acceptance_rider_count": over_rider_count,
        "high_risk_stop_count": high_risk_stop_count,
        "high_risk_rider_count": high_risk_rider_count,
        "max_adverse_delta_minutes": round(float(summary.get("max_adverse_delta_minutes", 0.0) or 0.0), 1),
        "max_over_acceptance_delta_minutes": round(float(summary.get("max_over_acceptance_delta_minutes", 0.0) or 0.0), 1),
        "weighted_avg_adverse_delta_minutes": round(float(summary.get("weighted_avg_adverse_delta_minutes", 0.0) or 0.0), 1),
        "worse_rider_count": summary.get("worse_rider_count"),
        "better_rider_count": summary.get("better_rider_count"),
        "route_changed_rider_count": summary.get("route_changed_rider_count"),
        "top_impacted_stops": [
            {
                "route_id": item.get("new_route_id") or item.get("route_id"),
                "affected_rider_count": item.get("affected_rider_count"),
                "adverse_delta_minutes": item.get("adverse_delta_minutes"),
                "impact_direction": item.get("impact_direction"),
                "acceptance_status": item.get("acceptance_status"),
            }
            for item in _take(summary.get("top_impacted_stops"), 5)
        ],
    }


def _format_time_impact_limit_minutes(value: Any) -> str:
    try:
        numeric = max(0.0, float(value))
    except (TypeError, ValueError):
        numeric = 15.0
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.1f}".rstrip("0").rstrip(".")


def _time_impact_limit_minutes(result: dict[str, Any], scenario: dict[str, Any], planner_config: dict[str, Any]) -> float:
    constraint = dict(scenario.get("time_constraint") or {})
    for value in (
        scenario.get("time_impact_limit_minutes"),
        constraint.get("time_impact_limit_minutes"),
        constraint.get("threshold_minutes"),
        dict(result.get("planner_config") or {}).get("time_impact_limit_minutes"),
        planner_config.get("time_impact_limit_minutes"),
    ):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return 15.0


def _scenario_display_name(key: str, result: dict[str, Any], scenario: dict[str, Any], planner_config: dict[str, Any]) -> str:
    label = str(scenario.get("display_name") or scenario.get("scenario_label") or "").strip()
    if key == "current_plan":
        return "Current Plan"
    if key == "time_constrained":
        if not label or label.endswith("-Minute Constrained") or label.endswith("-Minute Balanced Plan"):
            return "Strict Plan"
    if key == "exception_preserving":
        if not label or label in {"Exception Preserving", "Protected Route Plan"}:
            return "Protected Plan"
    return label or key


def _scenario_summary(
    key: str,
    result: dict[str, Any],
    scenario: dict[str, Any],
    planner_config: dict[str, Any],
) -> dict[str, Any]:
    enabled = bool(scenario) and scenario.get("enabled") is not False
    time_constraint = dict(scenario.get("time_constraint") or {})
    time_impact = _compact_time_impact(
        dict(scenario.get("time_impact") or {})
        or dict(dict(scenario.get("summary") or {}).get("time_impact") or {})
    )
    provider_total_duration_s = sum(
        float(dict(route.get("final_route_traffic_gate") or {}).get("verified_total_duration_s", 0.0) or 0.0)
        for route in list(scenario.get("routes") or [])
    )
    return {
        "key": key,
        "name": _scenario_display_name(key, result, scenario, planner_config),
        "enabled": enabled,
        "skipped_reason": scenario.get("skipped_reason"),
        "route_count": scenario.get("route_count") or scenario.get("bus_count"),
        "stop_count": _scenario_service_stop_count(scenario),
        "avg_route_distance_km": _float_km(scenario.get("avg_route_distance_m")),
        "avg_route_duration_min": _float_minutes(scenario.get("avg_route_duration_s")),
        "avg_load_factor_pct": round(float(scenario.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
        "bus_mix": scenario.get("bus_mix"),
        "provider_total_duration_s": provider_total_duration_s,
        "traffic_gate": _compact_traffic_gate(scenario),
        "feasibility_report": dict(scenario.get("feasibility_report") or {}),
        "final_time_impact_gate": dict(scenario.get("final_time_impact_gate") or {}),
        "exception_accepted": bool(dict(scenario.get("exception_preserving") or {}).get("accepted") or scenario.get("exception_feasible")),
        "time_constraint": {
            key: time_constraint.get(key)
            for key in (
                "enabled",
                "mode",
                "strict_satisfied",
                "bounded_solver_stop_count",
                "expected_solver_stop_count",
                "threshold_minutes",
            )
            if key in time_constraint
        },
        "time_impact": time_impact,
    }


def _scenario_time_impact_passed(scenario: dict[str, Any]) -> bool:
    final_gate = dict(scenario.get("final_time_impact_gate") or {})
    if final_gate:
        return str(final_gate.get("status") or "") == "passed"
    constraint = dict(scenario.get("time_constraint") or {})
    if constraint.get("enabled") is True and str(constraint.get("mode") or "") == "hard":
        return False
    hard_constraint = dict(
        dict(scenario.get("feasibility_report") or {}).get("hard_constraints") or {}
    ).get("time_impact")
    if isinstance(hard_constraint, dict) and hard_constraint:
        return str(hard_constraint.get("status") or "") == "passed"
    time_impact = dict(scenario.get("time_impact") or {})
    if time_impact.get("available"):
        return str(time_impact.get("decision") or "") == "acceptable"
    strict_satisfied = constraint.get("strict_satisfied")
    if isinstance(strict_satisfied, bool):
        return strict_satisfied
    bounded = int(constraint.get("bounded_solver_stop_count", 0) or 0)
    expected = int(constraint.get("expected_solver_stop_count", 0) or scenario.get("stop_count") or 0)
    return (
        constraint.get("enabled") is True
        and str(constraint.get("mode") or "") == "hard"
        and expected > 0
        and bounded >= expected
    )


def _recommended_scenario(scenarios: list[dict[str, Any]]) -> dict[str, Any] | None:
    order = ["time_constrained", "exception_preserving"]
    order_index = {key: index for index, key in enumerate(order)}
    ready: list[dict[str, Any]] = []
    for item in scenarios:
        key = str(item.get("key") or "")
        if key not in order_index or not item.get("enabled"):
            continue
        gate = dict(item.get("traffic_gate") or {})
        saving = dict(gate.get("vehicle_saving_target") or {})
        provider_passed = gate.get("status") == "passed"
        protected_passed = key == "exception_preserving" and bool(item.get("exception_accepted"))
        if (not provider_passed and not protected_passed) or saving.get("status") == "failed":
            continue
        if not _scenario_time_impact_passed(item):
            continue
        ready.append(item)
    return min(
        ready,
        key=lambda item: (
            int(item.get("route_count", 10**9) or 10**9),
            float(item.get("provider_total_duration_s", float("inf")) or float("inf")),
            order_index[str(item.get("key") or "")],
        ),
        default=None,
    )


def _input_address_review_summary(review: dict[str, Any]) -> dict[str, Any]:
    summary = dict(review.get("summary") or {})
    warnings = [dict(item) for item in list(review.get("warnings") or [])]
    return {
        "warning_count": int(summary.get("warning_count", len(warnings)) or 0),
        "school_distance_warning_count": int(summary.get("school_distance_warning_count", 0) or 0),
        "region_mismatch_warning_count": int(summary.get("region_mismatch_warning_count", 0) or 0),
        "route_context_warning_count": int(summary.get("route_context_warning_count", 0) or 0),
        "top_warnings": [
            {
                "type": item.get("type"),
                "route_id": item.get("route_id"),
                "status": item.get("status"),
                "accepted": item.get("accepted"),
                "distance_from_school_km": item.get("distance_from_school_km"),
                "expected_city": item.get("expected_city"),
                "resolved_city": item.get("resolved_city"),
                "detour_ratio": item.get("detour_ratio"),
            }
            for item in _take(warnings, 6)
        ],
    }


def _provider_validation_summary(result: dict[str, Any]) -> dict[str, Any]:
    structured = dict(result.get("structured_results") or {})
    strict = dict(result.get("time_constrained_optimization") or structured.get("time_constrained") or {})
    protected = dict(result.get("exception_preserving_optimization") or structured.get("exception_preserving") or {})
    return {
        "traffic_profile_name": result.get("traffic_profile_name") or structured.get("traffic_profile_name"),
        "traffic_profile_context": result.get("traffic_profile_context") or structured.get("traffic_profile_context"),
        "strict_plan": _compact_traffic_gate(strict),
        "protected_plan": _compact_traffic_gate(protected),
    }


def _demand_batch_summary(result: dict[str, Any]) -> dict[str, Any]:
    structured = dict(result.get("structured_results") or {})
    scenarios = [
        dict(result.get("time_constrained_optimization") or {}),
        dict(structured.get("time_constrained_optimization") or {}),
        dict(structured.get("time_constrained") or {}),
    ]
    batch_points = []
    for scenario in scenarios:
        for point in list(scenario.get("points") or []):
            point_data = dict(point)
            batch_count = _finite_int(point_data.get("demand_batch_count"))
            if batch_count and batch_count > 1:
                batch_points.append(
                    {
                        "passenger_count": point_data.get("passenger_count"),
                        "batch_index": point_data.get("demand_batch_index"),
                        "batch_count": batch_count,
                    }
                )
    return {
        "has_split_stop_batches": bool(batch_points),
        "split_stop_batch_count": len(batch_points),
        "sample_batches": _take(batch_points, 6),
    }


def _clean_report_markdown(markdown: str) -> str:
    cleaned_lines: list[str] = []
    previous_blank = False
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            if cleaned_lines and not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue
        if line in {"---", "***", "___", ">"}:
            continue
        line = line.lstrip(">").strip()
        if not line:
            continue
        cleaned_lines.append(line)
        previous_blank = False
    return "\n".join(cleaned_lines).strip()


def build_ai_audit_payload(job_record: dict[str, Any]) -> dict[str, Any]:
    result = dict(job_record.get("result") or {})
    metadata = dict(job_record.get("metadata") or {})
    current_plan = dict(result.get("current_plan_assessment") or {})
    route_reallocation = dict(result.get("route_reallocation_analysis") or {})
    reallocation_summary = dict(route_reallocation.get("summary") or {})
    time_constrained = dict(result.get("time_constrained_optimization") or {})
    structured = dict(result.get("structured_results") or {})
    current_plan_scenario = dict(structured.get("current_plan") or result.get("current_plan_scenario") or {})
    if not current_plan_scenario and current_plan:
        current_plan_scenario = {
            "enabled": True,
            "route_count": current_plan.get("route_count"),
            "service_stop_count": _assessment_service_stop_count(current_plan),
            "avg_route_distance_m": current_plan.get("avg_route_distance_m"),
            "avg_route_duration_s": current_plan.get("avg_route_duration_s"),
            "avg_load_factor": current_plan.get("avg_load_factor"),
            "bus_mix": current_plan.get("bus_mix"),
        }
    exception_preserving = dict(result.get("exception_preserving_optimization") or structured.get("exception_preserving") or {})
    current_vs_baseline = dict(result.get("current_plan_comparison") or {})
    planner_config = dict(metadata.get("planner_config") or job_record.get("config") or {})
    time_impact_limit_minutes = _time_impact_limit_minutes(result, time_constrained, planner_config)
    scenario_outcomes = [
        _scenario_summary("current_plan", result, current_plan_scenario, planner_config),
        _scenario_summary("time_constrained", result, time_constrained, planner_config),
        _scenario_summary("exception_preserving", result, exception_preserving, planner_config),
    ]
    recommended_scenario = _recommended_scenario(scenario_outcomes)
    route_summaries = list(current_plan.get("route_summaries") or [])
    route_summaries = sorted(
        route_summaries,
        key=lambda item: (
            float(dict(item).get("load_factor", 0.0) or 0.0),
            -float(dict(item).get("duration_s", 0.0) or 0.0),
        ),
    )

    return {
        "job": {
            "job_id": job_record.get("job_id"),
            "job_name": metadata.get("job_name") or metadata.get("source_label"),
            "owner_email": job_record.get("owner_email"),
            "service_direction": result.get("service_direction") or planner_config.get("service_direction"),
            "traffic_profile": result.get("traffic_profile_name") or planner_config.get("traffic_profile_name"),
            "target_route_duration_min": planner_config.get("max_route_duration_minutes"),
            "time_impact_limit_minutes": time_impact_limit_minutes,
        },
        "current_plan": {
            "route_count": current_plan.get("route_count"),
            "stop_count": _assessment_service_stop_count(current_plan),
            "assignment_count": _assessment_assignment_count(current_plan),
            "avg_load_factor_pct": round(float(current_plan.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
            "avg_route_distance_km": _float_km(current_plan.get("avg_route_distance_m")),
            "avg_route_duration_min": _float_minutes(current_plan.get("avg_route_duration_s")),
            "low_load_route_count": current_plan.get("low_load_route_count"),
            "overlong_route_count": current_plan.get("overlong_route_count"),
            "bus_mix": current_plan.get("bus_mix"),
            "system_findings": _take(current_plan.get("recommendations"), 6),
            "weakest_routes": [_compact_route(dict(item)) for item in _take(route_summaries, 8)],
        },
        "benchmarks": {
            "time_constrained": {
                "route_count": time_constrained.get("route_count") or time_constrained.get("bus_count"),
                "stop_count": _scenario_service_stop_count(time_constrained),
                "avg_route_distance_km": _float_km(time_constrained.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(time_constrained.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(time_constrained.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
                "time_constraint": time_constrained.get("time_constraint"),
                "am_time_window": _compact_traffic_gate(time_constrained),
            },
        },
        "comparisons": {
            "current_vs_baseline": current_vs_baseline,
            "current_vs_recommended": {
                "recommended_scenario_key": recommended_scenario.get("key") if recommended_scenario else None,
                "recommended_scenario_name": recommended_scenario.get("name") if recommended_scenario else None,
                "current_route_count": current_plan.get("route_count"),
                "recommended_route_count": recommended_scenario.get("route_count") if recommended_scenario else None,
            },
        },
        "scenario_outcomes": scenario_outcomes,
        "recommended_scenario": recommended_scenario,
        "local_reallocation": {
            "summary": reallocation_summary,
            "priority_actions": [
                _compact_move(dict(item))
                for item in _take(
                    reallocation_summary.get("priority_recommendations")
                    or route_reallocation.get("recommendations"),
                    8,
                )
            ],
        },
        "private_access": {
            "nearby": dict(result.get("nearby_private_access_analysis") or {}).get("summary"),
        },
        "decision_review": {
            "time_impact": _compact_time_impact(
                dict(time_constrained.get("time_impact") or {})
                or dict(dict(time_constrained.get("summary") or {}).get("time_impact") or {})
                or dict(dict(structured.get("time_constrained_optimization") or {}).get("time_impact") or {})
            ),
            "input_address_review": _input_address_review_summary(
                dict(result.get("input_address_review") or structured.get("input_address_review") or {})
            ),
            "provider_validation": _provider_validation_summary(result),
            "aggregated_stop_batches": _demand_batch_summary(result),
        },
        "constraints": {
            "no_full_address_list": True,
            "source": "Derived from BRP deterministic route audit outputs only.",
            "input_policy": "Aggregated metrics and review summaries only; full address list excluded.",
        },
    }


def generate_ai_audit_report(job_record: dict[str, Any], *, force: bool = False, language: str | None = None) -> dict[str, Any]:
    existing = job_record.get("ai_audit_report")
    if existing and not force:
        return dict(existing)
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
    if not job_record.get("result"):
        raise RuntimeError("AI audit can only be generated for a completed job with results.")

    payload = build_ai_audit_payload(job_record)
    report_language = (language or AI_AUDIT_LANGUAGE or "English").strip()
    system_prompt = (
        "You are a school bus operations audit analyst. Use only the supplied JSON facts. "
        "Do not invent addresses, route metrics, savings, timing changes, or decisions. "
        "Write a clean management briefing for operators. "
        "Prefer readable business language over template language. "
        "Treat scenario_outcomes, recommended_scenario, and decision_review as deterministic evidence. "
        "Do not recommend adopting a scenario whose traffic_gate status is failed or unavailable unless exception_accepted is true. "
        "Keep recommendations practical and clearly separate measured facts from interpretation."
    )
    user_prompt = (
        f"Write the report in {report_language}. Maximum 3,200 characters.\n"
        "Format as a readable Markdown briefing with these section headings only:\n"
        f"{_ai_audit_section_headings(report_language)}\n\n"
        "Style rules:\n"
        "- Start directly with the first heading; do not add a title line.\n"
        "- Do not use horizontal rules, quote blocks, tables, code fences, or decorative separators.\n"
        "- Use 2 to 3 short bullets per section, maximum 24 words per bullet.\n"
        "- Use route IDs only when present in the facts.\n"
        "- Make recommendations specific but do not repeat every candidate action.\n"
        "- Do not mention student names or full addresses.\n"
        "- Prefer recommended_scenario when it is adoption-ready; explain why skipped or failed scenarios are not recommended.\n"
        "- Cover input address review, time impact, and traffic confidence when facts are available.\n"
        "- If evidence is insufficient, state the validation question plainly.\n\n"
        f"FACTS JSON:\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    DEEPSEEK_LIMITER.wait()
    response = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": AI_AUDIT_MAX_TOKENS,
        },
        timeout=AI_AUDIT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise RuntimeError("DeepSeek returned no report choices.")
    message = dict(choices[0].get("message") or {})
    report_markdown = _clean_report_markdown(str(message.get("content") or ""))
    if not report_markdown:
        raise RuntimeError("DeepSeek returned an empty report.")
    return {
        "generated_at": utc_now_iso(),
        "provider": "deepseek",
        "model": DEEPSEEK_MODEL,
        "language": report_language,
        "report_markdown": report_markdown[:6000],
        "input_policy": "Aggregated route metrics and review summaries only; full address list excluded.",
    }


def _ai_audit_section_headings(language: str) -> str:
    normalized = language.strip().lower()
    if (
        normalized.startswith("zh")
        or normalized.startswith("cn")
        or "chinese" in normalized
        or "中文" in language
        or "汉语" in language
        or "漢語" in language
    ):
        return "\n".join(
            [
                "## 执行结论",
                "## 为什么选择这个方案",
                "## 时间窗影响",
                "## 运营取舍",
                "## 优先复核线路",
                "## 注意事项",
            ],
        )
    if "korean" in normalized or "한국" in normalized or "한글" in normalized:
        return "\n".join(
            [
                "## 종합 결론",
                "## 이 계획을 선택한 이유",
                "## 시간창 영향",
                "## 운영상 절충점",
                "## 우선 검토 노선",
                "## 유의 사항",
            ],
        )
    return "\n".join(
        [
            "## Executive conclusion",
            "## Why this plan",
            "## Time-window impact",
            "## Operational tradeoffs",
            "## Routes to review",
            "## Caveats",
        ],
    )
