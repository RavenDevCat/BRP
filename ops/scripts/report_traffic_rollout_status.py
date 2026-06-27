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
import urllib.error
import urllib.parse
import urllib.request
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
DEFAULT_WINDOWS_TIMER_TASKS: tuple[str, ...] = ()

DEFAULT_LOCAL_TIMEZONE = "Asia/Shanghai"
DEFAULT_MARKET_STALE_HOURS = 24 * 7
BANGKOK_STATIC_FALLBACK_MULTIPLIER = 1.75
DEFAULT_REMOTE_STATUS_TIMEOUT_SECONDS = 8.0

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
        "traffic_mode": "final_route_provider",
        "provider": "kakao_navi",
        "active_source": "per-job Kakao Navi future route checks",
        "required_periods": (),
        "fallback_multiplier": None,
        "requires_samples": False,
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

BUDGET_PROFILE_NAMES_BY_MARKET_CITY_PERIOD = {
    ("CN", "Shanghai", "am_peak"): "shanghai_am_peak",
    ("CN", "Shanghai", "pm_peak"): "shanghai_pm_peak",
    ("CN", "Suzhou", "am_peak"): "suzhou_am_peak",
    ("CN", "Suzhou", "pm_peak"): "suzhou_pm_peak",
    ("KR", "Seoul Metro", "am_peak"): "kr_am_peak",
    ("KR", "Seoul Metro", "pm_peak"): "kr_pm_peak",
    ("KR", "Seoul Metro", "off_peak"): "kr_off_peak",
}


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


def _all_market_codes() -> set[str]:
    return {
        _normalize_market_code(definition.get("market"))
        for definition in MARKET_OVERVIEW_DEFINITIONS
    }


def _default_sample_dir() -> Path:
    configured = os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DIR", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt":
        return ROOT_DIR / "state" / "traffic_samples"
    return report_traffic_rollout_readiness.report_live_traffic_readiness.DEFAULT_SAMPLE_DIR


def _deployment_tier() -> str:
    for name in (
        "BRP_DEPLOYMENT_TIER",
        "BRP_DEPLOYMENT_ENV",
        "BRP_ENV",
        "APP_ENV",
        "ENVIRONMENT",
    ):
        value = str(os.environ.get(name, "")).strip().casefold()
        if value:
            if value in {"prod", "production"}:
                return "production"
            if value in {"stage", "staging"}:
                return "staging"
            return value

    try:
        path_parts = {part.casefold() for part in Path(__file__).resolve().parts}
    except Exception:
        path_parts = set()
    if "staging" in path_parts:
        return "staging"
    if "prod" in path_parts or "production" in path_parts:
        return "production"
    if os.name == "nt":
        return "production"
    return "staging"


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
    if _deployment_tier() != "production":
        return _all_market_codes()
    if os.name == "nt":
        return {"KR"}
    return {"CN", "BK"}


def _remote_status_enabled() -> bool:
    raw = os.environ.get("BRP_TRAFFIC_ROLLOUT_REMOTE_STATUS_ENABLED", "").strip()
    if raw:
        return raw.lower() in {"1", "true", "yes", "on"}
    return _deployment_tier() != "production"


def _append_query(url: str, params: dict[str, str]) -> str:
    parts = urllib.parse.urlsplit(url)
    existing = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    existing.update(params)
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(existing))
    )


def _infer_remote_status_urls() -> list[str]:
    configured = os.environ.get("BRP_TRAFFIC_ROLLOUT_REMOTE_STATUS_URLS", "").strip()
    if configured:
        return [
            item.strip()
            for item in configured.replace(";", ",").split(",")
            if item.strip()
        ]
    relay_url = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_URL", "").strip()
    if not relay_url or "KR" not in _market_scope_for_current_environment():
        return []
    parts = urllib.parse.urlsplit(relay_url)
    return [
        urllib.parse.urlunsplit(
            parts._replace(path="/traffic-rollout/status", query="")
        )
    ]


def fetch_remote_rollout_statuses(
    *,
    min_measured_at: str,
    min_geo_ratio: float,
    include_remote: bool,
) -> list[dict[str, Any]]:
    if not include_remote or not _remote_status_enabled():
        return []
    token = os.environ.get("BRP_TRAFFIC_ROLLOUT_REMOTE_STATUS_TOKEN", "").strip()
    if not token:
        token = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TOKEN", "").strip()
    timeout = float(
        os.environ.get(
            "BRP_TRAFFIC_ROLLOUT_REMOTE_STATUS_TIMEOUT_SECONDS",
            str(DEFAULT_REMOTE_STATUS_TIMEOUT_SECONDS),
        )
        or DEFAULT_REMOTE_STATUS_TIMEOUT_SECONDS
    )
    rows: list[dict[str, Any]] = []
    for url in _infer_remote_status_urls():
        request_url = _append_query(
            url,
            {
                "include_timers": "false",
                "include_osrm": "false",
                "include_budget": "false",
                "include_remote": "false",
                "min_measured_at": min_measured_at,
                "min_geo_ratio": str(min_geo_ratio),
            },
        )
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            request = urllib.request.Request(request_url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("remote_status_url", url)
                rows.append(payload)
            else:
                rows.append({"status": "error", "remote_status_url": url, "error": "non-object payload"})
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            rows.append({"status": "error", "remote_status_url": url, "error": str(exc)})
    return rows


def _market_definitions_for_current_environment() -> list[dict[str, Any]]:
    market_scope = _market_scope_for_current_environment()
    return [
        definition
        for definition in MARKET_OVERVIEW_DEFINITIONS
        if _normalize_market_code(definition.get("market")) in market_scope
    ]


def required_profiles_for_current_environment() -> list[tuple[str, str, str]]:
    profiles: list[tuple[str, str, str]] = []
    for definition in _market_definitions_for_current_environment():
        if not bool(definition.get("requires_samples")):
            continue
        market = str(definition["market"])
        city = str(definition["city"])
        profiles.extend((market, city, str(period)) for period in definition["required_periods"])
    return profiles


def budget_profile_names_for_current_environment() -> list[str]:
    profile_names: list[str] = []
    for market, city, period in required_profiles_for_current_environment():
        profile_name = BUDGET_PROFILE_NAMES_BY_MARKET_CITY_PERIOD.get((market, city, period))
        if profile_name:
            profile_names.append(profile_name)
    return profile_names


def _timer_units_for_current_environment() -> list[str]:
    if os.name == "nt":
        return []
    return list(DEFAULT_TIMER_UNITS)


def _service_units_for_current_environment() -> list[str]:
    if os.name == "nt":
        return []
    return list(DEFAULT_SERVICE_UNITS)


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
    deployment_tier = _deployment_tier()
    explicit_market_scope = bool(os.environ.get("BRP_TRAFFIC_STATUS_MARKETS", "").strip())
    samples_required_in_scope = explicit_market_scope or deployment_tier == "production"
    markets: list[dict[str, Any]] = []
    for definition in MARKET_OVERVIEW_DEFINITIONS:
        market = str(definition["market"])
        if _normalize_market_code(market) not in market_scope:
            continue
        city = str(definition["city"])
        required_periods = [str(item) for item in definition["required_periods"]]
        requires_samples = bool(definition["requires_samples"])
        required_here = requires_samples and samples_required_in_scope
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
        if not requires_samples and str(definition["traffic_mode"]) == "static_fallback":
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
        "deployment_tier": deployment_tier,
        "market_scope": sorted(market_scope),
        "blocked_count": blocked_count,
        "warning_count": warning_count,
        "markets": markets,
    }


def _profile_key_from_row(row: dict[str, Any]) -> tuple[str, str, str]:
    return _normalize_profile_key(row.get("market"), row.get("city"), row.get("period"))


def merge_remote_rollout_gate(
    rollout_summary: dict[str, Any],
    remote_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    remote_requirements: dict[tuple[str, str, str], dict[str, Any]] = {}
    remote_sample_count = 0
    remote_filtered_count = 0
    for status in remote_statuses:
        gate = status.get("rollout_gate")
        if not isinstance(gate, dict):
            continue
        remote_sample_count += int(gate.get("sample_file_count") or 0)
        remote_filtered_count += int(gate.get("filtered_file_count") or 0)
        for row in gate.get("requirements") or []:
            if isinstance(row, dict):
                replacement = dict(row)
                replacement["source"] = "remote"
                remote_requirements[_profile_key_from_row(replacement)] = replacement
    if not remote_requirements:
        return rollout_summary

    merged_requirements = []
    for row in rollout_summary.get("requirements") or []:
        if not isinstance(row, dict):
            continue
        merged_requirements.append(remote_requirements.get(_profile_key_from_row(row), row))
    gate = {
        "status": "ok" if all(row.get("passed") for row in merged_requirements) else "failed",
        "readiness": {
            "requirements": merged_requirements,
            "sample_file_count": int(rollout_summary.get("sample_file_count") or 0) + remote_sample_count,
            "filtered_file_count": int(rollout_summary.get("filtered_file_count") or 0) + remote_filtered_count,
        },
    }
    return summarize_rollout_gate(gate)


def merge_remote_market_overview(
    market_overview: dict[str, Any],
    remote_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    remote_markets: dict[tuple[str, str], dict[str, Any]] = {}
    for status in remote_statuses:
        overview = status.get("market_overview")
        if not isinstance(overview, dict):
            continue
        for row in overview.get("markets") or []:
            if isinstance(row, dict):
                key = (_normalize_market_code(row.get("market")), str(row.get("city") or ""))
                replacement = dict(row)
                replacement["source"] = "remote"
                remote_markets[key] = replacement
    if not remote_markets:
        return market_overview

    merged = dict(market_overview)
    markets = []
    for row in market_overview.get("markets") or []:
        if not isinstance(row, dict):
            continue
        key = (_normalize_market_code(row.get("market")), str(row.get("city") or ""))
        markets.append(remote_markets.get(key, row))
    blocked_count = sum(1 for row in markets if row.get("status") == "blocked")
    warning_count = sum(1 for row in markets if row.get("status") == "warning")
    merged.update(
        {
            "status": "blocked" if blocked_count else ("warning" if warning_count else "healthy"),
            "blocked_count": blocked_count,
            "warning_count": warning_count,
            "sample_file_count": sum(int(row.get("sample_file_count") or 0) for row in markets),
            "markets": markets,
        }
    )
    return merged


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


def _parse_windows_task_timestamp(value: Any, local_tz: ZoneInfo, now: datetime | None) -> tuple[str, int | None]:
    raw = str(value or "").strip()
    if not raw or raw.startswith("0001-"):
        return "", None
    parsed = _parse_iso_timestamp(raw)
    if parsed is None:
        return raw, None
    local_value = parsed.astimezone(local_tz)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    seconds_until = max(0, int(round((parsed - now_utc).total_seconds())))
    return local_value.isoformat(timespec="seconds"), seconds_until


def collect_windows_timer_status(
    task_names: list[str],
    *,
    local_tz: ZoneInfo | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    local_tz = local_tz or _load_timezone("Asia/Seoul")
    for task_name in task_names:
        command = (
            "$task = Get-ScheduledTask -TaskName "
            f"'{task_name}' -ErrorAction Stop; "
            "$info = Get-ScheduledTaskInfo -TaskName "
            f"'{task_name}' -ErrorAction Stop; "
            "[pscustomobject]@{"
            "TaskName=$task.TaskName;"
            "State=$task.State.ToString();"
            "LastTaskResult=$info.LastTaskResult;"
            "LastRunTime=$info.LastRunTime.ToString('o');"
            "NextRunTime=$info.NextRunTime.ToString('o')"
            "} | ConvertTo-Json -Compress"
        )
        try:
            result = _run_command(["powershell", "-NoProfile", "-Command", command])
        except Exception as exc:
            rows.append({"unit": task_name, "available": False, "error": str(exc)})
            continue
        if result.returncode != 0:
            rows.append(
                {
                    "unit": task_name,
                    "available": False,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
            continue
        try:
            payload = json.loads(result.stdout)
        except Exception as exc:
            rows.append({"unit": task_name, "available": False, "error": f"invalid json: {exc}"})
            continue
        state = str(payload.get("State") or "")
        last_result = _status_int(payload.get("LastTaskResult"))
        next_local, seconds_until = _parse_windows_task_timestamp(payload.get("NextRunTime"), local_tz, now)
        last_local, _ = _parse_windows_task_timestamp(payload.get("LastRunTime"), local_tz, now)
        rows.append(
            {
                "unit": str(payload.get("TaskName") or task_name),
                "available": True,
                "active_state": state,
                "sub_state": state,
                "result": str(last_result if last_result is not None else ""),
                "next_elapse": str(payload.get("NextRunTime") or ""),
                "next_elapse_local": next_local,
                "seconds_until_next_elapse": seconds_until,
                "last_trigger": str(payload.get("LastRunTime") or ""),
                "last_trigger_local": last_local,
                "problem": last_result not in (None, 0),
            }
        )
    return rows


def collect_environment_timer_status(
    *,
    local_tz: ZoneInfo | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if os.name == "nt":
        return collect_windows_timer_status(
            list(DEFAULT_WINDOWS_TIMER_TASKS),
            local_tz=local_tz,
            now=now,
        )
    return collect_timer_status(_timer_units_for_current_environment(), local_tz=local_tz, now=now)


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


def collect_budget_status(profile_names: list[str] | None = None) -> dict[str, Any]:
    selected_profiles = list(profile_names or [])
    if profile_names is not None and not selected_profiles:
        return {
            "available": True,
            "skipped": True,
            "problem": False,
            "profile_count": 0,
            "missing_profile_count": 0,
            "missing_profiles": [],
            "total_estimated_api_call_count": 0,
            "max_estimated_api_call_count": 0,
            "profiles": [],
        }
    args = argparse.Namespace(
        include_off_peak=True,
        profile=selected_profiles,
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
        if row.get("available")
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
    profiles: list[tuple[str, str, str]] | None,
    min_geo_ratio: float,
    include_timers: bool,
    include_osrm: bool,
    include_budget: bool = False,
    include_remote: bool = True,
    local_timezone: str = DEFAULT_LOCAL_TIMEZONE,
    now: datetime | None = None,
) -> dict[str, Any]:
    local_tz = _load_timezone(local_timezone)
    scoped_profiles = list(profiles or required_profiles_for_current_environment())
    gate = report_traffic_rollout_readiness.build_report(
        sample_dir,
        min_measured_at=min_measured_at,
        profiles=scoped_profiles,
        min_geo_ratio=min_geo_ratio,
    )
    rollout_summary = summarize_rollout_gate(gate)
    remote_statuses = fetch_remote_rollout_statuses(
        min_measured_at=min_measured_at,
        min_geo_ratio=min_geo_ratio,
        include_remote=include_remote,
    )
    rollout_summary = merge_remote_rollout_gate(rollout_summary, remote_statuses)
    timer_status = (
        collect_environment_timer_status(local_tz=local_tz, now=now)
        if include_timers
        else []
    )
    service_status = (
        collect_service_status(_service_units_for_current_environment(), local_tz=local_tz)
        if include_timers
        else []
    )
    osrm_status = collect_osrm_manager_status() if include_osrm else {"available": False, "skipped": True}
    budget_status = (
        collect_budget_status(budget_profile_names_for_current_environment())
        if include_budget
        else {"available": False, "skipped": True}
    )
    market_overview = merge_remote_market_overview(
        build_market_overview(sample_dir, now=now),
        remote_statuses,
    )
    timer_problem_count = sum(
        1
        for row in timer_status
        if not row.get("available") or row.get("active_state") == "failed" or bool(row.get("problem"))
    )
    service_problem_count = sum(1 for row in service_status if bool(row.get("problem")))
    osrm_problem = bool(
        (osrm_status.get("available") is False and not osrm_status.get("skipped"))
        or int(osrm_status.get("stale_lock_count") or 0) > 0
    )
    budget_problem = bool(budget_status.get("problem"))
    status = (
        "ready"
        if rollout_summary.get("status") == "ok"
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
        "environment": {
            "deployment_tier": _deployment_tier(),
            "market_scope": sorted(_market_scope_for_current_environment()),
            "rollout_profiles": [
                f"{market}:{city}:{period}" for market, city, period in scoped_profiles
            ],
            "budget_profiles": budget_profile_names_for_current_environment(),
        },
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
        "remote_statuses": [
            {
                "remote_status_url": row.get("remote_status_url", ""),
                "status": row.get("status", ""),
                "error": row.get("error", ""),
                "market_scope": (row.get("environment") or {}).get("market_scope", []),
            }
            for row in remote_statuses
        ],
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
        default=_default_sample_dir(),
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
        help="Profile to require. Defaults to the current environment market scope.",
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
    parser.add_argument("--no-remote", action="store_true", help="Skip remote rollout status relays.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_geo_ratio <= 1.0:
        raise SystemExit("--min-geo-ratio must be between 0 and 1")
    profiles = list(args.profile) if args.profile else required_profiles_for_current_environment()
    report = build_status(
        sample_dir=args.sample_dir,
        min_measured_at=str(args.min_measured_at or "").strip(),
        profiles=profiles,
        min_geo_ratio=float(args.min_geo_ratio),
        include_timers=not args.no_timers,
        include_osrm=not args.no_osrm,
        include_budget=not args.no_budget,
        include_remote=not args.no_remote,
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
