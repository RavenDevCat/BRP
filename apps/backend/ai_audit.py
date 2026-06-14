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
        "constraints": {
            "no_full_address_list": True,
            "source": "Derived from BRP deterministic route audit outputs only.",
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
        "Do not invent addresses, route metrics, savings, or decisions. "
        "Write a clean management briefing for operators. "
        "Prefer readable business language over template language. "
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
        "input_policy": "Aggregated route metrics only; full address list excluded.",
    }


def _ai_audit_section_headings(language: str) -> str:
    normalized = language.strip().lower()
    if "korean" in normalized or "한국" in normalized or "한글" in normalized:
        return "\n".join(
            [
                "## 종합 판단",
                "## 현행 계획 사실",
                "## 기준선 비교",
                "## 우선 조치",
                "## 검증 메모",
            ],
        )
    return "\n".join(
        [
            "## Executive Verdict",
            "## Current Scheme Facts",
            "## Baseline Comparison",
            "## Priority Actions",
            "## Validation Notes",
        ],
    )
