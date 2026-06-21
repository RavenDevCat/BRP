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


def _collect_traffic_estimates(result: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    structured = dict(result.get("structured_results") or {})
    attribution = dict(result.get("traffic_attribution") or structured.get("traffic_attribution") or {})
    estimates_by_scenario = dict(attribution.get("scenario_route_estimates") or {})
    estimates: list[dict[str, Any]] = []
    for scenario_name, scenario_payload in estimates_by_scenario.items():
        for item in list(dict(scenario_payload or {}).get("route_estimates") or []):
            estimate = dict(item)
            estimates.append(
                {
                    "scenario": estimate.get("scenario") or scenario_name,
                    "route_id": estimate.get("route_id"),
                    "method": estimate.get("method"),
                    "quality_reason": estimate.get("quality_reason") or estimate.get("reason"),
                    "factor": round(float(estimate.get("factor", 0.0) or 0.0), 3),
                    "matched_sample_count": estimate.get("matched_sample_count"),
                    "avg_similarity": round(float(estimate.get("avg_similarity", 0.0) or 0.0), 3),
                }
            )
            if len(estimates) >= limit:
                return estimates
    return estimates


def _traffic_confidence_summary(result: dict[str, Any]) -> dict[str, Any]:
    structured = dict(result.get("structured_results") or {})
    attribution = dict(result.get("traffic_attribution") or structured.get("traffic_attribution") or {})
    scenario_payloads = [dict(item) for item in dict(attribution.get("scenario_route_estimates") or {}).values()]
    method_counts: dict[str, int] = {}
    fallback_route_count = 0
    route_estimate_count = 0
    for payload in scenario_payloads:
        for estimate in list(payload.get("route_estimates") or []):
            method = str(dict(estimate).get("method") or "unknown")
            method_counts[method] = method_counts.get(method, 0) + 1
            route_estimate_count += 1
            if method == "fallback":
                fallback_route_count += 1

    return {
        "traffic_profile_name": result.get("traffic_profile_name") or structured.get("traffic_profile_name"),
        "traffic_time_multiplier": result.get("traffic_time_multiplier") or structured.get("traffic_time_multiplier"),
        "traffic_profile_context": result.get("traffic_profile_context") or structured.get("traffic_profile_context"),
        "attribution_enabled": bool(attribution.get("enabled")),
        "attribution_succeeded": bool(attribution.get("succeeded")),
        "mode": attribution.get("mode"),
        "confidence": attribution.get("confidence"),
        "route_level_applied": bool(attribution.get("route_level_applied")),
        "observed_route_sample_count": attribution.get("observed_route_sample_count"),
        "geo_route_sample_count": attribution.get("geo_route_sample_count"),
        "route_estimate_count": route_estimate_count,
        "fallback_route_count": fallback_route_count,
        "method_counts": method_counts,
        "sample_route_estimates": _collect_traffic_estimates(result),
    }


def _demand_batch_summary(result: dict[str, Any]) -> dict[str, Any]:
    structured = dict(result.get("structured_results") or {})
    scenarios = [
        dict(result.get("free_optimization_baseline") or {}),
        dict(result.get("time_constrained_optimization") or {}),
        dict(structured.get("free_optimization_baseline") or {}),
        dict(structured.get("time_constrained_optimization") or {}),
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
    free_baseline = dict(result.get("free_optimization_baseline") or {})
    time_constrained = dict(result.get("time_constrained_optimization") or {})
    structured = dict(result.get("structured_results") or {})
    current_vs_free = dict(result.get("current_plan_comparison") or {})
    planner_config = dict(metadata.get("planner_config") or job_record.get("config") or {})
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
            "free_optimization": {
                "route_count": free_baseline.get("route_count"),
                "stop_count": _scenario_service_stop_count(free_baseline),
                "avg_route_distance_km": _float_km(free_baseline.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(free_baseline.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(free_baseline.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
                "bus_mix": free_baseline.get("bus_mix"),
            },
            "time_constrained": {
                "route_count": time_constrained.get("route_count") or time_constrained.get("bus_count"),
                "stop_count": _scenario_service_stop_count(time_constrained),
                "avg_route_distance_km": _float_km(time_constrained.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(time_constrained.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(time_constrained.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
                "time_constraint": time_constrained.get("time_constraint"),
            },
        },
        "comparisons": {
            "current_vs_free": current_vs_free,
        },
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
            "further_most": dict(result.get("further_most_private_access_analysis") or {}).get("summary"),
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
            "traffic_confidence": _traffic_confidence_summary(result),
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
        "Treat decision_review as deterministic evidence, not optional decoration. "
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
    if "korean" in normalized or "한국" in normalized or "한글" in normalized:
        return "\n".join(
            [
                "## 종합 결정",
                "## 입력 품질 검토",
                "## 현행 계획 상태",
                "## 최적화 영향",
                "## 시간 신뢰도",
                "## 운영 조치",
            ],
        )
    return "\n".join(
        [
            "## Executive Decision",
            "## Input Quality Review",
            "## Current Plan Health",
            "## Optimization Impact",
            "## Timing Confidence",
            "## Operator Actions",
        ],
    )
