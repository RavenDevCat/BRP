from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any

import requests


DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
AI_AUDIT_LANGUAGE = os.environ.get("BRP_AI_AUDIT_LANGUAGE", "English").strip() or "English"
AI_AUDIT_TIMEOUT_SECONDS = int(os.environ.get("BRP_AI_AUDIT_TIMEOUT_SECONDS", "90") or 90)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _float_minutes(value: Any) -> float:
    return round(float(value or 0.0) / 60.0, 1)


def _float_km(value: Any) -> float:
    return round(float(value or 0.0) / 1000.0, 1)


def _take(items: Any, limit: int) -> list[Any]:
    return list(items or [])[:limit]


def _compact_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_id": route.get("route_id"),
        "bus_type": route.get("bus_type"),
        "stop_count": route.get("stop_count"),
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


def build_ai_audit_payload(job_record: dict[str, Any]) -> dict[str, Any]:
    result = dict(job_record.get("result") or {})
    metadata = dict(job_record.get("metadata") or {})
    current_plan = dict(result.get("current_plan_assessment") or {})
    route_reallocation = dict(result.get("route_reallocation_analysis") or {})
    reallocation_summary = dict(route_reallocation.get("summary") or {})
    free_baseline = dict(result.get("free_optimization_baseline") or {})
    like_for_like = dict(result.get("like_for_like_baseline") or {})
    constrained = dict(result.get("constrained_improvement_baseline") or {})
    current_vs_free = dict(result.get("current_plan_comparison") or {})
    current_vs_like = dict(result.get("current_plan_like_for_like_comparison") or {})
    current_vs_constrained = dict(result.get("current_plan_constrained_comparison") or {})
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
            "stop_count": current_plan.get("stop_count"),
            "assignment_count": current_plan.get("assignment_count"),
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
                "avg_route_distance_km": _float_km(free_baseline.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(free_baseline.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(free_baseline.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
                "bus_mix": free_baseline.get("bus_mix"),
            },
            "like_for_like": {
                "route_count": like_for_like.get("route_count"),
                "avg_route_distance_km": _float_km(like_for_like.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(like_for_like.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(like_for_like.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
            },
            "constrained_improvement": {
                "route_count": constrained.get("route_count"),
                "avg_route_distance_km": _float_km(constrained.get("avg_route_distance_m")),
                "avg_route_duration_min": _float_minutes(constrained.get("avg_route_duration_s")),
                "avg_load_factor_pct": round(float(constrained.get("avg_load_factor", 0.0) or 0.0) * 100.0, 1),
            },
        },
        "comparisons": {
            "current_vs_free": current_vs_free,
            "current_vs_like_for_like": current_vs_like,
            "current_vs_constrained": current_vs_constrained,
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
        "Write a concise management-facing audit report. "
        "Keep recommendations practical and distinguish fact from interpretation."
    )
    user_prompt = (
        f"Write the report in {report_language}. Maximum 4,500 characters.\n"
        "Use exactly these Markdown sections:\n"
        "1. Executive Verdict\n"
        "2. Current Scheme Facts\n"
        "3. Comparison Against Baselines\n"
        "4. Actionable Recommendations\n"
        "5. Risks And Validation Questions\n\n"
        "Rules:\n"
        "- 2 to 4 bullets per section.\n"
        "- Mention route IDs only when present in the facts.\n"
        "- Do not mention student names or full addresses.\n"
        "- If evidence is insufficient, say what needs validation.\n\n"
        f"FACTS JSON:\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
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
            "max_tokens": 1600,
        },
        timeout=AI_AUDIT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise RuntimeError("DeepSeek returned no report choices.")
    message = dict(choices[0].get("message") or {})
    report_markdown = str(message.get("content") or "").strip()
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
