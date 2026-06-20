#!/usr/bin/env python3
"""Summarize traffic rollout readiness, timers, and OSRM manager health.

This script is read-only. It does not call traffic providers, start jobs, start
OSRM, or modify sample files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import report_traffic_rollout_readiness  # noqa: E402
import report_live_traffic_budget  # noqa: E402


DEFAULT_TIMER_UNITS = (
    "brp-live-traffic-am.timer",
    "brp-live-traffic-pm.timer",
    "brp-live-traffic-suzhou-am.timer",
    "brp-live-traffic-suzhou-pm.timer",
    "brp-osrm-cleanup.timer",
)

DEFAULT_SERVICE_UNITS = (
    "brp-live-traffic-am.service",
    "brp-live-traffic-pm.service",
    "brp-live-traffic-suzhou-am.service",
    "brp-live-traffic-suzhou-pm.service",
    "brp-osrm-cleanup.service",
)

DEFAULT_LOCAL_TIMEZONE = "Asia/Shanghai"
DEFAULT_MARKET_STALE_HOURS = 24 * 7
BANGKOK_STATIC_FALLBACK_MULTIPLIER = 1.75

MARKET_OVERVIEW_DEFINITIONS = (
    {
        "market": "CN",
        "city": "Shanghai",
        "label": "CN / Shanghai",
        "traffic_mode": "attributed",
        "provider": "amap",
        "active_source": "route-network live samples",
        "required_periods": ("am_peak", "pm_peak"),
        "fallback_multiplier": None,
        "requires_samples": True,
    },
    {
        "market": "CN",
        "city": "Suzhou",
        "label": "CN / Suzhou",
        "traffic_mode": "attributed",
        "provider": "amap",
        "active_source": "route-network live samples",
        "required_periods": ("am_peak", "pm_peak"),
        "fallback_multiplier": None,
        "requires_samples": True,
    },
    {
        "market": "KR",
        "city": "Seoul Metro",
        "label": "KR / Seoul Metro",
        "traffic_mode": "attributed",
        "provider": "kakao_navi",
        "active_source": "weekday route-network samples",
        "required_periods": ("am_peak", "pm_peak", "off_peak"),
        "fallback_multiplier": None,
        "requires_samples": True,
    },
    {
        "market": "BK",
        "city": "Bangkok",
        "label": "BK / Bangkok",
        "traffic_mode": "static_fallback",
        "provider": "static",
        "active_source": "all-day fallback coefficient",
        "required_periods": ("all_day",),
        "fallback_multiplier": BANGKOK_STATIC_FALLBACK_MULTIPLIER,
        "requires_samples": False,
    },
)


def _run_command(command: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def _parse_show_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def _load_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or DEFAULT_LOCAL_TIMEZONE))
    except Exception:
        return ZoneInfo(DEFAULT_LOCAL_TIMEZONE)


def _parse_systemd_utc_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"n/a", "never"}:
        return None
    if raw.endswith(" UTC"):
        raw = raw[:-4]
    for fmt in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _timestamp_local(value: Any, local_tz: ZoneInfo) -> str:
    timestamp = _parse_systemd_utc_timestamp(value)
    if timestamp is None:
        return ""
    return timestamp.astimezone(local_tz).isoformat(timespec="seconds")


def _parse_iso_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: Any, now: datetime | None = None) -> float | None:
    timestamp = _parse_iso_timestamp(value)
    if timestamp is None:
        return None
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(0.0, (now_utc - timestamp).total_seconds() / 3600.0)


def _normalize_profile_key(market: Any, city: Any, period: Any) -> tuple[str, str, str]:
    market_value = str(market or "").strip().casefold()
    city_value = str(city or "").strip().casefold()
    period_value = str(period or "").strip().casefold()
    if market_value in {"south korea", "korea", "kr"}:
        market_value = "kr"
    if market_value == "cn":
        market_value = "cn"
    if market_value in {"bk", "bangkok", "th", "thailand"}:
        market_value = "bk"
    if market_value == "kr" and city_value in {"seoul", "seoul metro", "incheon", "gyeonggi"}:
        city_value = "seoul metro"
    return market_value, city_value, period_value


def _normalize_market_code(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"cn", "china", "shanghai", "suzhou"}:
        return "CN"
    if normalized in {"kr", "korea", "south korea", "seoul", "seoul metro"}:
        return "KR"
    if normalized in {"bk", "bangkok", "th", "thailand"}:
        return "BK"
    return normalized.upper()


def _group_index(groups: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in groups:
        if not isinstance(row, dict):
            continue
        indexed[_normalize_profile_key(row.get("market"), row.get("city"), row.get("period"))] = row
    return indexed


def _market_scope_for_current_environment() -> set[str]:
    configured = os.environ.get("BRP_TRAFFIC_STATUS_MARKETS", "").strip()
    if configured:
        return {
            market
            for market in (_normalize_market_code(item) for item in configured.split(","))
            if market
        }
    if os.name == "nt":
        return {"KR"}
    return {"CN", "BK"}


def _period_row(period: str, group: dict[str, Any] | None, *, now: datetime | None) -> dict[str, Any]:
    if not group:
        return {
            "period": period,
            "status": "missing",
            "sample_file_count": 0,
            "route_sample_count": 0,
            "geo_route_sample_count": 0,
            "geo_route_sample_ratio": 0.0,
            "latest_measured_at": "",
            "latest_sample": "",
            "providers": [],
            "weekdays": [],
            "age_hours": None,
        }
    age = _age_hours(group.get("latest_measured_at"), now)
    return {
        "period": period,
        "status": "ok",
        "sample_file_count": int(group.get("sample_file_count") or 0),
        "route_sample_count": int(group.get("route_sample_count") or 0),
        "geo_route_sample_count": int(group.get("geo_route_sample_count") or 0),
        "geo_route_sample_ratio": float(group.get("geo_route_sample_ratio") or 0.0),
        "latest_measured_at": group.get("latest_measured_at") or "",
        "latest_sample": group.get("latest_sample") or "",
        "providers": list(group.get("providers") or []),
        "weekdays": list(group.get("weekdays") or []),
        "age_hours": age,
    }


def build_market_overview(
    sample_dir: Path,
    *,
    stale_after_hours: int = DEFAULT_MARKET_STALE_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    summary = report_traffic_rollout_readiness.report_live_traffic_readiness.summarize(sample_dir)
    groups = _group_index([row for row in summary.get("groups", []) if isinstance(row, dict)])
    market_scope = _market_scope_for_current_environment()
    markets: list[dict[str, Any]] = []
    for definition in MARKET_OVERVIEW_DEFINITIONS:
        market = str(definition["market"])
        if _normalize_market_code(market) not in market_scope:
            continue
        city = str(definition["city"])
        required_periods = [str(item) for item in definition["required_periods"]]
        requires_samples = bool(definition["requires_samples"])
        required_here = requires_samples
        period_rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        providers: set[str] = set()
        latest_measured_at = ""
        latest_sample = ""
        total_files = 0
        total_routes = 0
        total_geo_routes = 0
        for period in required_periods:
            group = groups.get(_normalize_profile_key(market, city, period))
            row = _period_row(period, group, now=now)
            period_rows.append(row)
            total_files += int(row["sample_file_count"])
            total_routes += int(row["route_sample_count"])
            total_geo_routes += int(row["geo_route_sample_count"])
            providers.update(str(item) for item in row.get("providers", []) if item)
            if str(row.get("latest_measured_at") or "") > latest_measured_at:
                latest_measured_at = str(row.get("latest_measured_at") or "")
                latest_sample = str(row.get("latest_sample") or "")
            if requires_samples and row["status"] == "missing":
                warnings.append(f"missing_{period}")
            age = row.get("age_hours")
            if requires_samples and isinstance(age, (int, float)) and age > stale_after_hours:
                warnings.append(f"stale_{period}")
            if requires_samples and float(row.get("geo_route_sample_ratio") or 0.0) <= 0.0:
                warnings.append(f"no_geo_{period}")
        if not requires_samples:
            warnings.append("static_fallback")
        geo_ratio = (total_geo_routes / total_routes) if total_routes else 0.0
        if required_here and any(item.startswith("missing_") for item in warnings):
            status = "blocked"
        elif warnings:
            status = "warning"
        else:
            status = "healthy"
        markets.append(
            {
                "market": market,
                "city": city,
                "label": definition["label"],
                "status": status,
                "traffic_mode": definition["traffic_mode"],
                "provider": definition["provider"],
                "observed_providers": sorted(providers),
                "active_source": definition["active_source"],
                "fallback_multiplier": definition["fallback_multiplier"],
                "requires_samples": requires_samples,
                "required_in_current_environment": required_here,
                "required_periods": required_periods,
                "sample_file_count": total_files,
                "route_sample_count": total_routes,
                "geo_route_sample_count": total_geo_routes,
                "geo_route_sample_ratio": geo_ratio,
                "latest_measured_at": latest_measured_at,
                "latest_sample": latest_sample,
                "stale_after_hours": stale_after_hours,
                "warnings": sorted(set(warnings)),
                "periods": period_rows,
            }
        )
    blocked_count = sum(1 for row in markets if row.get("status") == "blocked")
    warning_count = sum(1 for row in markets if row.get("status") == "warning")
    return {
        "status": "blocked" if blocked_count else ("warning" if warning_count else "healthy"),
        "sample_dir": str(sample_dir),
        "sample_file_count": summary.get("sample_file_count"),
        "filtered_file_count": summary.get("filtered_file_count"),
        "unreadable_file_count": summary.get("unreadable_file_count"),
        "default_traffic_coefficient_mode": os.environ.get("BRP_DEFAULT_TRAFFIC_COEFFICIENT_MODE", "legacy"),
        "stale_after_hours": stale_after_hours,
        "market_scope": sorted(market_scope),
        "blocked_count": blocked_count,
        "warning_count": warning_count,
        "markets": markets,
    }


def collect_timer_status(
    timer_units: list[str],
    *,
    local_tz: ZoneInfo | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    local_tz = local_tz or _load_timezone(None)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for unit in timer_units:
        try:
            result = _run_command(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "Result",
                    "-p",
                    "NextElapseUSecRealtime",
                    "-p",
                    "LastTriggerUSec",
                    "--no-pager",
                ]
            )
        except Exception as exc:
            rows.append({"unit": unit, "available": False, "error": str(exc)})
            continue
        if result.returncode != 0:
            rows.append(
                {
                    "unit": unit,
                    "available": False,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
            continue
        props = _parse_show_properties(result.stdout)
        next_elapse = props.get("NextElapseUSecRealtime", "")
        last_trigger = props.get("LastTriggerUSec", "")
        next_timestamp = _parse_systemd_utc_timestamp(next_elapse)
        seconds_until = (
            max(0, int(round((next_timestamp - now_utc).total_seconds())))
            if next_timestamp is not None
            else None
        )
        rows.append(
            {
                "unit": unit,
                "available": True,
                "active_state": props.get("ActiveState", ""),
                "sub_state": props.get("SubState", ""),
                "result": props.get("Result", ""),
                "next_elapse": next_elapse,
                "next_elapse_local": _timestamp_local(next_elapse, local_tz),
                "seconds_until_next_elapse": seconds_until,
                "last_trigger": last_trigger,
                "last_trigger_local": _timestamp_local(last_trigger, local_tz),
            }
        )
    return rows


def _status_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def collect_service_status(
    service_units: list[str],
    *,
    local_tz: ZoneInfo | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    local_tz = local_tz or _load_timezone(None)
    for unit in service_units:
        try:
            result = _run_command(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "Result",
                    "-p",
                    "ExecMainStatus",
                    "-p",
                    "ExecMainCode",
                    "-p",
                    "ExecMainStartTimestamp",
                    "-p",
                    "ExecMainExitTimestamp",
                    "--no-pager",
                ]
            )
        except Exception as exc:
            rows.append({"unit": unit, "available": False, "problem": True, "error": str(exc)})
            continue
        if result.returncode != 0:
            rows.append(
                {
                    "unit": unit,
                    "available": False,
                    "problem": True,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
            continue
        props = _parse_show_properties(result.stdout)
        exec_status = _status_int(props.get("ExecMainStatus"))
        result_status = props.get("Result", "")
        active_state = props.get("ActiveState", "")
        exec_main_start = props.get("ExecMainStartTimestamp", "")
        exec_main_exit = props.get("ExecMainExitTimestamp", "")
        problem = bool(
            result_status not in {"", "success"}
            or active_state == "failed"
            or (exec_status is not None and exec_status != 0)
        )
        rows.append(
            {
                "unit": unit,
                "available": True,
                "active_state": active_state,
                "sub_state": props.get("SubState", ""),
                "result": result_status,
                "exec_main_code": props.get("ExecMainCode", ""),
                "exec_main_status": exec_status,
                "exec_main_start": exec_main_start,
                "exec_main_start_local": _timestamp_local(exec_main_start, local_tz),
                "exec_main_exit": exec_main_exit,
                "exec_main_exit_local": _timestamp_local(exec_main_exit, local_tz),
                "problem": problem,
            }
        )
    return rows


def collect_osrm_manager_status() -> dict[str, Any]:
    script = SCRIPT_DIR / "report_osrm_manager.py"
    if not script.exists():
        return {"available": False, "error": f"missing {script}"}
    try:
        result = _run_command([sys.executable, str(script), "status", "--json"], timeout=15)
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if result.returncode != 0:
        return {"available": False, "error": (result.stderr or result.stdout or "").strip()}
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {"available": False, "error": f"invalid json: {exc}"}
    regions = [row for row in payload.get("regions", []) if isinstance(row, dict)]
    locks = [row for row in payload.get("locks", []) if isinstance(row, dict)]
    running_regions = [
        row.get("region")
        for row in regions
        if isinstance(row.get("container_status"), dict) and row["container_status"].get("running")
    ]
    return {
        "available": True,
        "on_demand_enabled": bool(payload.get("on_demand_enabled")),
        "lock_wait_seconds": payload.get("lock_wait_seconds"),
        "max_running_regions": payload.get("max_running_regions"),
        "available_memory_mb": payload.get("available_memory_mb"),
        "lock_count": len(locks),
        "locked_lock_count": sum(1 for row in locks if row.get("locked")),
        "stale_lock_count": sum(1 for row in locks if row.get("stale")),
        "running_region_count": len(running_regions),
        "running_regions": running_regions,
    }


def collect_budget_status() -> dict[str, Any]:
    args = argparse.Namespace(
        include_off_peak=False,
        profile=[],
        max_api_calls_per_run=None,
        sample_due_routes_only=False,
        now_local_time="",
    )
    try:
        report = report_live_traffic_budget.build_report(args)
    except Exception as exc:
        return {"available": False, "problem": True, "error": str(exc)}

    profiles = [row for row in report.get("profiles", []) if isinstance(row, dict)]
    missing_profiles = [str(item) for item in list(report.get("missing_profiles") or [])]
    over_cap_profiles = [row for row in profiles if row.get("status") != "ok"]
    baseline_fast_path_problems = [
        row
        for row in profiles
        if row.get("source") == "baseline_json" and row.get("baseline_fast_path_ready") is not True
    ]
    provider_api_called = bool(report.get("provider_api_called"))
    osrm_started = bool(report.get("osrm_started"))
    safety_violation_reasons = []
    if provider_api_called:
        safety_violation_reasons.append("provider_api_called")
    if osrm_started:
        safety_violation_reasons.append("osrm_started")
    compact_profiles = [
        {
            "profile": row.get("profile"),
            "city": row.get("city"),
            "period": row.get("period"),
            "provider": row.get("provider"),
            "estimated_api_call_count": int(row.get("estimated_api_call_count") or 0),
            "max_api_calls_per_run": int(row.get("max_api_calls_per_run") or 0),
            "provider_refresh_cap": int(row.get("provider_refresh_cap") or 0),
            "baseline_fast_path_ready": row.get("baseline_fast_path_ready"),
            "status": row.get("status"),
            "error": row.get("error", ""),
        }
        for row in profiles
    ]
    return {
        "available": True,
        "problem": bool(
            missing_profiles
            or over_cap_profiles
            or baseline_fast_path_problems
            or safety_violation_reasons
        ),
        "provider_api_called": provider_api_called,
        "osrm_started": osrm_started,
        "safety_violation_reasons": safety_violation_reasons,
        "profile_count": len(profiles),
        "missing_profile_count": len(missing_profiles),
        "missing_profiles": missing_profiles,
        "over_cap_count": len(over_cap_profiles),
        "over_cap_profiles": [row.get("profile") for row in over_cap_profiles],
        "baseline_fast_path_problem_count": len(baseline_fast_path_problems),
        "baseline_fast_path_problem_profiles": [row.get("profile") for row in baseline_fast_path_problems],
        "total_estimated_api_call_count": sum(int(row.get("estimated_api_call_count") or 0) for row in profiles),
        "max_estimated_api_call_count": max(
            [int(row.get("estimated_api_call_count") or 0) for row in profiles],
            default=0,
        ),
        "profiles": compact_profiles,
    }


def summarize_rollout_gate(gate: dict[str, Any]) -> dict[str, Any]:
    readiness = dict(gate.get("readiness") or {})
    requirements = [row for row in readiness.get("requirements", []) if isinstance(row, dict)]
    reason_counts = Counter(str(row.get("reason") or "") for row in requirements if not row.get("passed"))
    passed_count = sum(1 for row in requirements if row.get("passed"))
    missing_profiles = [
        {
            "profile": f"{row.get('market')}:{row.get('city')}:{row.get('period')}",
            "market": row.get("market"),
            "city": row.get("city"),
            "period": row.get("period"),
            "reason": row.get("reason"),
            "route_sample_count": int(row.get("route_sample_count") or 0),
            "geo_route_sample_count": int(row.get("geo_route_sample_count") or 0),
            "latest_excluded_sample": row.get("latest_excluded_sample") or "",
            "latest_excluded_measured_at": row.get("latest_excluded_measured_at") or "",
            "excluded_route_sample_count": int(row.get("excluded_route_sample_count") or 0),
            "excluded_geo_route_sample_count": int(row.get("excluded_geo_route_sample_count") or 0),
        }
        for row in requirements
        if not row.get("passed")
    ]
    return {
        "status": gate.get("status"),
        "passed_requirement_count": passed_count,
        "failed_requirement_count": max(0, len(requirements) - passed_count),
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "missing_profiles": missing_profiles,
        "sample_file_count": readiness.get("sample_file_count"),
        "filtered_file_count": readiness.get("filtered_file_count"),
        "requirements": requirements,
    }


def _next_relevant_timer(timer_status: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        row
        for row in timer_status
        if str(row.get("unit") or "").startswith("brp-live-traffic-")
        and row.get("available")
        and row.get("seconds_until_next_elapse") is not None
    ]
    if not candidates:
        return None
    item = min(candidates, key=lambda row: int(row.get("seconds_until_next_elapse") or 0))
    return {
        "unit": item.get("unit"),
        "next_elapse": item.get("next_elapse"),
        "next_elapse_local": item.get("next_elapse_local"),
        "seconds_until_next_elapse": item.get("seconds_until_next_elapse"),
    }


def _problem_services(service_status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "unit": row.get("unit"),
            "result": row.get("result"),
            "active_state": row.get("active_state"),
            "exec_main_status": row.get("exec_main_status"),
            "exec_main_exit_local": row.get("exec_main_exit_local"),
            "error": row.get("error", ""),
        }
        for row in service_status
        if bool(row.get("problem"))
    ]


def build_status(
    *,
    sample_dir: Path,
    min_measured_at: str,
    profiles: list[tuple[str, str, str]],
    min_geo_ratio: float,
    include_timers: bool,
    include_osrm: bool,
    include_budget: bool = False,
    local_timezone: str = DEFAULT_LOCAL_TIMEZONE,
    now: datetime | None = None,
) -> dict[str, Any]:
    local_tz = _load_timezone(local_timezone)
    gate = report_traffic_rollout_readiness.build_report(
        sample_dir,
        min_measured_at=min_measured_at,
        profiles=profiles,
        min_geo_ratio=min_geo_ratio,
    )
    rollout_summary = summarize_rollout_gate(gate)
    timer_status = (
        collect_timer_status(list(DEFAULT_TIMER_UNITS), local_tz=local_tz, now=now)
        if include_timers
        else []
    )
    service_status = (
        collect_service_status(list(DEFAULT_SERVICE_UNITS), local_tz=local_tz)
        if include_timers
        else []
    )
    osrm_status = collect_osrm_manager_status() if include_osrm else {"available": False, "skipped": True}
    budget_status = collect_budget_status() if include_budget else {"available": False, "skipped": True}
    market_overview = build_market_overview(sample_dir, now=now)
    timer_problem_count = sum(1 for row in timer_status if not row.get("available") or row.get("active_state") == "failed")
    service_problem_count = sum(1 for row in service_status if bool(row.get("problem")))
    osrm_problem = bool(
        (osrm_status.get("available") is False and not osrm_status.get("skipped"))
        or int(osrm_status.get("stale_lock_count") or 0) > 0
    )
    budget_problem = bool(budget_status.get("problem"))
    status = (
        "ready"
        if gate.get("status") == "ok"
        and timer_problem_count == 0
        and service_problem_count == 0
        and not osrm_problem
        and not budget_problem
        else "waiting"
    )
    next_timer = _next_relevant_timer(timer_status)
    next_step = "Run representative route audits and job attribution gates."
    if status != "ready":
        next_step = "Wait for fresh timer samples or fix reported timer/OSRM issues."
        if next_timer:
            next_step = (
                f"Wait for {next_timer.get('unit')} at {next_timer.get('next_elapse_local')} "
                "to collect fresh samples, or fix reported timer/OSRM issues."
            )
    return {
        "status": status,
        "local_timezone": str(local_tz),
        "rollout_gate": rollout_summary,
        "timers": {
            "problem_count": timer_problem_count,
            "next_relevant_timer": next_timer,
            "items": timer_status,
        },
        "services": {
            "problem_count": service_problem_count,
            "problem_services": _problem_services(service_status),
            "items": service_status,
        },
        "api_budget": budget_status,
        "osrm_manager": osrm_status,
        "market_overview": market_overview,
        "next_step": next_step,
    }


def _print_status(report: dict[str, Any]) -> None:
    print(f"status: {report.get('status')}")
    print(f"next_step: {report.get('next_step')}")
    gate = dict(report.get("rollout_gate") or {})
    print(
        "rollout_gate:",
        gate.get("status"),
        f"passed={gate.get('passed_requirement_count')}",
        f"failed={gate.get('failed_requirement_count')}",
        f"reasons={gate.get('failure_reason_counts')}",
    )
    missing_profiles = [row.get("profile") for row in list(gate.get("missing_profiles") or [])]
    if missing_profiles:
        print("missing_profiles:", ", ".join(str(item) for item in missing_profiles))
    timers = dict(report.get("timers") or {})
    print(f"timer_problem_count: {timers.get('problem_count')}")
    next_timer = dict(timers.get("next_relevant_timer") or {})
    if next_timer:
        print(
            "next_relevant_timer:",
            next_timer.get("unit"),
            next_timer.get("next_elapse_local") or next_timer.get("next_elapse"),
            f"in={next_timer.get('seconds_until_next_elapse')}s",
        )
    services = dict(report.get("services") or {})
    print(f"service_problem_count: {services.get('problem_count')}")
    problem_services = [row.get("unit") for row in list(services.get("problem_services") or [])]
    if problem_services:
        print("problem_services:", ", ".join(str(item) for item in problem_services))
    budget = dict(report.get("api_budget") or {})
    if budget.get("skipped"):
        print("api_budget: skipped")
    else:
        print(
            "api_budget:",
            f"available={budget.get('available')}",
            f"problem={budget.get('problem')}",
            f"total_calls={budget.get('total_estimated_api_call_count')}",
            f"max_calls={budget.get('max_estimated_api_call_count')}",
            f"fast_path_problems={budget.get('baseline_fast_path_problem_count')}",
        )
    osrm = dict(report.get("osrm_manager") or {})
    print(
        "osrm_manager:",
        f"available={osrm.get('available')}",
        f"locks={osrm.get('lock_count')}",
        f"running={osrm.get('running_region_count')}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=report_traffic_rollout_readiness.report_live_traffic_readiness.DEFAULT_SAMPLE_DIR,
    )
    parser.add_argument(
        "--min-measured-at",
        default=report_traffic_rollout_readiness.DEFAULT_CUTOFF,
        help="Rollout cutoff passed through to the readiness gate.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        type=report_traffic_rollout_readiness._parse_profile,
        metavar="MARKET:CITY:PERIOD",
        help="Profile to require. Defaults to the current CN Shanghai/Suzhou rollout profiles.",
    )
    parser.add_argument("--min-geo-ratio", type=float, default=1.0)
    parser.add_argument(
        "--local-timezone",
        default=DEFAULT_LOCAL_TIMEZONE,
        help=f"Timezone for local timer/service display. Default: {DEFAULT_LOCAL_TIMEZONE}.",
    )
    parser.add_argument("--no-timers", action="store_true", help="Skip systemd timer status collection.")
    parser.add_argument("--no-osrm", action="store_true", help="Skip OSRM manager status collection.")
    parser.add_argument("--no-budget", action="store_true", help="Skip API budget / baseline fast-path preflight.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_geo_ratio <= 1.0:
        raise SystemExit("--min-geo-ratio must be between 0 and 1")
    profiles = list(args.profile) if args.profile else list(report_traffic_rollout_readiness.DEFAULT_PROFILES)
    report = build_status(
        sample_dir=args.sample_dir,
        min_measured_at=str(args.min_measured_at or "").strip(),
        profiles=profiles,
        min_geo_ratio=float(args.min_geo_ratio),
        include_timers=not args.no_timers,
        include_osrm=not args.no_osrm,
        include_budget=not args.no_budget,
        local_timezone=str(args.local_timezone or DEFAULT_LOCAL_TIMEZONE),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_status(report)
    if report["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
