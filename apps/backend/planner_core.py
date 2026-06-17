from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import importlib.util
import io
import itertools
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urljoin
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
try:
    from .BusingProblem import transpose_matrix
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from BusingProblem import transpose_matrix
import requests


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent
LEGACY_PLANNER_PATH = BASE_DIR / "BusingProblem.py"
OUTPUT_DIR = BASE_DIR / "outputs"
CACHE_DIR = Path(os.environ.get("BRP_BACKEND_CACHE_DIR", str(BASE_DIR / "cache"))).expanduser()
PLANNER_RESULT_CACHE_PATH = CACHE_DIR / "planner_result_cache.json"
ROUTE_METRICS_CACHE_PATH = CACHE_DIR / "route_metrics_cache.json"
ROUTE_GEOMETRY_CACHE_PATH = CACHE_DIR / "route_geometry_cache.json"
TRAFFIC_CALIBRATION_CACHE_PATH = CACHE_DIR / "traffic_calibration_cache.json"


def _default_runtime_path(*parts: str) -> str:
    if os.name == "nt":
        return str(ROOT_DIR / "state" / Path(*parts))
    return str(Path("/opt/brp/shared/runtime", *parts))


LIVE_TRAFFIC_SAMPLE_DIR = Path(
    os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DIR", _default_runtime_path("traffic_samples"))
).expanduser()
LIVE_TRAFFIC_ROLLING_WORKDAYS = max(
    1,
    int(os.environ.get("BRP_LIVE_TRAFFIC_ROLLING_WORKDAYS", "5") or 5),
)
LIVE_TRAFFIC_MAX_AGE_DAYS = max(
    1,
    int(os.environ.get("BRP_LIVE_TRAFFIC_MAX_AGE_DAYS", "14") or 14),
)
LIVE_TRAFFIC_MIN_ROUTE_SAMPLES = max(
    1,
    int(os.environ.get("BRP_LIVE_TRAFFIC_MIN_ROUTE_SAMPLES", "1") or 1),
)
TRAFFIC_COEFFICIENT_MODE_LEGACY = "legacy"
TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED = "attributed"
TRAFFIC_ATTRIBUTION_TOP_K = max(
    1,
    int(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_TOP_K", "5") or 5),
)
TRAFFIC_ATTRIBUTION_GRID_DEGREES = max(
    0.001,
    float(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_GRID_DEGREES", "0.01") or 0.01),
)
TRAFFIC_ATTRIBUTION_CENTER_DECAY_KM = max(
    1.0,
    float(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_CENTER_DECAY_KM", "12") or 12),
)
TRAFFIC_ATTRIBUTION_MIN_SIMILARITY = max(
    0.0,
    float(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_MIN_SIMILARITY", "0.08") or 0.08),
)
TRAFFIC_ATTRIBUTION_MIN_GEO_SIMILARITY = max(
    0.0,
    float(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_MIN_GEO_SIMILARITY", "0.18") or 0.18),
)
TRAFFIC_ATTRIBUTION_MIN_GEO_MATCHES = max(
    1,
    int(os.environ.get("BRP_TRAFFIC_ATTRIBUTION_MIN_GEO_MATCHES", "1") or 1),
)
AMAP_TRAFFIC_CALIBRATION_ENABLED = os.environ.get(
    "BRP_AMAP_TRAFFIC_CALIBRATION_ENABLED",
    "false",
).strip().lower() not in {"0", "false", "no", "off"}
AMAP_TRAFFIC_CALIBRATION_MAX_CALLS = max(
    0,
    int(os.environ.get("BRP_AMAP_TRAFFIC_CALIBRATION_MAX_CALLS", "40") or 0),
)
AMAP_TRAFFIC_CALIBRATION_INTERVAL_SECONDS = max(
    60,
    int(os.environ.get("BRP_AMAP_TRAFFIC_CALIBRATION_INTERVAL_SECONDS", "900") or 900),
)
AMAP_TRAFFIC_CALIBRATION_SAMPLE_COUNT = max(
    1,
    min(48, int(os.environ.get("BRP_AMAP_TRAFFIC_CALIBRATION_SAMPLE_COUNT", "5") or 5)),
)
AMAP_TRAFFIC_CALIBRATION_MIN_FACTOR = max(
    0.1,
    float(os.environ.get("BRP_AMAP_TRAFFIC_CALIBRATION_MIN_FACTOR", "0.8") or 0.8),
)
AMAP_TRAFFIC_CALIBRATION_MAX_FACTOR = max(
    AMAP_TRAFFIC_CALIBRATION_MIN_FACTOR,
    float(os.environ.get("BRP_AMAP_TRAFFIC_CALIBRATION_MAX_FACTOR", "2.8") or 2.8),
)
AMAP_TRAFFIC_CALIBRATION_TZ = ZoneInfo("Asia/Shanghai")
TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES = max(
    0.0,
    float(os.environ.get("BRP_TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES", "15") or 15),
)
INPUT_ADDRESS_REVIEW_SCHOOL_DISTANCE_KM = max(
    0.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_SCHOOL_DISTANCE_KM", "50") or 50),
)
INPUT_ADDRESS_REVIEW_DETOUR_EXTRA_KM = max(
    0.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_DETOUR_EXTRA_KM", "12") or 12),
)
INPUT_ADDRESS_REVIEW_DETOUR_RATIO = max(
    1.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_DETOUR_RATIO", "2.5") or 2.5),
)
INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM = max(
    0.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM", "8") or 8),
)
INPUT_ADDRESS_REVIEW_ORDER_EXTRA_KM = max(
    0.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_ORDER_EXTRA_KM", "5") or 5),
)
INPUT_ADDRESS_REVIEW_ORDER_RATIO = max(
    1.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_ORDER_RATIO", "1.4") or 1.4),
)
INPUT_ADDRESS_REVIEW_ISOLATED_NEIGHBOR_KM = max(
    0.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_ISOLATED_NEIGHBOR_KM", "12") or 12),
)
INPUT_ADDRESS_REVIEW_ISOLATED_ROUTE_MULTIPLIER = max(
    1.0,
    float(os.environ.get("BRP_INPUT_ADDRESS_REVIEW_ISOLATED_ROUTE_MULTIPLIER", "3") or 3),
)
TRAFFIC_PROFILE_MULTIPLIERS: dict[str, float] = {
    "Off-Peak": 1.0,
    "AM Peak": 1.2,
    "PM Peak": 1.3,
}
TRAFFIC_PROFILE_LOCATION_MULTIPLIERS: dict[tuple[str, str], dict[str, float]] = {
    ("CHINA", ""): {
        "Off-Peak": 1.0,
        "AM Peak": 1.38,
        "PM Peak": 1.68,
    },
    ("CHINA", "SHANGHAI"): {
        "Off-Peak": 1.84,
        "AM Peak": 1.6,
        "PM Peak": 1.75,
    },
    ("CHINA", "BEIJING"): {
        "Off-Peak": 1.0,
        "AM Peak": 1.42,
        "PM Peak": 1.72,
    },
    ("CHINA", "SUZHOU"): {
        "Off-Peak": 1.89,
        "AM Peak": 1.32,
        "PM Peak": 1.58,
    },
    ("CHINA", "XIAN"): {
        "Off-Peak": 1.0,
        "AM Peak": 1.30,
        "PM Peak": 1.56,
    },
    ("SOUTH KOREA", "SEOUL"): {
        "Off-Peak": 1.0,
        "AM Peak": 1.2,
        "PM Peak": 1.32,
    },
    ("BANGKOK", ""): {
        "Off-Peak": 1.75,
        "AM Peak": 1.75,
        "PM Peak": 1.75,
    },
    ("BANGKOK", "BANGKOK"): {
        "Off-Peak": 1.75,
        "AM Peak": 1.75,
        "PM Peak": 1.75,
    },
    ("BK", ""): {
        "Off-Peak": 1.75,
        "AM Peak": 1.75,
        "PM Peak": 1.75,
    },
    ("BK", "BANGKOK"): {
        "Off-Peak": 1.75,
        "AM Peak": 1.75,
        "PM Peak": 1.75,
    },
    ("THAILAND", "BANGKOK"): {
        "Off-Peak": 1.75,
        "AM Peak": 1.75,
        "PM Peak": 1.75,
    },
}
ROUTE_DURATION_GRACE_MINUTES = 10


@dataclass
class PlannerConfig:
    large_bus_name: str = "Large Bus"
    mid_bus_name: str = "Mid Bus"
    small_bus_name: str = "Small Bus"
    large_bus_capacity: int = 42
    mid_bus_capacity: int = 35
    small_bus_capacity: int = 19
    large_bus_max_count: int = 20
    mid_bus_max_count: int = 15
    small_bus_max_count: int = 10
    free_baseline_large_bus_ratio: float = 20.0
    free_baseline_mid_bus_ratio: float = 15.0
    free_baseline_small_bus_ratio: float = 10.0
    express_threshold_km: float = 15.0
    reserved_express_buses: int = 4
    express_skip_inner_km: float = 8.0
    max_route_duration_minutes: int = 60
    stop_service_minutes: int = 1
    subway_search_radius_m: int = 1500
    max_subway_walk_distance_m: int = 800
    nearby_cluster_radius_m: int = 500
    comfort_load_factor: float = 1.0
    traffic_profile_name: str = "Off-Peak"
    traffic_coefficient_mode: str = TRAFFIC_COEFFICIENT_MODE_LEGACY
    service_direction: str = "From School"
    to_school_arrival_time: str = "08:00"
    from_school_departure_time: str = "15:40"
    matrix_nearest_neighbors: int = 10
    matrix_candidate_radius_km: float = 15.0
    operating_cost_per_km: float = 0.0
    revenue_rules: list[dict[str, float | None]] | None = None
    original_output_name: str = "school_bus_routes.html"
    current_plan_output_name: str = "current_plan_routes.html"
    subway_output_name: str = "school_bus_routes_subway_aggregated.html"
    nearby_output_name: str = "school_bus_routes_nearby_aggregated.html"
    time_constrained_output_name: str = "school_bus_routes_time_constrained.html"
    further_most_output_name: str = "school_bus_routes_further_most.html"
    further_most_nearby_output_name: str = "school_bus_routes_further_most_nearby.html"
    output_directory_name: str | None = None
    include_subway_aggregation_scenario: bool = True
    include_nearby_aggregation_scenario: bool = True


def _normalize_location_value(value: str | None) -> str:
    return str(value or "").strip().upper()


def _normalize_traffic_city(country: str | None, city: str | None) -> str:
    normalized_country = _normalize_location_value(country)
    normalized_city = _normalize_location_value(city)
    if normalized_country == "SOUTH KOREA" and normalized_city in {
        "SEOUL",
        "SEONGNAM",
        "SEONGNAM-SI",
        "SEONGNAM SI",
        "성남",
        "성남시",
    }:
        return "SEOUL"
    if normalized_country in {"BANGKOK", "BK", "THAILAND"} and normalized_city in {
        "BANGKOK",
        "BANGKOK METROPOLIS",
        "KRUNG THEP",
        "KRUNG THEP MAHA NAKHON",
        "กรุงเทพ",
        "กรุงเทพฯ",
        "กรุงเทพมหานคร",
    }:
        return "BANGKOK"
    return normalized_city


def _build_bus_type_configs(config: PlannerConfig) -> list[dict[str, int | str]]:
    return [
        {"name": str(config.large_bus_name).strip() or "Large Bus", "capacity": int(config.large_bus_capacity), "max_count": int(config.large_bus_max_count)},
        {"name": str(config.mid_bus_name).strip() or "Mid Bus", "capacity": int(config.mid_bus_capacity), "max_count": int(config.mid_bus_max_count)},
        {"name": str(config.small_bus_name).strip() or "Small Bus", "capacity": int(config.small_bus_capacity), "max_count": int(config.small_bus_max_count)},
    ]


def _build_slot_policy_maps(config: PlannerConfig) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    bus_type_configs = _build_bus_type_configs(config)
    fixed_cost_by_name = {
        str(bus_type_configs[0]["name"]): 0,
        str(bus_type_configs[1]["name"]): 1200,
        str(bus_type_configs[2]["name"]): 2600,
    }
    min_load_target_by_name = {
        str(item["name"]): max(0, int(round(int(item["capacity"]) * 0.5)))
        for item in bus_type_configs
    }
    min_load_penalty_by_name = {
        str(bus_type_configs[0]["name"]): 140,
        str(bus_type_configs[1]["name"]): 100,
        str(bus_type_configs[2]["name"]): 40,
    }
    return fixed_cost_by_name, min_load_target_by_name, min_load_penalty_by_name


def effective_route_duration_limit_minutes(config: PlannerConfig) -> int:
    return max(1, int(config.max_route_duration_minutes) + ROUTE_DURATION_GRACE_MINUTES)


def normalize_service_direction(service_direction: str | None) -> str:
    return "To School" if str(service_direction or "").strip() == "To School" else "From School"


def transpose_matrix(matrix: list[list[float]] | list[list[int]]) -> list[list[float]]:
    return [list(row) for row in zip(*matrix)] if matrix else []


def route_terminal_index(route_rows: list[dict[str, Any]], service_direction: str | None) -> int:
    if not route_rows:
        return 0
    return len(route_rows) - 1 if normalize_service_direction(service_direction) == "To School" else 0


def split_route_terminal_rows(
    route_rows: list[dict[str, Any]],
    service_direction: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not route_rows:
        return {}, []
    terminal_idx = route_terminal_index(route_rows, service_direction)
    terminal_row = dict(route_rows[terminal_idx])
    service_rows = [dict(row) for idx, row in enumerate(route_rows) if idx != terminal_idx]
    return terminal_row, service_rows


def order_points_for_service_direction(
    matched_points: list[dict[str, Any]],
    service_direction: str | None,
    *,
    optimized: bool = False,
) -> list[dict[str, Any]]:
    if normalize_service_direction(service_direction) != "To School":
        return list(matched_points)
    if not matched_points:
        return []
    if optimized:
        # optimized_points 在 To School 时来自 transpose 矩阵的求解结果，
        # 其第一个点是 depot。真实世界行驶顺序是其逆序。
        return list(reversed(matched_points))
    # 非优化时，matched_points 已经是真实世界顺序。
    # 如果第一个点是 depot（From School 格式），把它移到最后；
    # 否则保持原样（已经是 To School 格式：[..., depot]）。
    if bool(matched_points[0].get("is_depot")):
        return [*matched_points[1:], matched_points[0]]
    return list(matched_points)


def _allocate_vehicle_counts_from_ratio(total_vehicle_count: int, weights: list[float]) -> list[int]:
    if total_vehicle_count <= 0 or not weights:
        return [0 for _ in weights]
    positive_weights = [max(0.0, float(weight)) for weight in weights]
    weight_sum = sum(positive_weights)
    if weight_sum <= 0:
        return [0 for _ in weights]

    raw_allocations = [
        float(total_vehicle_count) * float(weight) / weight_sum
        for weight in positive_weights
    ]
    counts = [int(allocation) for allocation in raw_allocations]
    remaining = total_vehicle_count - sum(counts)
    remainders = sorted(
        (
            (raw_allocations[index] - counts[index], index)
            for index in range(len(raw_allocations))
        ),
        reverse=True,
    )
    for _, index in remainders[:remaining]:
        counts[index] += 1
    return counts


def build_free_baseline_bus_type_configs(config: PlannerConfig) -> list[dict[str, int | str]]:
    default_configs = _build_bus_type_configs(config)
    total_vehicle_count = sum(int(item["max_count"]) for item in default_configs)
    ratio_weights = [
        float(config.free_baseline_large_bus_ratio),
        float(config.free_baseline_mid_bus_ratio),
        float(config.free_baseline_small_bus_ratio),
    ]
    if total_vehicle_count <= 0 or sum(max(0.0, weight) for weight in ratio_weights) <= 0:
        return default_configs

    allocated_counts = _allocate_vehicle_counts_from_ratio(total_vehicle_count, ratio_weights)
    return [
        {
            "name": str(item["name"]),
            "capacity": int(item["capacity"]),
            "max_count": int(allocated_count),
        }
        for item, allocated_count in zip(default_configs, allocated_counts)
    ]


def infer_traffic_location(input_records: list[dict[str, Any]] | None) -> tuple[str, str]:
    records = list(input_records or [])
    if not records:
        return "", ""
    countries = {
        _normalize_location_value(item.get("country"))
        for item in records
        if _normalize_location_value(item.get("country"))
    }
    cities = {
        _normalize_traffic_city(item.get("country"), item.get("city"))
        for item in records
        if _normalize_traffic_city(item.get("country"), item.get("city"))
    }
    if len(countries) == 1 and len(cities) == 1:
        return next(iter(countries)), next(iter(cities))
    if len(countries) == 1:
        return next(iter(countries)), ""
    return "", ""


def resolve_traffic_profile(
    profile_name: str | None,
    input_records: list[dict[str, Any]] | None = None,
) -> tuple[str, float, str]:
    normalized = str(profile_name or "").strip()
    if normalized not in TRAFFIC_PROFILE_MULTIPLIERS:
        normalized = "Off-Peak"
    country, city = infer_traffic_location(input_records)
    location_key = (country, city)
    location_profiles = TRAFFIC_PROFILE_LOCATION_MULTIPLIERS.get(location_key)
    if location_profiles is not None:
        return normalized, float(location_profiles[normalized]), f"{city.title()} default"
    country_profiles = TRAFFIC_PROFILE_LOCATION_MULTIPLIERS.get((country, ""))
    if country_profiles is not None:
        return normalized, float(country_profiles[normalized]), f"{country.title()} default"
    return normalized, float(TRAFFIC_PROFILE_MULTIPLIERS[normalized]), "Global default"


def normalize_traffic_coefficient_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"attributed", "attribution", "route_attributed", "similarity"}:
        return TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED
    return TRAFFIC_COEFFICIENT_MODE_LEGACY


def live_traffic_period_for_service_direction(service_direction: str | None) -> str | None:
    normalized = normalize_service_direction(service_direction)
    if normalized == "To School":
        return "am_peak"
    if normalized == "From School":
        return "pm_peak"
    return None


def _traffic_timezone(country: str) -> ZoneInfo:
    if country == "SOUTH KOREA":
        return ZoneInfo("Asia/Seoul")
    return AMAP_TRAFFIC_CALIBRATION_TZ


def _traffic_weekday_label(value: datetime) -> str:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][value.weekday()]


def _parse_live_sample_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=AMAP_TRAFFIC_CALIBRATION_TZ)
    return parsed.astimezone(AMAP_TRAFFIC_CALIBRATION_TZ)


def _load_live_traffic_sample(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _matching_live_traffic_samples(
    *,
    country: str,
    city: str,
    period: str,
    sample_dir: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if country not in {"CHINA", "SOUTH KOREA"} or not city:
        return []
    directory = sample_dir or LIVE_TRAFFIC_SAMPLE_DIR
    if not directory.exists():
        return []
    tz = _traffic_timezone(country)
    now = (now or datetime.now(tz)).astimezone(tz)
    cutoff = now - timedelta(days=LIVE_TRAFFIC_MAX_AGE_DAYS)
    expected_weekday = _traffic_weekday_label(now)
    matches: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        payload = _load_live_traffic_sample(path)
        if not payload:
            continue
        if bool(payload.get("dry_run")):
            continue
        if str(payload.get("period", "")).strip() != period:
            continue
        if str(payload.get("city", "")).strip().upper() != city:
            continue
        payload_country = _normalize_location_value(str(payload.get("country") or ""))
        if payload_country and payload_country != country:
            continue
        sample_weekday = str(payload.get("sample_weekday") or "").strip().lower()
        if (
            country == "SOUTH KOREA"
            and expected_weekday in {"mon", "tue", "wed", "thu", "fri"}
            and sample_weekday
            and sample_weekday != expected_weekday
        ):
            continue
        measured_at = _parse_live_sample_datetime(payload.get("measured_at"))
        if measured_at is not None:
            measured_at = measured_at.astimezone(tz)
        if measured_at is None or measured_at < cutoff:
            continue
        route_count = int(payload.get("route_count", 0) or 0)
        total_osrm = float(payload.get("total_osrm_duration_s", 0.0) or 0.0)
        total_api = payload.get("total_api_duration_s")
        if total_api is None:
            total_api = payload.get("total_amap_duration_s")
        total_api_duration = float(total_api or 0.0)
        if route_count <= 0 or total_osrm <= 0 or total_api_duration <= 0:
            continue
        item = dict(payload)
        item["_path"] = str(path)
        item["_measured_at"] = measured_at
        item["_local_date"] = str(payload.get("local_date") or payload.get("sample_date") or measured_at.date().isoformat())
        item["_total_api_duration_s"] = total_api_duration
        matches.append(item)
    matches.sort(key=lambda item: item["_measured_at"], reverse=True)
    return matches


def _select_live_traffic_sample_window(matches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    selected_dates: list[str] = []
    selected: list[dict[str, Any]] = []
    for item in matches:
        local_date = str(item["_local_date"])
        if local_date not in selected_dates:
            if len(selected_dates) >= LIVE_TRAFFIC_ROLLING_WORKDAYS:
                continue
            selected_dates.append(local_date)
        if local_date in selected_dates:
            selected.append(item)
    return selected, selected_dates


def summarize_live_traffic_samples(
    *,
    service_direction: str | None,
    input_records: list[dict[str, Any]] | None,
    sample_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    country, city = infer_traffic_location(input_records)
    period = live_traffic_period_for_service_direction(service_direction)
    if period is None:
        return None
    matches = _matching_live_traffic_samples(
        country=country,
        city=city,
        period=period,
        sample_dir=sample_dir,
        now=now,
    )
    if not matches:
        return None

    selected, selected_dates = _select_live_traffic_sample_window(matches)

    total_osrm = sum(float(item.get("total_osrm_duration_s", 0.0) or 0.0) for item in selected)
    total_api = sum(float(item.get("_total_api_duration_s", item.get("total_api_duration_s", 0.0)) or 0.0) for item in selected)
    route_count = sum(int(item.get("route_count", 0) or 0) for item in selected)
    if total_osrm <= 0 or total_api <= 0 or route_count < LIVE_TRAFFIC_MIN_ROUTE_SAMPLES:
        return None
    factor = total_api / total_osrm
    latest = selected[0]
    label = "AM Peak" if period == "am_peak" else "PM Peak"
    providers = sorted({str(item.get("provider") or "").strip() for item in selected if str(item.get("provider") or "").strip()})
    return {
        "enabled": True,
        "succeeded": True,
        "source": "live_traffic_samples",
        "country": country,
        "city": city,
        "period": period,
        "period_label": label,
        "traffic_profile_name": f"{label} (Live)",
        "traffic_time_multiplier": float(factor),
        "traffic_profile_context": (
            f"{city.title()} {label} live traffic sample; "
            f"{route_count} route sample(s), {len(selected_dates)} workday(s), "
            f"provider {', '.join(providers) if providers else 'traffic API'}, "
            f"latest {latest['_measured_at'].strftime('%Y-%m-%d %H:%M')}"
        ),
        "route_sample_count": route_count,
        "sample_file_count": len(selected),
        "workday_count": len(selected_dates),
        "local_dates": selected_dates,
        "latest_measured_at": latest["_measured_at"].isoformat(timespec="seconds"),
        "total_osrm_duration_s": total_osrm,
        "total_api_duration_s": total_api,
        "providers": providers,
        "sample_weekday": str(latest.get("sample_weekday") or ""),
        "sample_paths": [str(item.get("_path", "")) for item in selected],
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def _save_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _next_local_timestamp(hour: int, minute: int = 0) -> int:
    now = datetime.now(AMAP_TRAFFIC_CALIBRATION_TZ)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def peak_traffic_periods() -> list[dict[str, Any]]:
    return [
        {
            "key": "am_peak",
            "label": "AM Peak",
            "firsttime": _next_local_timestamp(8, 0),
            "interval": AMAP_TRAFFIC_CALIBRATION_INTERVAL_SECONDS,
            "count": AMAP_TRAFFIC_CALIBRATION_SAMPLE_COUNT,
        },
        {
            "key": "pm_peak",
            "label": "PM Peak",
            "firsttime": _next_local_timestamp(15, 30),
            "interval": AMAP_TRAFFIC_CALIBRATION_INTERVAL_SECONDS,
            "count": AMAP_TRAFFIC_CALIBRATION_SAMPLE_COUNT,
        },
    ]


def _point_lng_lat(point: dict[str, Any]) -> tuple[float, float]:
    return float(point.get("lng", 0.0) or 0.0), float(point.get("lat", 0.0) or 0.0)


def _traffic_cache_key(
    origin: dict[str, Any],
    destination: dict[str, Any],
    period: dict[str, Any],
) -> str:
    origin_lng, origin_lat = _point_lng_lat(origin)
    destination_lng, destination_lat = _point_lng_lat(destination)
    payload = {
        "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
        "destination": f"{destination_lng:.6f},{destination_lat:.6f}",
        "firsttime": int(period["firsttime"]),
        "interval": int(period["interval"]),
        "count": int(period["count"]),
        "strategy": 1,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _parse_amap_future_duration_seconds(payload: dict[str, Any]) -> list[float]:
    durations: list[float] = []
    for path in list(((payload.get("data") or {}).get("paths")) or []):
        for time_info in list(path.get("time_infos") or []):
            elements = list(time_info.get("elements") or [])
            if not elements and time_info.get("duration") not in (None, ""):
                elements = [time_info]
            for element in elements:
                raw_duration = element.get("duration")
                if raw_duration in (None, ""):
                    continue
                try:
                    # AMap advanced path returns duration in minutes.
                    duration_seconds = float(raw_duration) * 60.0
                except (TypeError, ValueError):
                    continue
                if duration_seconds > 0:
                    durations.append(duration_seconds)
    return durations


def amap_future_driving_duration_seconds(
    planner: Any,
    origin: dict[str, Any],
    destination: dict[str, Any],
    period: dict[str, Any],
    cache: dict[str, Any],
) -> tuple[list[float], bool]:
    cache_key = _traffic_cache_key(origin, destination, period)
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        durations = []
        for item in list(cached.get("durations_s") or []):
            try:
                duration = float(item)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                durations.append(duration)
        if durations:
            return durations, True

    origin_lng, origin_lat = _point_lng_lat(origin)
    destination_lng, destination_lat = _point_lng_lat(destination)
    limiter = getattr(planner, "AMAP_ROUTING_LIMITER", None)
    if limiter is not None:
        limiter.wait()
    response = requests.get(
        "https://restapi.amap.com/v4/etd/driving",
        params={
            "key": getattr(planner, "AMAP_KEY", ""),
            "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
            "destination": f"{destination_lng:.6f},{destination_lat:.6f}",
            "strategy": "1",
            "firsttime": str(int(period["firsttime"])),
            "interval": str(int(period["interval"])),
            "count": str(int(period["count"])),
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("errcode", -1) or -1) != 0:
        raise RuntimeError(str(payload.get("errmsg") or payload.get("errdetail") or "AMap future driving failed"))
    durations = _parse_amap_future_duration_seconds(payload)
    if not durations:
        raise RuntimeError("AMap future driving returned no usable durations.")
    cache[cache_key] = {
        "durations_s": durations,
        "period": str(period["key"]),
        "firsttime": int(period["firsttime"]),
        "interval": int(period["interval"]),
        "count": int(period["count"]),
        "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
        "destination": f"{destination_lng:.6f},{destination_lat:.6f}",
    }
    return durations, False


def _current_plan_matched_route_points(
    current_plan: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    service_direction: str,
) -> dict[str, list[dict[str, Any]]]:
    normalized = _normalize_current_plan(current_plan)
    if not normalized:
        return {}
    point_lookup = _make_point_lookup(prepared_original_points)
    stop_lookup = {
        str(item.get("stop_id", "")).strip(): dict(item)
        for item in list(normalized.get("stops") or [])
    }
    route_groups: dict[str, list[dict[str, Any]]] = {}
    for assignment in list(normalized.get("assignments") or []):
        route_id = str(assignment.get("route_id", "")).strip()
        stop_id = str(assignment.get("stop_id", "")).strip()
        if not route_id or not stop_id or stop_id not in stop_lookup:
            continue
        row = {
            "stop_sequence": int(assignment.get("stop_sequence", 0) or 0),
            **dict(stop_lookup[stop_id]),
        }
        route_groups.setdefault(route_id, []).append(row)

    matched: dict[str, list[dict[str, Any]]] = {}
    for route_id, rows in sorted(route_groups.items()):
        ordered_rows = sorted(rows, key=lambda item: int(item.get("stop_sequence", 0) or 0))
        points: list[dict[str, Any]] = []
        for row in ordered_rows:
            key = (
                str(row.get("country", "")).strip().lower(),
                str(row.get("city", "")).strip().lower(),
                str(row.get("address", "")).strip().lower(),
            )
            point = point_lookup.get(key)
            if point is not None:
                points.append(point)
        ordered_points = order_points_for_service_direction(points, service_direction, optimized=False)
        if len(ordered_points) >= 2:
            matched[route_id] = ordered_points
    return matched


def _sample_current_plan_edges(
    route_points_by_id: dict[str, list[dict[str, Any]]],
    max_edges: int,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    if max_edges <= 0:
        return []
    all_edges: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen: set[tuple[int, int]] = set()
    for route_id, points in sorted(route_points_by_id.items()):
        for origin, destination in zip(points[:-1], points[1:]):
            key = (int(origin.get("node_id", -1)), int(destination.get("node_id", -1)))
            if key in seen or key[0] < 0 or key[1] < 0 or key[0] == key[1]:
                continue
            seen.add(key)
            all_edges.append((route_id, origin, destination))
    if len(all_edges) <= max_edges:
        return all_edges
    step = len(all_edges) / float(max_edges)
    sampled = [all_edges[int(index * step)] for index in range(max_edges)]
    deduped: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    sampled_keys: set[tuple[int, int]] = set()
    for item in sampled:
        key = (int(item[1].get("node_id", -1)), int(item[2].get("node_id", -1)))
        if key not in sampled_keys:
            deduped.append(item)
            sampled_keys.add(key)
    return deduped


def _weighted_average_factor(samples: list[dict[str, float]]) -> float | None:
    total_weight = sum(float(item.get("weight_s", 0.0) or 0.0) for item in samples)
    if total_weight <= 0:
        return None
    weighted = sum(float(item["factor"]) * float(item["weight_s"]) for item in samples)
    return weighted / total_weight


def _summarize_route_period_stats(
    route_samples: dict[str, dict[str, dict[str, float]]],
    route_edge_counts: dict[str, int],
) -> dict[str, dict[str, dict[str, Any]]]:
    route_periods: dict[str, dict[str, dict[str, Any]]] = {}
    for period_key, routes in route_samples.items():
        route_periods[period_key] = {}
        for route_id, stats in routes.items():
            osrm_duration_s = float(stats.get("osrm_duration_s", 0.0) or 0.0)
            amap_duration_s = float(stats.get("amap_duration_s", 0.0) or 0.0)
            sampled_leg_count = int(stats.get("sampled_leg_count", 0.0) or 0)
            total_leg_count = int(route_edge_counts.get(route_id, sampled_leg_count))
            factor = (amap_duration_s / osrm_duration_s) if osrm_duration_s > 0 else None
            route_periods[period_key][route_id] = {
                "osrm_duration_s": osrm_duration_s,
                "amap_duration_s": amap_duration_s,
                "factor": float(factor) if factor is not None else None,
                "sample_count": int(stats.get("sample_count", 0.0) or 0),
                "sampled_leg_count": sampled_leg_count,
                "total_leg_count": total_leg_count,
                "coverage_complete": sampled_leg_count >= total_leg_count and total_leg_count > 0,
            }
    return route_periods


def _selected_route_traffic_stats(
    traffic_calibration: dict[str, Any] | None,
    route_id: str,
) -> dict[str, Any] | None:
    calibration = dict(traffic_calibration or {})
    if not calibration.get("succeeded"):
        return None
    selected_period = str(calibration.get("selected_period", "")).strip()
    route_periods = dict(calibration.get("route_periods") or {})
    selected_routes = dict(route_periods.get(selected_period) or {})
    stats = selected_routes.get(str(route_id).strip())
    return dict(stats) if isinstance(stats, dict) else None


def _route_sample_factor(route: dict[str, Any]) -> float | None:
    try:
        factor = float(route.get("factor"))
    except (TypeError, ValueError):
        factor = 0.0
    if factor > 0:
        return factor
    osrm_duration_s = float(route.get("osrm_duration_s", 0.0) or 0.0)
    api_duration_s = route.get("api_duration_s")
    if api_duration_s is None:
        api_duration_s = route.get("amap_duration_s")
    api_duration_s = float(api_duration_s or 0.0)
    if osrm_duration_s <= 0 or api_duration_s <= 0:
        return None
    return api_duration_s / osrm_duration_s


def _traffic_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _traffic_point_coordinates(point: dict[str, Any]) -> tuple[float, float] | None:
    lat = _traffic_float(point.get("plot_lat"))
    lng = _traffic_float(point.get("plot_lng"))
    if lat is None or lng is None:
        lat = _traffic_float(point.get("lat"))
        lng = _traffic_float(point.get("lng"))
    if lat is None or lng is None:
        return None
    if abs(lat) > 90 or abs(lng) > 180:
        return None
    return lat, lng


def _traffic_geometry_coordinates(geometry: Any) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for item in list(geometry or []):
        lat = lng = None
        if isinstance(item, dict):
            lat = _traffic_float(item.get("lat") or item.get("latitude"))
            lng = _traffic_float(item.get("lng") or item.get("lon") or item.get("longitude"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            lat = _traffic_float(item[0])
            lng = _traffic_float(item[1])
        if lat is None or lng is None or abs(lat) > 90 or abs(lng) > 180:
            continue
        coords.append((lat, lng))
    return coords


def _traffic_grid_cell(lat: float, lng: float, grid_degrees: float = TRAFFIC_ATTRIBUTION_GRID_DEGREES) -> str:
    return f"{math.floor(lat / grid_degrees)}:{math.floor(lng / grid_degrees)}"


def _traffic_haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2.0) ** 2
    return 6371.0088 * 2.0 * math.asin(min(1.0, math.sqrt(h)))


def _traffic_bearing_sector(origin: tuple[float, float], destination: tuple[float, float], sectors: int = 16) -> int | None:
    if origin == destination:
        return None
    lat1 = math.radians(origin[0])
    lat2 = math.radians(destination[0])
    dlng = math.radians(destination[1] - origin[1])
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return int(round(bearing / (360.0 / sectors))) % sectors


def _traffic_bbox(coords: list[tuple[float, float]]) -> dict[str, float] | None:
    if not coords:
        return None
    lats = [lat for lat, _lng in coords]
    lngs = [lng for _lat, lng in coords]
    return {
        "min_lat": round(min(lats), 6),
        "max_lat": round(max(lats), 6),
        "min_lng": round(min(lngs), 6),
        "max_lng": round(max(lngs), 6),
    }


def _traffic_bbox_overlap_score(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float:
    if not a or not b:
        return 0.0
    min_lat = max(float(a.get("min_lat", 0.0) or 0.0), float(b.get("min_lat", 0.0) or 0.0))
    max_lat = min(float(a.get("max_lat", 0.0) or 0.0), float(b.get("max_lat", 0.0) or 0.0))
    min_lng = max(float(a.get("min_lng", 0.0) or 0.0), float(b.get("min_lng", 0.0) or 0.0))
    max_lng = min(float(a.get("max_lng", 0.0) or 0.0), float(b.get("max_lng", 0.0) or 0.0))
    intersection = max(0.0, max_lat - min_lat) * max(0.0, max_lng - min_lng)
    area_a = max(0.0, float(a.get("max_lat", 0.0) or 0.0) - float(a.get("min_lat", 0.0) or 0.0)) * max(
        0.0,
        float(a.get("max_lng", 0.0) or 0.0) - float(a.get("min_lng", 0.0) or 0.0),
    )
    area_b = max(0.0, float(b.get("max_lat", 0.0) or 0.0) - float(b.get("min_lat", 0.0) or 0.0)) * max(
        0.0,
        float(b.get("max_lng", 0.0) or 0.0) - float(b.get("min_lng", 0.0) or 0.0),
    )
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _traffic_sector_similarity(a: Any, b: Any, sectors: int = 16) -> float:
    if a is None or b is None:
        return 0.0
    try:
        sector_a = int(a)
        sector_b = int(b)
    except (TypeError, ValueError):
        return 0.0
    gap = abs(sector_a - sector_b) % sectors
    gap = min(gap, sectors - gap)
    return max(0.0, 1.0 - gap / (sectors / 2.0))


def build_route_traffic_fingerprint(
    route: dict[str, Any] | None = None,
    *,
    all_points: list[dict[str, Any]] | None = None,
    route_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    route = dict(route or {})
    if route_points is None and all_points is not None:
        route_points = []
        for node_id in list(route.get("nodes") or []):
            try:
                route_points.append(dict(all_points[int(node_id)]))
            except (IndexError, TypeError, ValueError):
                continue
    route_points = [dict(point) for point in list(route_points or [])]
    point_coord_pairs = [
        (point, coord)
        for point in route_points
        if (coord := _traffic_point_coordinates(point)) is not None
    ]
    stop_coords = [coord for _point, coord in point_coord_pairs]
    geometry_coords: list[tuple[float, float]] = []
    for leg in list(route.get("leg_details") or []):
        geometry_coords.extend(_traffic_geometry_coordinates(leg.get("geometry")))
    corridor_coords = geometry_coords or stop_coords
    if len(corridor_coords) < 2 and len(stop_coords) < 2:
        return None
    center_coords = corridor_coords or stop_coords
    center_lat = sum(lat for lat, _lng in center_coords) / len(center_coords)
    center_lng = sum(lng for _lat, lng in center_coords) / len(center_coords)
    start = stop_coords[0] if stop_coords else center_coords[0]
    end = stop_coords[-1] if stop_coords else center_coords[-1]
    school_coord = next(
        (
            coord
            for point, coord in point_coord_pairs
            if bool(point.get("is_depot") or point.get("is_school"))
        ),
        None,
    )
    if school_coord is None and stop_coords:
        school_coord = stop_coords[0]
    corridor_cells = sorted({_traffic_grid_cell(lat, lng) for lat, lng in corridor_coords})
    stop_cells = sorted({_traffic_grid_cell(lat, lng) for lat, lng in stop_coords})
    cells = sorted(set(corridor_cells) | set(stop_cells))
    return {
        "schema_version": 1,
        "grid_degrees": TRAFFIC_ATTRIBUTION_GRID_DEGREES,
        "cell_count": len(cells),
        "cells": cells[:500],
        "corridor_cells": corridor_cells[:500],
        "stop_cells": stop_cells[:200],
        "point_count": len(stop_coords),
        "geometry_point_count": len(geometry_coords),
        "center": {"lat": round(center_lat, 6), "lng": round(center_lng, 6)},
        "start": {"lat": round(start[0], 6), "lng": round(start[1], 6)},
        "end": {"lat": round(end[0], 6), "lng": round(end[1], 6)},
        "bbox": _traffic_bbox(center_coords),
        "bearing_sector": _traffic_bearing_sector(start, end),
        "school_bearing_sector": _traffic_bearing_sector(school_coord, (center_lat, center_lng)) if school_coord else None,
    }


def _route_traffic_fingerprint(route: dict[str, Any]) -> dict[str, Any]:
    fingerprint = route.get("route_fingerprint")
    if not isinstance(fingerprint, dict):
        fingerprint = route.get("traffic_fingerprint")
    return dict(fingerprint) if isinstance(fingerprint, dict) else {}


def _traffic_center_tuple(fingerprint: dict[str, Any]) -> tuple[float, float] | None:
    center = dict(fingerprint.get("center") or {})
    lat = _traffic_float(center.get("lat"))
    lng = _traffic_float(center.get("lng"))
    if lat is None or lng is None:
        return None
    return lat, lng


def _traffic_cell_jaccard(a: list[Any], b: list[Any]) -> float:
    set_a = {str(item) for item in a if str(item)}
    set_b = {str(item) for item in b if str(item)}
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _traffic_fingerprint_similarity(
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, float] | None:
    target_fp = _route_traffic_fingerprint(target)
    candidate_fp = _route_traffic_fingerprint(candidate)
    if not target_fp or not candidate_fp:
        return None
    corridor_overlap = _traffic_cell_jaccard(
        list(target_fp.get("corridor_cells") or target_fp.get("cells") or []),
        list(candidate_fp.get("corridor_cells") or candidate_fp.get("cells") or []),
    )
    stop_overlap = _traffic_cell_jaccard(list(target_fp.get("stop_cells") or []), list(candidate_fp.get("stop_cells") or []))
    target_center = _traffic_center_tuple(target_fp)
    candidate_center = _traffic_center_tuple(candidate_fp)
    center_distance_km = (
        _traffic_haversine_km(target_center, candidate_center)
        if target_center is not None and candidate_center is not None
        else None
    )
    center_score = (
        1.0 / (1.0 + float(center_distance_km or 0.0) / TRAFFIC_ATTRIBUTION_CENTER_DECAY_KM)
        if center_distance_km is not None
        else 0.0
    )
    bbox_score = _traffic_bbox_overlap_score(
        dict(target_fp.get("bbox") or {}),
        dict(candidate_fp.get("bbox") or {}),
    )
    bearing_score = max(
        _traffic_sector_similarity(target_fp.get("bearing_sector"), candidate_fp.get("bearing_sector")),
        _traffic_sector_similarity(target_fp.get("school_bearing_sector"), candidate_fp.get("school_bearing_sector")),
    )
    score = (
        0.45 * corridor_overlap
        + 0.15 * stop_overlap
        + 0.25 * center_score
        + 0.10 * bearing_score
        + 0.05 * bbox_score
    )
    return {
        "geo_score": max(0.001, float(score)),
        "corridor_overlap": float(corridor_overlap),
        "stop_overlap": float(stop_overlap),
        "center_distance_km": float(center_distance_km) if center_distance_km is not None else -1.0,
        "center_score": float(center_score),
        "bearing_score": float(bearing_score),
        "bbox_score": float(bbox_score),
    }


def _live_route_attribution_candidates(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for sample in samples:
        measured_at = sample.get("_measured_at")
        for route in list(sample.get("routes") or []):
            route = dict(route or {})
            factor = _route_sample_factor(route)
            osrm_duration_s = float(route.get("osrm_duration_s", 0.0) or 0.0)
            if factor is None or osrm_duration_s <= 0:
                continue
            candidates.append(
                {
                    "route_id": str(route.get("route_id") or "").strip(),
                    "source_id": str(route.get("source_id") or sample.get("source_id") or "").strip(),
                    "sample_path": str(sample.get("_path") or ""),
                    "provider": str(route.get("provider") or sample.get("provider") or "").strip(),
                    "measured_at": measured_at.isoformat(timespec="seconds") if isinstance(measured_at, datetime) else "",
                    "osrm_duration_s": osrm_duration_s,
                    "api_duration_s": float(route.get("api_duration_s") or route.get("amap_duration_s") or 0.0),
                    "stop_count": int(route.get("stop_count", 0) or 0),
                    "factor": float(factor),
                    "route_fingerprint": _route_traffic_fingerprint(route),
                }
            )
    return candidates


def _target_current_plan_route_features(
    planner: Any,
    current_plan: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    service_direction: str,
) -> list[dict[str, Any]]:
    route_points_by_id = _current_plan_matched_route_points(
        current_plan,
        prepared_original_points,
        service_direction,
    )
    if not route_points_by_id:
        return []
    features: list[dict[str, Any]] = []
    previous_osrm_base_url = getattr(planner, "OSRM_BASE_URL", "")
    planner.OSRM_BASE_URL = planner.resolve_osrm_base_url(prepared_original_points)
    try:
        for route_id, points in route_points_by_id.items():
            osrm_duration_s = 0.0
            distance_m = 0.0
            leg_count = 0
            leg_details: list[dict[str, Any]] = []
            for origin, destination in zip(points[:-1], points[1:]):
                try:
                    leg_distance_m, leg_duration_s, geometry = planner.osrm_driving_direction(origin, destination)
                except Exception:
                    continue
                if float(leg_duration_s or 0.0) <= 0:
                    continue
                distance_m += float(leg_distance_m or 0.0)
                osrm_duration_s += float(leg_duration_s or 0.0)
                leg_count += 1
                leg_details.append({"geometry": geometry})
            if osrm_duration_s <= 0 or leg_count <= 0:
                continue
            fingerprint = build_route_traffic_fingerprint(
                {"leg_details": leg_details},
                route_points=points,
            )
            features.append(
                {
                    "route_id": str(route_id),
                    "osrm_duration_s": osrm_duration_s,
                    "distance_m": distance_m,
                    "stop_count": max(0, len(points) - 1),
                    "leg_count": leg_count,
                    "route_fingerprint": fingerprint or {},
                }
            )
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url
    return features


def _traffic_attribution_similarity_details(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target_duration = max(1.0, float(target.get("osrm_duration_s", 0.0) or 0.0))
    candidate_duration = max(1.0, float(candidate.get("osrm_duration_s", 0.0) or 0.0))
    duration_gap = abs(math.log(target_duration / candidate_duration))
    duration_score = 1.0 / (1.0 + duration_gap * 1.8)
    target_stop_count = int(target.get("stop_count", 0) or 0)
    candidate_stop_count = int(candidate.get("stop_count", 0) or 0)
    stop_score = 1.0 / (1.0 + abs(target_stop_count - candidate_stop_count) / 4.0)
    scale_score = 0.75 * duration_score + 0.25 * stop_score
    fingerprint_similarity = _traffic_fingerprint_similarity(target, candidate)
    if fingerprint_similarity is not None:
        geo_score = float(fingerprint_similarity["geo_score"])
        score = 0.65 * geo_score + 0.35 * scale_score
        return {
            "score": max(0.001, float(score)),
            "method": "geo_route_similarity",
            "geo_score": geo_score,
            "duration_score": float(duration_score),
            "stop_score": float(stop_score),
            "scale_score": float(scale_score),
            **fingerprint_similarity,
        }
    return {
        "score": max(0.001, float(duration_score * stop_score)),
        "method": "route_similarity",
        "geo_score": 0.0,
        "duration_score": float(duration_score),
        "stop_score": float(stop_score),
        "scale_score": float(scale_score),
        "corridor_overlap": 0.0,
        "stop_overlap": 0.0,
        "center_distance_km": -1.0,
        "center_score": 0.0,
        "bearing_score": 0.0,
        "bbox_score": 0.0,
    }


def _traffic_attribution_similarity(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    return float(_traffic_attribution_similarity_details(target, candidate)["score"])


def _route_attributed_factor(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ranked_all = sorted(
        (
            {
                **candidate,
                **{
                    "similarity_score": (details := _traffic_attribution_similarity_details(target, candidate))["score"],
                    "similarity_method": details["method"],
                    "duration_score": details["duration_score"],
                    "stop_score": details["stop_score"],
                    "scale_score": details["scale_score"],
                    "geo_similarity_score": details.get("geo_score", 0.0) if details["method"] == "geo_route_similarity" else 0.0,
                    "corridor_overlap": details["corridor_overlap"],
                    "center_distance_km": details["center_distance_km"],
                    "bearing_score": details["bearing_score"],
                },
            }
            for candidate in candidates
        ),
        key=lambda item: float(item["similarity_score"]),
        reverse=True,
    )

    geo_candidates = [
        item
        for item in ranked_all
        if item.get("similarity_method") == "geo_route_similarity"
    ]
    geo_ranked = [
        item
        for item in geo_candidates
        if float(item.get("geo_similarity_score", 0.0) or 0.0) >= TRAFFIC_ATTRIBUTION_MIN_GEO_SIMILARITY
    ]
    scale_ranked = [
        item
        for item in ranked_all
        if float(item.get("similarity_score", 0.0) or 0.0) >= TRAFFIC_ATTRIBUTION_MIN_SIMILARITY
    ]
    quality_reason = ""
    if geo_candidates:
        if len(geo_ranked) >= TRAFFIC_ATTRIBUTION_MIN_GEO_MATCHES:
            ranked = geo_ranked[:TRAFFIC_ATTRIBUTION_TOP_K]
            quality_reason = "geo_threshold_passed"
        else:
            ranked = scale_ranked[:TRAFFIC_ATTRIBUTION_TOP_K]
            quality_reason = "insufficient_geo_match_fallback_to_route_similarity"
    else:
        ranked = scale_ranked[:TRAFFIC_ATTRIBUTION_TOP_K]
        quality_reason = "scale_similarity_only"
    if not ranked:
        return None
    total_weight = sum(
        max(0.001, float(item["similarity_score"])) * max(60.0, float(item["osrm_duration_s"]))
        for item in ranked
    )
    if total_weight <= 0:
        return None
    factor = sum(
        float(item["factor"]) * max(0.001, float(item["similarity_score"])) * max(60.0, float(item["osrm_duration_s"]))
        for item in ranked
    ) / total_weight
    avg_similarity = sum(float(item["similarity_score"]) for item in ranked) / float(len(ranked))
    return {
        "route_id": str(target.get("route_id") or ""),
        "method": "geo_route_similarity" if quality_reason == "geo_threshold_passed" else "route_similarity",
        "quality_reason": quality_reason,
        "candidate_count": len(ranked_all),
        "geo_candidate_count": len(geo_candidates),
        "usable_geo_candidate_count": len(geo_ranked),
        "factor": float(factor),
        "weight_s": float(target.get("osrm_duration_s", 0.0) or 0.0),
        "osrm_duration_s": float(target.get("osrm_duration_s", 0.0) or 0.0),
        "stop_count": int(target.get("stop_count", 0) or 0),
        "matched_sample_count": len(ranked),
        "avg_similarity": float(avg_similarity),
        "top_matches": [
            {
                "route_id": str(item.get("route_id") or ""),
                "source_id": str(item.get("source_id") or ""),
                "factor": float(item.get("factor", 0.0) or 0.0),
                "osrm_duration_s": float(item.get("osrm_duration_s", 0.0) or 0.0),
                "stop_count": int(item.get("stop_count", 0) or 0),
                "similarity_score": float(item.get("similarity_score", 0.0) or 0.0),
                "similarity_method": str(item.get("similarity_method") or "route_similarity"),
                "duration_score": float(item.get("duration_score", 0.0) or 0.0),
                "stop_score": float(item.get("stop_score", 0.0) or 0.0),
                "scale_score": float(item.get("scale_score", 0.0) or 0.0),
                "geo_similarity_score": float(item.get("geo_similarity_score", 0.0) or 0.0),
                "corridor_overlap": float(item.get("corridor_overlap", 0.0) or 0.0),
                "center_distance_km": float(item.get("center_distance_km", -1.0) or -1.0),
                "bearing_score": float(item.get("bearing_score", 0.0) or 0.0),
            }
            for item in ranked[:3]
        ],
    }


def _traffic_attribution_confidence(score: float, route_count: int, sample_count: int) -> str:
    if route_count >= 3 and sample_count >= 10 and score >= 0.55:
        return "high"
    if route_count >= 1 and sample_count >= 3 and score >= 0.35:
        return "medium"
    return "low"


def build_traffic_attribution_context(
    input_records: list[dict[str, Any]],
    config: PlannerConfig,
    sample_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    mode = normalize_traffic_coefficient_mode(config.traffic_coefficient_mode)
    if mode != TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED:
        return {"enabled": False, "mode": mode, "reason": "legacy_mode"}
    country, city = infer_traffic_location(input_records)
    period = live_traffic_period_for_service_direction(config.service_direction)
    if period is None:
        return {"enabled": True, "mode": mode, "succeeded": False, "reason": "unsupported_service_direction"}
    matches = _matching_live_traffic_samples(
        country=country,
        city=city,
        period=period,
        sample_dir=sample_dir,
        now=now,
    )
    selected, selected_dates = _select_live_traffic_sample_window(matches)
    candidates = _live_route_attribution_candidates(selected)
    label = "AM Peak" if period == "am_peak" else "PM Peak"
    return {
        "enabled": True,
        "succeeded": bool(candidates),
        "mode": mode,
        "country": country,
        "city": city,
        "period": period,
        "period_label": label,
        "selected": selected,
        "selected_dates": selected_dates,
        "candidates": candidates,
        "sample_file_count": len(selected),
        "workday_count": len(selected_dates),
        "observed_route_sample_count": len(candidates),
        "reason": "" if candidates else "no_route_level_samples",
    }


def resolve_attributed_traffic_profile(
    planner: Any,
    current_plan: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    input_records: list[dict[str, Any]],
    config: PlannerConfig,
    fallback_profile_name: str,
    fallback_multiplier: float,
    fallback_context: str,
    sample_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    mode = normalize_traffic_coefficient_mode(config.traffic_coefficient_mode)
    if mode != TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED:
        return {"enabled": False, "mode": mode, "reason": "legacy_mode"}
    sample_context = build_traffic_attribution_context(input_records, config, sample_dir=sample_dir, now=now)
    if not sample_context.get("enabled"):
        return sample_context
    country = str(sample_context.get("country") or "")
    city = str(sample_context.get("city") or "")
    period = str(sample_context.get("period") or "")
    label = str(sample_context.get("period_label") or ("AM Peak" if period == "am_peak" else "PM Peak"))
    candidates = list(sample_context.get("candidates") or [])
    selected_dates = list(sample_context.get("selected_dates") or [])
    if not candidates:
        return {
            "enabled": True,
            "succeeded": False,
            "mode": mode,
            "reason": str(sample_context.get("reason") or "no_route_level_samples"),
            "fallback_profile_name": fallback_profile_name,
            "fallback_multiplier": float(fallback_multiplier),
            "fallback_context": fallback_context,
        }

    service_direction = normalize_service_direction(config.service_direction)
    target_routes = _target_current_plan_route_features(
        planner,
        current_plan,
        prepared_original_points,
        service_direction,
    )
    route_estimates = [
        estimate
        for target in target_routes
        if (estimate := _route_attributed_factor(target, candidates)) is not None
    ]
    if route_estimates:
        factor = _weighted_average_factor(
            [
                {"factor": float(item["factor"]), "weight_s": max(60.0, float(item["weight_s"]))}
                for item in route_estimates
            ]
        )
        avg_similarity = sum(
            float(item["avg_similarity"]) * max(60.0, float(item["weight_s"]))
            for item in route_estimates
        ) / sum(max(60.0, float(item["weight_s"])) for item in route_estimates)
        method = "route_similarity"
    else:
        factor = _weighted_average_factor(
            [
                {"factor": float(item["factor"]), "weight_s": max(60.0, float(item["osrm_duration_s"]))}
                for item in candidates
            ]
        )
        avg_similarity = 0.0
        method = "city_period_average"

    if factor is None:
        return {
            "enabled": True,
            "succeeded": False,
            "mode": mode,
            "reason": "no_usable_attribution_factor",
            "fallback_profile_name": fallback_profile_name,
            "fallback_multiplier": float(fallback_multiplier),
            "fallback_context": fallback_context,
        }
    confidence = _traffic_attribution_confidence(
        avg_similarity,
        len(route_estimates),
        len(candidates),
    )
    context = (
        f"{city.title()} {label} attributed traffic coefficient; "
        f"{len(route_estimates) or len(target_routes)} target route(s), "
        f"{len(candidates)} observed route sample(s), confidence {confidence}"
    )
    if method == "city_period_average":
        context += "; route similarity unavailable, using city-period sample average"
    return {
        "enabled": True,
        "succeeded": True,
        "mode": mode,
        "method": method,
        "country": country,
        "city": city,
        "period": period,
        "period_label": label,
        "traffic_profile_name": f"{label} (Attributed)",
        "traffic_time_multiplier": float(factor),
        "traffic_profile_context": context,
        "confidence": confidence,
        "confidence_score": float(avg_similarity),
        "target_route_count": len(target_routes),
        "attributed_route_count": len(route_estimates),
        "observed_route_sample_count": len(candidates),
        "sample_file_count": int(sample_context.get("sample_file_count", 0) or 0),
        "workday_count": len(selected_dates),
        "local_dates": selected_dates,
        "fallback_profile_name": fallback_profile_name,
        "fallback_multiplier": float(fallback_multiplier),
        "fallback_context": fallback_context,
        "route_estimates": route_estimates[:24],
    }


def _route_raw_osrm_duration_s(route: dict[str, Any]) -> float:
    raw_osrm_duration_s = float(route.get("raw_osrm_time_s", 0.0) or 0.0)
    if raw_osrm_duration_s > 0:
        return raw_osrm_duration_s
    return sum(
        float(leg.get("raw_osrm_duration_s", 0.0) or 0.0)
        for leg in list(route.get("leg_details") or [])
    )


def _optimized_route_attribution_target(
    route: dict[str, Any],
    route_index: int,
    points: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    raw_osrm_duration_s = _route_raw_osrm_duration_s(route)
    if raw_osrm_duration_s <= 0:
        return None
    stop_count = int(route.get("stop_count", 0) or 0)
    if stop_count <= 0:
        stop_count = len([node for node in list(route.get("nodes") or [])[1:] if int(node) != 0])
    target = {
        "route_id": str(route.get("route_id") or route.get("id") or f"Bus {route_index}"),
        "osrm_duration_s": raw_osrm_duration_s,
        "stop_count": max(0, stop_count),
    }
    fingerprint = build_route_traffic_fingerprint(route, all_points=points)
    if fingerprint:
        target["route_fingerprint"] = fingerprint
    return target


def _apply_route_traffic_factor(route: dict[str, Any], factor: float) -> None:
    route_factor = max(0.1, float(factor))
    route["traffic_buffer_factor"] = route_factor
    adjusted_drive_time_s = 0
    stop_service_time_s = 0
    for leg in list(route.get("leg_details") or []):
        raw_osrm_duration_s = int(float(leg.get("raw_osrm_duration_s", 0.0) or 0.0))
        stop_service_s = int(float(leg.get("stop_service_s", 0.0) or 0.0))
        adjusted_duration_s = int(round(raw_osrm_duration_s * route_factor)) if raw_osrm_duration_s else 0
        leg["traffic_adjusted_duration_s"] = adjusted_duration_s
        leg["duration_s"] = adjusted_duration_s + stop_service_s
        adjusted_drive_time_s += adjusted_duration_s
        stop_service_time_s += stop_service_s
    if adjusted_drive_time_s:
        route["traffic_adjusted_drive_time_s"] = adjusted_drive_time_s
        route["stop_service_time_s"] = stop_service_time_s
        route["time_s"] = adjusted_drive_time_s + stop_service_time_s


def apply_attributed_traffic_to_scenario_routes(
    routes: list[dict[str, Any]],
    attribution_context: dict[str, Any] | None,
    fallback_multiplier: float,
    scenario_label: str,
    points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    context = dict(attribution_context or {})
    if not context.get("enabled") or not context.get("candidates"):
        return []
    candidates = list(context.get("candidates") or [])
    estimates: list[dict[str, Any]] = []
    for route_index, route in enumerate(routes, start=1):
        target = _optimized_route_attribution_target(route, route_index, points)
        estimate = _route_attributed_factor(target, candidates) if target else None
        if estimate is None:
            factor = float(fallback_multiplier)
            estimate = {
                "route_id": str(route.get("route_id") or route.get("id") or f"Bus {route_index}"),
                "factor": factor,
                "weight_s": float(target.get("osrm_duration_s", 0.0) if target else 0.0),
                "osrm_duration_s": float(target.get("osrm_duration_s", 0.0) if target else 0.0),
                "stop_count": int(target.get("stop_count", 0) if target else 0),
                "matched_sample_count": 0,
                "avg_similarity": 0.0,
                "fallback": True,
                "reason": "no_similar_route_sample",
            }
        else:
            factor = float(estimate["factor"])
        _apply_route_traffic_factor(route, factor)
        route["traffic_time_source"] = "Attributed route-level traffic samples"
        route["traffic_attribution"] = {
            "scenario": scenario_label,
            "method": str(estimate.get("method") or "route_similarity") if int(estimate.get("matched_sample_count", 0) or 0) else "fallback",
            "quality_reason": str(estimate.get("quality_reason") or ""),
            "factor": float(factor),
            "avg_similarity": float(estimate.get("avg_similarity", 0.0) or 0.0),
            "matched_sample_count": int(estimate.get("matched_sample_count", 0) or 0),
            "candidate_count": int(estimate.get("candidate_count", 0) or 0),
            "geo_candidate_count": int(estimate.get("geo_candidate_count", 0) or 0),
            "usable_geo_candidate_count": int(estimate.get("usable_geo_candidate_count", 0) or 0),
            "top_matches": list(estimate.get("top_matches") or []),
        }
        estimates.append(
            {
                **estimate,
                "scenario": scenario_label,
                "vehicle_id": route.get("vehicle_id"),
                "bus_type_name": route.get("bus_type_name"),
            }
        )
    return estimates


def calibrate_peak_traffic_multiplier(
    planner: Any,
    current_plan: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    input_records: list[dict[str, Any]],
    config: PlannerConfig,
    fallback_profile_name: str,
    fallback_multiplier: float,
    fallback_context: str,
) -> dict[str, Any]:
    country, _city = infer_traffic_location(input_records)
    service_direction = normalize_service_direction(config.service_direction)
    if not AMAP_TRAFFIC_CALIBRATION_ENABLED:
        return {"enabled": False, "reason": "disabled_by_env"}
    if country != "CHINA":
        return {"enabled": False, "reason": "non_china_location"}
    if not current_plan:
        return {"enabled": False, "reason": "missing_current_plan"}
    if AMAP_TRAFFIC_CALIBRATION_MAX_CALLS <= 0:
        return {"enabled": False, "reason": "max_calls_zero"}

    periods = peak_traffic_periods()
    max_edges = max(1, AMAP_TRAFFIC_CALIBRATION_MAX_CALLS // max(1, len(periods)))
    route_points_by_id = _current_plan_matched_route_points(current_plan, prepared_original_points, service_direction)
    edges = _sample_current_plan_edges(route_points_by_id, max_edges)
    if not edges:
        return {"enabled": False, "reason": "no_matched_current_plan_edges"}
    route_edge_counts = {
        str(route_id): max(0, len(points) - 1)
        for route_id, points in route_points_by_id.items()
    }

    cache = _load_json_object(TRAFFIC_CALIBRATION_CACHE_PATH)
    cache_changed = False
    period_samples: dict[str, list[dict[str, float]]] = {str(period["key"]): [] for period in periods}
    route_period_samples: dict[str, dict[str, dict[str, float]]] = {
        str(period["key"]): {} for period in periods
    }
    api_calls = 0
    cache_hits = 0
    errors: list[str] = []

    previous_osrm_base_url = getattr(planner, "OSRM_BASE_URL", "")
    planner.OSRM_BASE_URL = planner.resolve_osrm_base_url(prepared_original_points)
    try:
        for period in periods:
            for route_id, origin, destination in edges:
                try:
                    distance_m, osrm_duration_s, _geometry = planner.osrm_driving_direction(origin, destination)
                    if osrm_duration_s <= 0:
                        continue
                    durations_s, from_cache = amap_future_driving_duration_seconds(
                        planner,
                        origin,
                        destination,
                        period,
                        cache,
                    )
                    if from_cache:
                        cache_hits += 1
                    else:
                        api_calls += 1
                        cache_changed = True
                    period_key = str(period["key"])
                    route_stats = route_period_samples.setdefault(period_key, {}).setdefault(
                        str(route_id),
                        {
                            "osrm_duration_s": 0.0,
                            "amap_duration_s": 0.0,
                            "sample_count": 0.0,
                            "sampled_leg_count": 0.0,
                        },
                    )
                    average_amap_duration_s = (
                        sum(float(item) for item in durations_s) / float(len(durations_s))
                    ) if durations_s else 0.0
                    route_stats["osrm_duration_s"] += float(osrm_duration_s)
                    route_stats["amap_duration_s"] += float(average_amap_duration_s)
                    route_stats["sample_count"] += float(len(durations_s))
                    route_stats["sampled_leg_count"] += 1.0
                    for duration_s in durations_s:
                        raw_factor = float(duration_s) / float(osrm_duration_s)
                        factor = min(
                            AMAP_TRAFFIC_CALIBRATION_MAX_FACTOR,
                            max(AMAP_TRAFFIC_CALIBRATION_MIN_FACTOR, raw_factor),
                        )
                        period_samples[str(period["key"])].append(
                            {
                                "factor": factor,
                                "weight_s": float(osrm_duration_s),
                                "route_id": str(route_id),
                                "distance_m": float(distance_m),
                            }
                        )
                except Exception as exc:
                    if len(errors) < 8:
                        errors.append(str(exc))
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url

    if cache_changed:
        _save_json_object(TRAFFIC_CALIBRATION_CACHE_PATH, cache)

    period_summaries: dict[str, dict[str, Any]] = {}
    route_periods = _summarize_route_period_stats(route_period_samples, route_edge_counts)
    for period in periods:
        key = str(period["key"])
        samples = period_samples.get(key) or []
        factor = _weighted_average_factor(samples)
        if factor is not None:
            period_summaries[key] = {
                "label": str(period["label"]),
                "factor": float(factor),
                "sample_count": len(samples),
                "firsttime": int(period["firsttime"]),
                "interval_seconds": int(period["interval"]),
                "time_point_count": int(period["count"]),
            }

    selected_key = "am_peak" if service_direction == "To School" else "pm_peak"
    selected = period_summaries.get(selected_key)
    combined_samples = [
        item
        for samples in period_samples.values()
        for item in samples
    ]
    combined_factor = _weighted_average_factor(combined_samples)
    selected_factor = float(selected["factor"]) if selected else combined_factor
    if selected_factor is None:
        return {
            "enabled": True,
            "succeeded": False,
            "reason": "no_usable_amap_samples",
            "fallback_profile_name": fallback_profile_name,
            "fallback_multiplier": float(fallback_multiplier),
            "fallback_context": fallback_context,
            "api_calls": api_calls,
            "cache_hits": cache_hits,
            "errors": errors,
        }

    selected_factor = min(
        AMAP_TRAFFIC_CALIBRATION_MAX_FACTOR,
        max(AMAP_TRAFFIC_CALIBRATION_MIN_FACTOR, float(selected_factor)),
    )
    return {
        "enabled": True,
        "succeeded": True,
        "selected_period": selected_key,
        "service_direction": service_direction,
        "traffic_profile_name": f"{fallback_profile_name} (AMap calibrated)",
        "traffic_time_multiplier": selected_factor,
        "traffic_profile_context": (
            f"AMap future driving calibration from current-plan legs; "
            f"{len(edges)} sampled edge(s), {api_calls} API call(s), {cache_hits} cache hit(s)"
        ),
        "fallback_profile_name": fallback_profile_name,
        "fallback_multiplier": float(fallback_multiplier),
        "fallback_context": fallback_context,
        "combined_factor": float(combined_factor) if combined_factor is not None else None,
        "periods": period_summaries,
        "route_periods": route_periods,
        "sampled_edge_count": len(edges),
        "api_calls": api_calls,
        "cache_hits": cache_hits,
        "errors": errors,
    }


def load_legacy_planner():
    spec = importlib.util.spec_from_file_location("legacy_busing_problem", LEGACY_PLANNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load planner from {LEGACY_PLANNER_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_request_output_directory(config: PlannerConfig) -> Path:
    if config.output_directory_name:
        return OUTPUT_DIR / config.output_directory_name
    return OUTPUT_DIR


def build_output_path_map(config: PlannerConfig) -> dict[str, str]:
    output_dir = build_request_output_directory(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "original": str(output_dir / config.original_output_name),
        "current_plan": str(output_dir / config.current_plan_output_name),
        "subway": str(output_dir / config.subway_output_name),
        "nearby": str(output_dir / config.nearby_output_name),
        "time_constrained": str(output_dir / config.time_constrained_output_name),
        "further_most": str(output_dir / config.further_most_output_name),
        "further_most_nearby": str(output_dir / config.further_most_nearby_output_name),
    }


def create_request_scoped_config(config: PlannerConfig, job_id: str | None = None) -> PlannerConfig:
    scoped = deepcopy(config)
    scoped.output_directory_name = job_id or uuid.uuid4().hex
    return scoped


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_planner_result_cache() -> dict[str, Any]:
    ensure_cache_dir()
    if not PLANNER_RESULT_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(PLANNER_RESULT_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_planner_result_cache(cache_data: dict[str, Any]) -> None:
    ensure_cache_dir()
    PLANNER_RESULT_CACHE_PATH.write_text(
        json.dumps(cache_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


PLANNER_RESULT_CACHE = load_planner_result_cache()


class _StreamingLogCapture(io.TextIOBase):
    def __init__(self, callback: Any | None = None) -> None:
        super().__init__()
        self._buffer = io.StringIO()
        self._line_buffer = ""
        self._callback = callback

    def write(self, text: str) -> int:
        self._buffer.write(text)
        self._line_buffer += text
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            if self._callback is not None:
                self._callback(line)
        return len(text)

    def flush(self) -> None:
        if self._line_buffer and self._callback is not None:
            self._callback(self._line_buffer)
            self._line_buffer = ""

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def clear_route_caches() -> None:
    global PLANNER_RESULT_CACHE

    ensure_cache_dir()
    PLANNER_RESULT_CACHE = {}
    save_planner_result_cache(PLANNER_RESULT_CACHE)

    for cache_path in (ROUTE_METRICS_CACHE_PATH, ROUTE_GEOMETRY_CACHE_PATH):
        cache_path.write_text("{}", encoding="utf-8")


def get_excel_sheet_names(excel_path: str | Path) -> list[str]:
    workbook = pd.ExcelFile(excel_path)
    return list(workbook.sheet_names)


def read_excel_sheet(excel_path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    return pd.read_excel(excel_path, sheet_name=sheet_name)


def read_input_records_from_excel(
    excel_path: str | Path,
    address_column: str,
    city_column: str,
    country_column: str,
    passenger_count_column: str,
    sheet_name: str | int = 0,
) -> list[dict[str, Any]]:
    df = read_excel_sheet(excel_path, sheet_name=sheet_name)
    if address_column not in df.columns:
        raise ValueError(f"Column not found: {address_column}")
    if city_column not in df.columns:
        raise ValueError(f"Column not found: {city_column}")
    if country_column not in df.columns:
        raise ValueError(f"Column not found: {country_column}")
    if passenger_count_column not in df.columns:
        raise ValueError(f"Column not found: {passenger_count_column}")

    records: list[dict[str, Any]] = []
    for row_index, row in df.iterrows():
        raw_address = row[address_column]
        if pd.isna(raw_address) or not str(raw_address).strip():
            continue

        address = str(raw_address).strip()
        city = "" if pd.isna(row[city_column]) else str(row[city_column]).strip()
        country = "" if pd.isna(row[country_column]) else str(row[country_column]).strip()
        raw_count = row[passenger_count_column]
        if row_index == 0 and (pd.isna(raw_count) or str(raw_count).strip() == ""):
            passenger_count = 0
        else:
            try:
                passenger_count = int(raw_count)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Passenger count is invalid for address: {address}. "
                    f"Please provide a whole-number passenger count in the selected column."
                )
        if passenger_count < 0:
            raise ValueError(f"Passenger count cannot be negative for address: {address}.")

        records.append(
            {
                "address": address,
                "city": city,
                "country": country,
                "passenger_count": passenger_count,
            }
        )

    if not records:
        raise ValueError("No usable addresses found in the selected column.")
    return records


def build_excel_template_bytes() -> bytes:
    assignments_df = pd.DataFrame(
        {
            "route_id": ["R1", "R1", "R1", "R2", "R2"],
            "stop_sequence": [1, 2, 3, 1, 2],
            "bus_type": ["Large Bus", "Large Bus", "Large Bus", "Mid Bus", "Mid Bus"],
            "country": ["China", "China", "China", "China", "China"],
            "city": ["Shanghai", "Shanghai", "Shanghai", "Shanghai", "Shanghai"],
            "address": [
                "上海市闵行区马桥镇曙光路1935号",
                "上海市徐汇区漕溪北路398号",
                "上海市静安区南京西路1038号",
                "上海市闵行区马桥镇曙光路1935号",
                "上海市长宁区长宁路1018号",
            ],
            "passenger_count": [0, 3, 2, 0, 2],
            "note": ["Depot", "Current route stop 1", "Current route stop 2", "Depot", "Current route stop 3"],
        }
    )
    fleet_df = pd.DataFrame(
        {
            "bus_type": ["Large Bus", "Mid Bus"],
            "seat_count": [45, 28],
            "vehicle_count": [1, 1],
            "note": [
                "Actual supplier seat count for Large Bus",
                "Actual supplier seat count for Mid Bus",
            ],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        assignments_df.to_excel(writer, index=False, sheet_name="current_plan_assignments")
        fleet_df.to_excel(writer, index=False, sheet_name="current_plan_fleet")
    return buffer.getvalue()


def build_baseline_template_workbook_bytes(
    scenario: dict[str, Any],
    *,
    service_direction: str,
) -> bytes:
    points = [dict(item) for item in list(scenario.get("points") or [])]
    routes = [dict(item) for item in list(scenario.get("routes") or [])]
    point_by_node: dict[int, dict[str, Any]] = {}
    for index, point in enumerate(points):
        raw_node_id = point.get("node_id", index)
        try:
            node_id = int(raw_node_id)
        except (TypeError, ValueError):
            node_id = index
        point_by_node[node_id] = point

    assignment_rows: list[dict[str, Any]] = []
    fleet_counts: dict[tuple[str, int], int] = {}

    for route_index, route in enumerate(routes, start=1):
        route_id = str(route.get("route_id") or route.get("vehicle_id") or route_index).strip()
        if not route_id.upper().startswith("R"):
            route_id = f"R{route_id}"
        bus_type = str(route.get("bus_type_name") or route.get("bus_type") or "").strip() or "Unknown"
        bus_capacity = int(route.get("bus_capacity", 0) or 0)
        fleet_counts[(bus_type, bus_capacity)] = fleet_counts.get((bus_type, bus_capacity), 0) + 1

        for stop_sequence, raw_node_id in enumerate(list(route.get("nodes") or []), start=1):
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError):
                node_id = -1
            point = point_by_node.get(node_id, {})
            time_impact = dict(point.get("time_impact") or {})
            assignment_rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": stop_sequence,
                    "bus_type": bus_type,
                    "country": str(point.get("country", "") or "").strip(),
                    "city": str(point.get("city", "") or "").strip(),
                    "address": str(point.get("address", "") or "").strip(),
                    "passenger_count": int(point.get("passenger_count", 0) or 0),
                    "original pick up/drop off time": str(
                        time_impact.get("current_time_label") or ""
                    ).strip(),
                    "new pick up/drop off time": str(
                        time_impact.get("new_time_label") or ""
                    ).strip(),
                    "note": "Free optimization baseline export",
                }
            )

    if not assignment_rows:
        raise ValueError("Free optimization baseline has no route nodes to export.")

    fleet_rows = [
        {
            "bus_type": bus_type,
            "seat_count": seat_count,
            "vehicle_count": vehicle_count,
            "note": "Generated from free optimization baseline result",
        }
        for (bus_type, seat_count), vehicle_count in sorted(fleet_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    notes_df = pd.DataFrame(
        {
            "section": ["Source", "Service Direction", "How to use"],
            "guidance": [
                "Generated from the Free Optimization Baseline result.",
                str(service_direction or "From School"),
                "This workbook uses the current-plan input sheet format and can be uploaded for another audit run.",
            ],
        }
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(assignment_rows).to_excel(writer, index=False, sheet_name="current_plan_assignments")
        pd.DataFrame(fleet_rows).to_excel(writer, index=False, sheet_name="current_plan_fleet")
        notes_df.to_excel(writer, index=False, sheet_name="template_notes")
    return buffer.getvalue()


def get_demo_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "country": [
                "China","China","China","China","China","China","China","China","China","China",
                "China","China","China","China","China","China","China","China","China","China",
            ],
            "city": [
                "Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai",
                "Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai","Shanghai",
            ],
            "address": [
                "上海市闵行区马桥镇曙光路1935号",
                "上海市徐汇区漕溪北路398号",
                "上海市徐汇区肇嘉浜路1111号",
                "上海市静安区南京西路1038号",
                "上海市静安区北京西路1701号",
                "上海市长宁区长宁路1018号",
                "上海市长宁区定西路1328号",
                "上海市普陀区中山北路3300号",
                "上海市普陀区武宁路101号",
                "上海市浦东新区世纪大道100号",
                "上海市浦东新区陆家嘴环路1000号",
                "上海市浦东新区锦绣路1001号",
                "上海市闵行区申长路688号",
                "上海市闵行区吴中路1799号",
                "上海市闵行区七莘路3655号",
                "上海市虹口区东大名路501号",
                "上海市虹口区四川北路2002号",
                "上海市杨浦区四平路1239号",
                "上海市杨浦区控江路1688号",
                "上海市宝山区友谊路1538号",
            ],
            "passenger_count": [
                0, 2, 3, 2, 2, 1, 2, 3, 2, 4,
                3, 2, 2, 3, 2, 2, 1, 2, 2, 3,
            ],
            "note": [
                "Depot / school",
                "Demo stop 1",
                "Demo stop 2",
                "Demo stop 3",
                "Demo stop 4",
                "Demo stop 5",
                "Demo stop 6",
                "Demo stop 7",
                "Demo stop 8",
                "Demo stop 9",
                "Demo stop 10",
                "Demo stop 11",
                "Demo stop 12",
                "Demo stop 13",
                "Demo stop 14",
                "Demo stop 15",
                "Demo stop 16",
                "Demo stop 17",
                "Demo stop 18",
                "Demo stop 19",
            ],
        }
    )


def _apply_config(planner: Any, config: PlannerConfig, input_records: list[dict[str, Any]]) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    traffic_profile_name, traffic_time_multiplier, traffic_profile_context = resolve_traffic_profile(
        config.traffic_profile_name,
        input_records,
    )

    planner.INPUT_STOPS = deepcopy(input_records)
    planner.ADDRESSES = [record["address"] for record in input_records]
    planner.BUS_TYPE_CONFIGS = _build_bus_type_configs(config)
    (
        planner.VEHICLE_FIXED_COST,
        planner.MIN_LOAD_TARGET,
        planner.MIN_LOAD_PENALTY,
    ) = _build_slot_policy_maps(config)
    planner.EXPRESS_THRESHOLD_KM = float(config.express_threshold_km)
    planner.RESERVED_EXPRESS_BUSES = int(config.reserved_express_buses)
    planner.EXPRESS_SKIP_INNER_KM = float(config.express_skip_inner_km)
    planner.MAX_ROUTE_DURATION_SECONDS = effective_route_duration_limit_minutes(config) * 60
    planner.ANNOTATION_ROUTE_DURATION_SECONDS = config.max_route_duration_minutes * 60
    planner.STOP_SERVICE_SECONDS = config.stop_service_minutes * 60
    planner.SUBWAY_SEARCH_RADIUS_M = config.subway_search_radius_m
    planner.MAX_SUBWAY_WALK_DISTANCE_M = config.max_subway_walk_distance_m
    planner.NEARBY_CLUSTER_RADIUS_M = config.nearby_cluster_radius_m
    planner.COMFORT_LOAD_FACTOR = min(1.0, max(0.1, float(config.comfort_load_factor)))
    planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
    planner.TRAFFIC_TIME_MULTIPLIER = traffic_time_multiplier
    planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
    planner.SERVICE_DIRECTION = normalize_service_direction(config.service_direction)
    planner.MATRIX_NEAREST_NEIGHBORS = config.matrix_nearest_neighbors
    planner.MATRIX_MAX_CANDIDATE_DISTANCE_KM = config.matrix_candidate_radius_km
    planner.OPERATING_COST_PER_KM = config.operating_cost_per_km
    if config.revenue_rules is not None:
        planner.REVENUE_RULES = config.revenue_rules

    path_map = build_output_path_map(config)
    planner.OUTPUT_HTML = path_map["original"]
    planner.SUBWAY_OUTPUT_HTML = path_map["subway"]
    planner.NEARBY_OUTPUT_HTML = path_map["nearby"]

    return {
        "original_html": path_map["original"],
        "subway_html": path_map["subway"],
        "nearby_html": path_map["nearby"],
    }


def build_planner_cache_key(input_records: list[dict[str, Any]], config: PlannerConfig) -> str:
    payload = {
        "input_records": [
            {
                "country": str(item.get("country", "")).strip(),
                "city": str(item.get("city", "")).strip(),
                "address": str(item["address"]).strip(),
                "passenger_count": int(item.get("passenger_count", 0)),
            }
            for item in input_records
        ],
        "route_config": {
            "large_bus_name": str(config.large_bus_name),
            "large_bus_capacity": int(config.large_bus_capacity),
            "mid_bus_name": str(config.mid_bus_name),
            "mid_bus_capacity": int(config.mid_bus_capacity),
            "small_bus_name": str(config.small_bus_name),
            "small_bus_capacity": int(config.small_bus_capacity),
            "large_bus_max_count": int(config.large_bus_max_count),
            "mid_bus_max_count": int(config.mid_bus_max_count),
            "small_bus_max_count": int(config.small_bus_max_count),
            "free_baseline_large_bus_ratio": float(config.free_baseline_large_bus_ratio),
            "free_baseline_mid_bus_ratio": float(config.free_baseline_mid_bus_ratio),
            "free_baseline_small_bus_ratio": float(config.free_baseline_small_bus_ratio),
            "express_threshold_km": float(config.express_threshold_km),
            "reserved_express_buses": int(config.reserved_express_buses),
            "express_skip_inner_km": float(config.express_skip_inner_km),
            "max_route_duration_minutes": int(config.max_route_duration_minutes),
            "stop_service_minutes": int(config.stop_service_minutes),
            "subway_search_radius_m": int(config.subway_search_radius_m),
            "max_subway_walk_distance_m": int(config.max_subway_walk_distance_m),
            "nearby_cluster_radius_m": int(config.nearby_cluster_radius_m),
            "comfort_load_factor": float(config.comfort_load_factor),
            "traffic_profile_name": resolve_traffic_profile(config.traffic_profile_name, input_records)[0],
            "service_direction": normalize_service_direction(config.service_direction),
            "matrix_nearest_neighbors": int(config.matrix_nearest_neighbors),
            "matrix_candidate_radius_km": float(config.matrix_candidate_radius_km),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def summarize_logs(log_text: str, uploaded_address_count: int) -> dict[str, Any]:
    original_bus_matches = re.findall(r"=== Route Plan ===(.*?)(?:Rendering route map for Original stops|\Z)", log_text, re.S)
    subway_bus_matches = re.findall(r"=== Route Plan ===(.*?)(?:Rendering route map for Subway-aggregated stops|\Z)", log_text, re.S)
    nearby_bus_matches = re.findall(r"=== Route Plan ===(.*?)(?:Rendering route map for Nearby-address aggregated stops|\Z)", log_text, re.S)

    original_section = original_bus_matches[0] if original_bus_matches else ""
    subway_section = subway_bus_matches[-1] if subway_bus_matches else ""
    nearby_section = nearby_bus_matches[-1] if nearby_bus_matches else ""

    original_vehicle_count = len(re.findall(r"\nBus \d+ \|", original_section))
    subway_vehicle_count = len(re.findall(r"\nBus \d+ \|", subway_section))
    nearby_vehicle_count = len(re.findall(r"\nBus \d+ \|", nearby_section))

    valid_stop_match = re.search(r"Valid stops:\s*(\d+)\s*/\s*(\d+)", log_text)
    subway_reduce_match = re.search(r"Subway aggregation reduced stops from (\d+) to (\d+)", log_text)
    nearby_reduce_match = re.search(r"Nearby-address aggregation reduced stops from (\d+) to (\d+)", log_text)

    original_valid_stops = int(valid_stop_match.group(1)) if valid_stop_match else uploaded_address_count
    original_uploaded_stops = int(valid_stop_match.group(2)) if valid_stop_match else uploaded_address_count
    subway_before = int(subway_reduce_match.group(1)) + 1 if subway_reduce_match else original_valid_stops
    subway_after = int(subway_reduce_match.group(2)) + 1 if subway_reduce_match else original_valid_stops
    nearby_before = int(nearby_reduce_match.group(1)) + 1 if nearby_reduce_match else original_valid_stops
    nearby_after = int(nearby_reduce_match.group(2)) + 1 if nearby_reduce_match else original_valid_stops

    original_non_depot = max(0, original_valid_stops - 1)
    subway_non_depot = max(0, subway_after - 1)
    nearby_non_depot = max(0, nearby_after - 1)

    stop_reduction = original_non_depot - subway_non_depot
    stop_reduction_pct = (stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0
    nearby_stop_reduction = original_non_depot - nearby_non_depot
    nearby_stop_reduction_pct = (nearby_stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0

    vehicle_reduction = original_vehicle_count - subway_vehicle_count
    vehicle_reduction_pct = (vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0
    nearby_vehicle_reduction = original_vehicle_count - nearby_vehicle_count
    nearby_vehicle_reduction_pct = (nearby_vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0

    return {
        "uploaded_address_count": uploaded_address_count,
        "original_uploaded_stops": original_uploaded_stops,
        "original_valid_stops": original_valid_stops,
        "subway_valid_stops": subway_after,
        "nearby_valid_stops": nearby_after,
        "original_vehicle_count": original_vehicle_count,
        "subway_vehicle_count": subway_vehicle_count,
        "nearby_vehicle_count": nearby_vehicle_count,
        "stop_reduction": stop_reduction,
        "stop_reduction_pct": stop_reduction_pct,
        "nearby_stop_reduction": nearby_stop_reduction,
        "nearby_stop_reduction_pct": nearby_stop_reduction_pct,
        "vehicle_reduction": vehicle_reduction,
        "vehicle_reduction_pct": vehicle_reduction_pct,
        "nearby_vehicle_reduction": nearby_vehicle_reduction,
        "nearby_vehicle_reduction_pct": nearby_vehicle_reduction_pct,
        "subway_before_stops": subway_before,
        "nearby_before_stops": nearby_before,
    }


def summarize_structured_results(results: dict[str, Any], uploaded_address_count: int) -> dict[str, Any]:
    original = results.get("original", {})
    subway = results.get("subway", {})
    nearby = results.get("nearby", {})
    currency_code = str(results.get("currency_code", "USD"))
    input_address_review = dict(results.get("input_address_review") or {})
    input_address_review_summary = dict(input_address_review.get("summary") or {})

    original_valid_stops = int(original.get("stop_count", uploaded_address_count))
    subway_valid_stops = int(subway.get("stop_count", original_valid_stops))
    nearby_valid_stops = int(nearby.get("stop_count", original_valid_stops))

    original_vehicle_count = int(original.get("bus_count", 0))
    subway_vehicle_count = int(subway.get("bus_count", 0))
    nearby_vehicle_count = int(nearby.get("bus_count", 0))
    original_bus_mix = dict(original.get("bus_mix", {}))
    subway_bus_mix = dict(subway.get("bus_mix", {}))
    nearby_bus_mix = dict(nearby.get("bus_mix", {}))
    original_total_operating_cost = float(original.get("total_operating_cost", 0.0))
    subway_total_operating_cost = float(subway.get("total_operating_cost", 0.0))
    nearby_total_operating_cost = float(nearby.get("total_operating_cost", 0.0))
    original_total_chargeable_revenue = float(original.get("total_chargeable_revenue", 0.0))
    subway_total_chargeable_revenue = float(subway.get("total_chargeable_revenue", 0.0))
    nearby_total_chargeable_revenue = float(nearby.get("total_chargeable_revenue", 0.0))
    original_total_profit_loss = float(original.get("total_profit_loss", 0.0))
    subway_total_profit_loss = float(subway.get("total_profit_loss", 0.0))
    nearby_total_profit_loss = float(nearby.get("total_profit_loss", 0.0))

    original_non_depot = max(0, original_valid_stops)
    subway_non_depot = max(0, subway_valid_stops)
    nearby_non_depot = max(0, nearby_valid_stops)

    stop_reduction = original_non_depot - subway_non_depot
    stop_reduction_pct = (stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0
    nearby_stop_reduction = original_non_depot - nearby_non_depot
    nearby_stop_reduction_pct = (nearby_stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0

    vehicle_reduction = original_vehicle_count - subway_vehicle_count
    vehicle_reduction_pct = (vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0
    nearby_vehicle_reduction = original_vehicle_count - nearby_vehicle_count
    nearby_vehicle_reduction_pct = (nearby_vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0
    subway_cost_savings = original_total_operating_cost - subway_total_operating_cost
    nearby_cost_savings = original_total_operating_cost - nearby_total_operating_cost
    subway_profit_improvement = subway_total_profit_loss - original_total_profit_loss
    nearby_profit_improvement = nearby_total_profit_loss - original_total_profit_loss

    return {
        "uploaded_address_count": uploaded_address_count,
        "currency_code": currency_code,
        "input_address_review_warning_count": int(
            input_address_review_summary.get("warning_count", 0) or 0
        ),
        "original_uploaded_stops": uploaded_address_count,
        "original_valid_stops": original_valid_stops,
        "subway_valid_stops": subway_valid_stops,
        "nearby_valid_stops": nearby_valid_stops,
        "original_vehicle_count": original_vehicle_count,
        "subway_vehicle_count": subway_vehicle_count,
        "nearby_vehicle_count": nearby_vehicle_count,
        "original_bus_mix": original_bus_mix,
        "subway_bus_mix": subway_bus_mix,
        "nearby_bus_mix": nearby_bus_mix,
        "original_total_operating_cost": original_total_operating_cost,
        "subway_total_operating_cost": subway_total_operating_cost,
        "nearby_total_operating_cost": nearby_total_operating_cost,
        "original_total_chargeable_revenue": original_total_chargeable_revenue,
        "subway_total_chargeable_revenue": subway_total_chargeable_revenue,
        "nearby_total_chargeable_revenue": nearby_total_chargeable_revenue,
        "original_total_profit_loss": original_total_profit_loss,
        "subway_total_profit_loss": subway_total_profit_loss,
        "nearby_total_profit_loss": nearby_total_profit_loss,
        "stop_reduction": stop_reduction,
        "stop_reduction_pct": stop_reduction_pct,
        "nearby_stop_reduction": nearby_stop_reduction,
        "nearby_stop_reduction_pct": nearby_stop_reduction_pct,
        "vehicle_reduction": vehicle_reduction,
        "vehicle_reduction_pct": vehicle_reduction_pct,
        "nearby_vehicle_reduction": nearby_vehicle_reduction,
        "nearby_vehicle_reduction_pct": nearby_vehicle_reduction_pct,
        "subway_cost_savings": subway_cost_savings,
        "nearby_cost_savings": nearby_cost_savings,
        "subway_profit_improvement": subway_profit_improvement,
        "nearby_profit_improvement": nearby_profit_improvement,
        "subway_before_stops": original_valid_stops,
        "nearby_before_stops": original_valid_stops,
    }


def _normalize_revenue_rules(revenue_rules: list[dict[str, float | None]] | None) -> list[dict[str, float | None]]:
    if not revenue_rules:
        return []
    normalized = []
    for rule in revenue_rules:
        min_km = float(rule.get("min_km", 0.0) or 0.0)
        max_km_raw = rule.get("max_km")
        max_km = None if max_km_raw in (None, "", "None") else float(max_km_raw)
        fee_per_person = float(rule.get("fee_per_person", 0.0) or 0.0)
        normalized.append(
            {
                "min_km": min_km,
                "max_km": max_km,
                "fee_per_person": fee_per_person,
            }
        )
    normalized.sort(key=lambda item: item["min_km"])
    return normalized


def _find_revenue_rule(distance_km: float, revenue_rules: list[dict[str, float | None]]) -> dict[str, float | None] | None:
    for rule in revenue_rules:
        upper_ok = rule["max_km"] is None or distance_km < rule["max_km"]
        if distance_km >= rule["min_km"] and upper_ok:
            return rule
    return None


def apply_pricing_to_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    repriced = deepcopy(results)
    revenue_rules = _normalize_revenue_rules(config.revenue_rules)

    for scenario_key in (
        "original",
        "current_plan",
        "subway",
        "nearby",
        "time_constrained",
        "further_most",
        "further_most_nearby",
    ):
        scenario = repriced.get(scenario_key) or {}
        routes = scenario.get("routes") or []
        total_operating_cost = 0.0
        total_chargeable_revenue = 0.0

        for route in routes:
            route_operating_cost = (float(route.get("distance_m", 0.0)) / 1000.0) * config.operating_cost_per_km
            route_revenue = 0.0
            revenue_details = []
            for item in route.get("stop_revenue_basis", []):
                distance_km = float(item.get("distance_km", 0.0))
                passenger_count = int(item.get("passenger_count", 1))
                rule = _find_revenue_rule(distance_km, revenue_rules)
                fee_per_person = float(rule["fee_per_person"]) if rule else 0.0
                stop_revenue = passenger_count * fee_per_person
                route_revenue += stop_revenue
                revenue_details.append(
                    {
                        "stop_address": item.get("stop_address"),
                        "distance_km": distance_km,
                        "passenger_count": passenger_count,
                        "fee_per_person": fee_per_person,
                        "stop_revenue": stop_revenue,
                    }
                )

            route["operating_cost"] = route_operating_cost
            route["chargeable_revenue"] = route_revenue
            route["profit_loss"] = route_revenue - route_operating_cost
            route["revenue_details"] = revenue_details
            total_operating_cost += route_operating_cost
            total_chargeable_revenue += route_revenue

        scenario["total_operating_cost"] = total_operating_cost
        scenario["total_chargeable_revenue"] = total_chargeable_revenue
        scenario["total_profit_loss"] = total_chargeable_revenue - total_operating_cost

    return repriced


def rerender_html_from_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    hydrated = attach_output_paths_to_structured_results(results, config)
    planner = load_legacy_planner()
    input_records = list((hydrated.get("current_plan") or hydrated.get("original") or {}).get("points") or [])
    traffic_profile_name, traffic_time_multiplier, traffic_profile_context = resolve_traffic_profile(
        config.traffic_profile_name,
        input_records,
    )
    planner.CURRENT_CURRENCY_CODE = str(hydrated.get("currency_code", "USD"))
    planner.BUS_TYPE_CONFIGS = _build_bus_type_configs(config)
    (
        planner.VEHICLE_FIXED_COST,
        planner.MIN_LOAD_TARGET,
        planner.MIN_LOAD_PENALTY,
    ) = _build_slot_policy_maps(config)
    planner.OPERATING_COST_PER_KM = config.operating_cost_per_km
    planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
    planner.TRAFFIC_TIME_MULTIPLIER = traffic_time_multiplier
    planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
    planner.SERVICE_DIRECTION = normalize_service_direction(config.service_direction)
    planner.MAX_ROUTE_DURATION_SECONDS = effective_route_duration_limit_minutes(config) * 60
    planner.ANNOTATION_ROUTE_DURATION_SECONDS = config.max_route_duration_minutes * 60
    planner.STOP_SERVICE_SECONDS = config.stop_service_minutes * 60
    planner.MATRIX_NEAREST_NEIGHBORS = config.matrix_nearest_neighbors
    planner.MATRIX_MAX_CANDIDATE_DISTANCE_KM = config.matrix_candidate_radius_km
    if config.revenue_rules is not None:
        planner.REVENUE_RULES = config.revenue_rules

    for scenario_key in (
        "original",
        "current_plan",
        "subway",
        "nearby",
        "time_constrained",
        "further_most",
        "further_most_nearby",
    ):
        scenario = hydrated.get(scenario_key) or {}
        points = scenario.get("points")
        routes = scenario.get("routes")
        output_html = scenario.get("output_html")
        if points is not None and routes is not None and output_html:
            planner.render_map(
                points,
                routes,
                output_html,
                outlying_private_access_rows=list(scenario.get("outlying_private_access_rows") or []),
            )
    return hydrated


def extract_excluded_stops(log_text: str, original_addresses: list[str]) -> list[dict[str, str]]:
    bucket_reasons = {
        "invalid_geocode_or_input": "Could not be geocoded or was not accepted as a valid input stop.",
    }

    excluded_rows: list[dict[str, str]] = []
    seen_addresses: set[str] = set()
    current_bucket = None

    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if "The following addresses were not accepted as valid stops" in line:
            current_bucket = "invalid_geocode_or_input"
            continue
        if line.startswith("[") and not line.startswith("[WARN]"):
            current_bucket = None
            continue

        if not current_bucket:
            continue

        if line.startswith("- ") or line.startswith("  - "):
            normalized = line[2:].strip() if line.startswith("- ") else line[4:].strip()
            matched_address = next(
                (address for address in original_addresses if normalized.startswith(address)),
                None,
            )
            if matched_address and matched_address not in seen_addresses:
                excluded_rows.append(
                    {
                        "address": matched_address,
                        "reason": bucket_reasons[current_bucket],
                    }
                )
                seen_addresses.add(matched_address)

    return excluded_rows


def extract_geocode_warnings(log_text: str, original_addresses: list[str]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    seen_addresses: set[str] = set()

    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched_address = next(
            (address for address in original_addresses if address in line),
            None,
        )
        if not matched_address or matched_address in seen_addresses:
            continue

        if "geocode failed" in line or "geocode crashed" in line:
            warnings.append(
                {
                    "address": matched_address,
                    "warning": "This address could not be resolved to coordinates by the map API.",
                    "suggestion": (
                        "Use a more complete postal-style address, including district, road name, "
                        "building number, and city. Avoid abbreviations, landmarks only, or informal descriptions."
                    ),
                }
            )
            seen_addresses.add(matched_address)

    return warnings


def _point_coordinate(point: dict[str, Any]) -> tuple[float, float] | None:
    for lat_key, lng_key in (("lat", "lng"), ("plot_lat", "plot_lng")):
        try:
            lat = float(point.get(lat_key, 0.0) or 0.0)
            lng = float(point.get(lng_key, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if lat and lng:
            return lat, lng
    return None


def _point_display_address(point: dict[str, Any]) -> str:
    return str(
        point.get("display_address")
        or point.get("address")
        or point.get("requested_address")
        or ""
    ).strip()


def _point_source_rows(point: dict[str, Any]) -> str:
    return str(point.get("source_excel_rows") or point.get("excel_rows") or "").strip()


_INPUT_REVIEW_CHINA_CITY_CONFIGS: dict[str, dict[str, Any]] = {
    "shanghai": {
        "aliases": ["shanghai", "shanghai city", "上海", "上海市"],
        "adcode_prefixes": ["31"],
        "bbox": (31.90, 122.25, 30.65, 120.85),
    },
    "beijing": {
        "aliases": ["beijing", "beijing city", "北京", "北京市"],
        "adcode_prefixes": ["11"],
        "bbox": (41.10, 117.60, 39.40, 115.40),
    },
    "suzhou": {
        "aliases": ["suzhou", "suzhou city", "苏州", "苏州市"],
        "adcode_prefixes": ["3205"],
        "bbox": (32.15, 121.35, 30.75, 119.85),
    },
    "xian": {
        "aliases": ["xian", "xi'an", "xi an", "西安", "西安市"],
        "adcode_prefixes": ["6101"],
        "bbox": (34.85, 109.85, 33.40, 107.50),
    },
}
_INPUT_REVIEW_KOREA_BBOX = (38.75, 132.2, 33.0, 124.0)


def _input_review_is_china(country: str) -> bool:
    return country.strip().lower() in {"china", "中国", "中华人民共和国"}


def _input_review_is_korea(country: str) -> bool:
    return country.strip().lower() in {
        "korea",
        "south korea",
        "republic of korea",
        "대한민국",
        "한국",
        "남한",
    }


def _input_review_city_key(city: str) -> str:
    normalized = city.strip().lower().replace("'", "").replace(" ", "")
    if normalized in {"shanghai", "shanghaicity", "上海", "上海市"}:
        return "shanghai"
    if normalized in {"beijing", "beijingcity", "北京", "北京市"}:
        return "beijing"
    if normalized in {"suzhou", "suzhoucity", "苏州", "苏州市"}:
        return "suzhou"
    if normalized in {"xian", "xi’an", "西安", "西安市"}:
        return "xian"
    return ""


def _input_review_within_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    north, east, south, west = bbox
    return south <= lat <= north and west <= lng <= east


def _input_review_region_issue(point: dict[str, Any]) -> dict[str, Any] | None:
    coords = _point_coordinate(point)
    if coords is None:
        return None
    lat, lng = coords
    country = str(point.get("country", point.get("requested_country", "")) or "").strip()
    city = str(point.get("city", point.get("requested_city", "")) or "").strip()
    formatted_address = str(point.get("formatted_address", "") or "").strip()
    adcode = str(point.get("adcode", "") or "").strip()

    if _input_review_is_china(country):
        city_key = _input_review_city_key(city)
        config = _INPUT_REVIEW_CHINA_CITY_CONFIGS.get(city_key)
        if not config:
            return None
        reason = ""
        if adcode and not any(adcode.startswith(prefix) for prefix in config["adcode_prefixes"]):
            reason = f"Resolved adcode {adcode} does not match the workbook city {city or 'N/A'}."
        else:
            address_text = formatted_address.lower()
            for other_key, other_config in _INPUT_REVIEW_CHINA_CITY_CONFIGS.items():
                if other_key == city_key:
                    continue
                if any(alias.lower() in address_text for alias in other_config["aliases"]):
                    reason = f"Resolved address appears to be in {other_key}, not the workbook city {city or 'N/A'}."
                    break
        if not reason and not _input_review_within_bbox(lat, lng, config["bbox"]):
            reason = f"Resolved coordinates are outside the supported service area for {city or 'this city'}."
        if not reason:
            return None
        warning = _address_warning_base(
            point,
            warning_type="region_mismatch",
            reason=reason,
        )
        warning.update(
            {
                "expected_country": country,
                "expected_city": city,
                "resolved_adcode": adcode,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "message": (
                    f"{_point_display_address(point) or 'This stop'} resolved outside the expected service area. "
                    "The coordinate was accepted, but the workbook location should be reviewed."
                ),
            }
        )
        return warning

    if _input_review_is_korea(country) and not _input_review_within_bbox(lat, lng, _INPUT_REVIEW_KOREA_BBOX):
        warning = _address_warning_base(
            point,
            warning_type="region_mismatch",
            reason="Resolved coordinates are outside South Korea.",
        )
        warning.update(
            {
                "expected_country": country,
                "expected_city": city,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "message": (
                    f"{_point_display_address(point) or 'This stop'} resolved outside South Korea. "
                    "The coordinate was accepted, but the workbook location should be reviewed."
                ),
            }
        )
        return warning
    return None


def _median(values: list[float]) -> float | None:
    clean = sorted(value for value in values if value > 0)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2.0


def _route_distance_km(
    planner: Any,
    points_by_node: dict[int, dict[str, Any]],
    distance_matrix: list[list[float]] | None,
    from_node: int,
    to_node: int,
) -> float | None:
    if distance_matrix:
        try:
            value_m = float(distance_matrix[from_node][to_node] or 0.0)
        except (IndexError, TypeError, ValueError):
            value_m = 0.0
        if value_m > 0:
            return value_m / 1000.0
    from_coords = _point_coordinate(points_by_node.get(from_node, {}))
    to_coords = _point_coordinate(points_by_node.get(to_node, {}))
    if from_coords is None or to_coords is None:
        return None
    return float(planner.haversine_distance_km(from_coords[0], from_coords[1], to_coords[0], to_coords[1]))


def _local_xy_km(origin: tuple[float, float], coords: tuple[float, float]) -> tuple[float, float]:
    lat0 = origin[0]
    return (
        (coords[1] - origin[1]) * 111.320 * max(0.1, abs(math.cos(math.radians(lat0)))),
        (coords[0] - origin[0]) * 110.574,
    )


def _point_segment_context_km(
    prev_coords: tuple[float, float],
    stop_coords: tuple[float, float],
    next_coords: tuple[float, float],
) -> tuple[float, float]:
    px, py = _local_xy_km(prev_coords, prev_coords)
    sx, sy = _local_xy_km(prev_coords, stop_coords)
    nx, ny = _local_xy_km(prev_coords, next_coords)
    vx, vy = nx - px, ny - py
    wx, wy = sx - px, sy - py
    denom = vx * vx + vy * vy
    if denom <= 0:
        distance = ((sx - px) ** 2 + (sy - py) ** 2) ** 0.5
        return distance, 0.0
    projection = (wx * vx + wy * vy) / denom
    clamped = min(1.0, max(0.0, projection))
    closest_x = px + clamped * vx
    closest_y = py + clamped * vy
    distance = ((sx - closest_x) ** 2 + (sy - closest_y) ** 2) ** 0.5
    return distance, projection


def _address_warning_base(
    point: dict[str, Any],
    *,
    warning_type: str,
    reason: str,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "type": warning_type,
        "severity": severity,
        "status": "needs_review",
        "accepted": True,
        "address": _point_display_address(point),
        "source_excel_rows": _point_source_rows(point),
        "country": str(point.get("country", point.get("requested_country", ""))).strip(),
        "city": str(point.get("city", point.get("requested_city", ""))).strip(),
        "formatted_address": str(point.get("formatted_address", "") or "").strip(),
        "provider": str(point.get("provider", "") or "").strip(),
        "reason": reason,
        "suggestion": (
            "Review the workbook address and the resolved map address. "
            "If this location is intentional, keep the result; otherwise correct the workbook and rerun."
        ),
    }


def _dedupe_address_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    severity_order = {"critical": 3, "warning": 2, "info": 1}
    for warning in sorted(
        warnings,
        key=lambda item: (
            -severity_order.get(str(item.get("severity", "warning")), 2),
            str(item.get("address", "")),
            str(item.get("type", "")),
        ),
    ):
        key = (
            str(warning.get("type", "")).strip(),
            str(warning.get("address", "")).strip().lower(),
            str(warning.get("route_id", "")).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped


def build_input_address_review(
    planner: Any,
    original_points: list[dict[str, Any]],
    *,
    service_direction: str,
    current_plan_assessment: dict[str, Any] | None = None,
    current_plan_distance_matrix: list[list[float]] | None = None,
    current_plan_routes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    points = [dict(point) for point in list(original_points or [])]
    if len(points) <= 1:
        return {
            "summary": {
                "warning_count": 0,
                "school_distance_warning_count": 0,
                "region_warning_count": 0,
                "route_context_warning_count": 0,
            },
            "warnings": [],
        }

    school_point = next((point for point in points if bool(point.get("is_depot"))), points[0])
    school_coords = _point_coordinate(school_point)
    if school_coords is not None and INPUT_ADDRESS_REVIEW_SCHOOL_DISTANCE_KM > 0:
        for point in points:
            if bool(point.get("is_depot")):
                continue
            point_coords = _point_coordinate(point)
            if point_coords is None:
                continue
            distance_km = float(
                planner.haversine_distance_km(
                    school_coords[0],
                    school_coords[1],
                    point_coords[0],
                    point_coords[1],
                )
            )
            if distance_km <= INPUT_ADDRESS_REVIEW_SCHOOL_DISTANCE_KM:
                continue
            warning = _address_warning_base(
                point,
                warning_type="school_distance",
                reason="Resolved address is unusually far from the school.",
            )
            warning.update(
                {
                    "distance_to_school_km": round(distance_km, 1),
                    "threshold_km": round(INPUT_ADDRESS_REVIEW_SCHOOL_DISTANCE_KM, 1),
                    "message": (
                        f"{_point_display_address(point) or 'This stop'} is {distance_km:.1f} km from the school. "
                        "The coordinate was accepted, but the source address should be reviewed."
                    ),
                }
            )
            warnings.append(warning)

    for point in points:
        if bool(point.get("is_depot")):
            continue
        region_warning = _input_review_region_issue(point)
        if region_warning:
            warnings.append(region_warning)

    if current_plan_assessment:
        point_by_node = {
            int(point.get("node_id", index) or index): point
            for index, point in enumerate(points)
        }
        route_summaries = list(current_plan_assessment.get("route_summaries") or [])
        current_route_leg_distance_km: dict[str, dict[tuple[int, int], float]] = {}
        for route in list(current_plan_routes or []):
            route_id = str(route.get("route_id", "") or "").strip()
            if not route_id:
                continue
            route_distances = current_route_leg_distance_km.setdefault(route_id, {})
            for leg in list(route.get("leg_details") or []):
                try:
                    from_node = int(leg.get("from_node"))
                    to_node = int(leg.get("to_node"))
                except (TypeError, ValueError):
                    continue
                distance_m = leg.get("distance_m")
                if distance_m is None:
                    continue
                try:
                    distance_km = float(distance_m) / 1000.0
                except (TypeError, ValueError):
                    continue
                if distance_km > 0:
                    route_distances[(from_node, to_node)] = distance_km
        for route_summary in route_summaries:
            node_ids = [
                int(node_id)
                for node_id in list(route_summary.get("matched_node_ids") or [])
                if str(node_id).strip()
            ]
            if len(node_ids) < 3:
                continue
            route_id = str(route_summary.get("route_id", "") or "").strip()
            route_leg_distances = current_route_leg_distance_km.get(route_id, {})
            route_leg_lengths = [
                value
                for value in (
                    route_leg_distances.get((from_node, to_node))
                    or _route_distance_km(planner, point_by_node, current_plan_distance_matrix, from_node, to_node)
                    for from_node, to_node in zip(node_ids, node_ids[1:])
                )
                if value is not None and value > 0
            ]
            median_route_leg_km = _median(route_leg_lengths)
            for stop_index in range(1, len(node_ids) - 1):
                node_id = node_ids[stop_index]
                point = point_by_node.get(node_id)
                if not point or bool(point.get("is_depot")):
                    continue
                prev_node = node_ids[stop_index - 1]
                next_node = node_ids[stop_index + 1]
                if prev_node == next_node:
                    continue
                prev_to_stop_km = _route_distance_km(
                    planner,
                    point_by_node,
                    current_plan_distance_matrix,
                    prev_node,
                    node_id,
                )
                prev_to_stop_km = route_leg_distances.get((prev_node, node_id)) or prev_to_stop_km
                stop_to_next_km = _route_distance_km(
                    planner,
                    point_by_node,
                    current_plan_distance_matrix,
                    node_id,
                    next_node,
                )
                stop_to_next_km = route_leg_distances.get((node_id, next_node)) or stop_to_next_km
                prev_to_next_km = _route_distance_km(
                    planner,
                    point_by_node,
                    current_plan_distance_matrix,
                    prev_node,
                    next_node,
                )
                if not prev_to_stop_km or not stop_to_next_km or not prev_to_next_km:
                    continue

                detour_extra_km = max(0.0, prev_to_stop_km + stop_to_next_km - prev_to_next_km)
                detour_ratio = (prev_to_stop_km + stop_to_next_km) / max(prev_to_next_km, 0.001)
                signals: list[tuple[str, str, dict[str, Any]]] = []
                if (
                    detour_extra_km >= INPUT_ADDRESS_REVIEW_DETOUR_EXTRA_KM
                    and detour_ratio >= INPUT_ADDRESS_REVIEW_DETOUR_RATIO
                ):
                    signals.append(
                        (
                            "detour",
                            "Stop creates an unusually large detour between adjacent route stops.",
                            {
                                "detour_extra_km": round(detour_extra_km, 1),
                                "detour_ratio": round(detour_ratio, 2),
                                "threshold_extra_km": round(INPUT_ADDRESS_REVIEW_DETOUR_EXTRA_KM, 1),
                                "threshold_ratio": round(INPUT_ADDRESS_REVIEW_DETOUR_RATIO, 2),
                            },
                        )
                    )

                prev_coords = _point_coordinate(point_by_node.get(prev_node, {}))
                stop_coords = _point_coordinate(point)
                next_coords = _point_coordinate(point_by_node.get(next_node, {}))
                corridor_distance_km: float | None = None
                projection: float | None = None
                if prev_coords is not None and stop_coords is not None and next_coords is not None:
                    corridor_distance_km, projection = _point_segment_context_km(prev_coords, stop_coords, next_coords)
                    if (
                        INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM > 0
                        and corridor_distance_km >= INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM
                        and detour_extra_km >= max(2.0, INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM / 2.0)
                    ):
                        signals.append(
                            (
                                "corridor",
                                "Stop is far from the corridor implied by adjacent route stops.",
                                {
                                    "corridor_distance_km": round(corridor_distance_km, 1),
                                    "threshold_corridor_km": round(INPUT_ADDRESS_REVIEW_CORRIDOR_DISTANCE_KM, 1),
                                    "detour_extra_km": round(detour_extra_km, 1),
                                },
                            )
                        )
                    if (
                        projection is not None
                        and (projection < -0.25 or projection > 1.25)
                        and detour_extra_km >= INPUT_ADDRESS_REVIEW_ORDER_EXTRA_KM
                        and detour_ratio >= INPUT_ADDRESS_REVIEW_ORDER_RATIO
                    ):
                        signals.append(
                            (
                                "reverse_direction",
                                "Stop falls outside the direction implied by adjacent route stops.",
                                {
                                    "projection_on_adjacent_segment": round(projection, 2),
                                    "detour_extra_km": round(detour_extra_km, 1),
                                    "detour_ratio": round(detour_ratio, 2),
                                    "threshold_extra_km": round(INPUT_ADDRESS_REVIEW_ORDER_EXTRA_KM, 1),
                                    "threshold_ratio": round(INPUT_ADDRESS_REVIEW_ORDER_RATIO, 2),
                                },
                            )
                        )

                if median_route_leg_km is not None:
                    isolation_threshold_km = max(
                        INPUT_ADDRESS_REVIEW_ISOLATED_NEIGHBOR_KM,
                        median_route_leg_km * INPUT_ADDRESS_REVIEW_ISOLATED_ROUTE_MULTIPLIER,
                    )
                    nearest_adjacent_km = min(prev_to_stop_km, stop_to_next_km)
                    if nearest_adjacent_km >= isolation_threshold_km:
                        signals.append(
                            (
                                "isolated",
                                "Stop is unusually isolated from both adjacent stops on this route.",
                                {
                                    "nearest_adjacent_km": round(nearest_adjacent_km, 1),
                                    "median_route_leg_km": round(median_route_leg_km, 1),
                                    "isolation_threshold_km": round(isolation_threshold_km, 1),
                                },
                            )
                        )

                for signal_type, reason, details in signals:
                    warning = _address_warning_base(
                        point,
                        warning_type=f"route_context_{signal_type}",
                        reason=reason,
                    )
                    warning.update(
                        {
                            "route_id": route_id,
                            "stop_sequence": stop_index,
                            "previous_address": _point_display_address(point_by_node.get(prev_node, {})),
                            "next_address": _point_display_address(point_by_node.get(next_node, {})),
                            "prev_to_stop_km": round(prev_to_stop_km, 1),
                            "stop_to_next_km": round(stop_to_next_km, 1),
                            "prev_to_next_km": round(prev_to_next_km, 1),
                            **details,
                        }
                    )
                    if signal_type == "detour":
                        warning["message"] = (
                            f"{_point_display_address(point) or 'This stop'} adds about {detour_extra_km:.1f} km "
                            f"between adjacent stops on route {route_id or 'N/A'}."
                        )
                    elif signal_type == "corridor":
                        warning["message"] = (
                            f"{_point_display_address(point) or 'This stop'} is about {corridor_distance_km:.1f} km "
                            f"away from the line between adjacent stops on route {route_id or 'N/A'}."
                        )
                    elif signal_type == "reverse_direction":
                        warning["message"] = (
                            f"{_point_display_address(point) or 'This stop'} appears outside the forward sequence "
                            f"between adjacent stops on route {route_id or 'N/A'}."
                        )
                    else:
                        warning["message"] = (
                            f"{_point_display_address(point) or 'This stop'} is unusually far from both adjacent "
                            f"stops on route {route_id or 'N/A'}."
                        )
                    warnings.append(warning)

    warnings = _dedupe_address_warnings(warnings)
    by_type = Counter(str(warning.get("type", "unknown")) for warning in warnings)
    return {
        "summary": {
            "warning_count": len(warnings),
            "school_distance_warning_count": int(by_type.get("school_distance", 0)),
            "region_warning_count": int(by_type.get("region_mismatch", 0)),
            "route_context_warning_count": sum(
                count for warning_type, count in by_type.items() if warning_type.startswith("route_context_")
            ),
            "service_direction": normalize_service_direction(service_direction),
        },
        "warnings": warnings,
    }



def _normalize_input_records(input_records: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
    normalized_records: list[dict[str, Any]] = []
    for index, item in enumerate(list(input_records)):
        if isinstance(item, str):
            normalized_records.append(
                {
                    "country": "",
                    "city": "",
                    "address": item,
                    "passenger_count": 0 if index == 0 else 1,
                }
            )
        else:
            normalized_records.append(
                {
                    "country": str(item.get("country", "")).strip(),
                    "city": str(item.get("city", "")).strip(),
                    "address": str(item["address"]).strip(),
                    "passenger_count": int(item.get("passenger_count", 0 if index == 0 else 1)),
                }
            )
    return normalized_records


def build_excluded_stops_from_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in warnings:
        address = str(item.get("address", "")).strip()
        if not address or address in seen:
            continue
        excluded.append(
            {
                "address": address,
                "reason": "Could not be geocoded or was not accepted as a valid input stop.",
            }
        )
        seen.add(address)
    return excluded


def _normalize_current_plan(current_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not current_plan:
        return None
    stops = [dict(item) for item in list(current_plan.get("stops") or [])]
    assignments = [dict(item) for item in list(current_plan.get("assignments") or [])]
    fleet = [dict(item) for item in list(current_plan.get("fleet") or [])]
    if not stops or not assignments:
        return None
    return {
        "stops": stops,
        "assignments": assignments,
        "fleet": fleet,
        "summary": dict(current_plan.get("summary") or {}),
        "service_direction": str(current_plan.get("service_direction", "From School") or "From School"),
    }


def _build_bus_capacity_lookup(config: PlannerConfig, current_plan: dict[str, Any] | None = None) -> dict[str, int]:
    lookup = {
        str(config.large_bus_name).strip() or "Large Bus": int(config.large_bus_capacity),
        str(config.mid_bus_name).strip() or "Mid Bus": int(config.mid_bus_capacity),
        str(config.small_bus_name).strip() or "Small Bus": int(config.small_bus_capacity),
    }
    if current_plan:
        for fleet_item in list(current_plan.get("fleet") or []):
            bus_type = str(fleet_item.get("bus_type", "")).strip()
            if not bus_type:
                continue
            try:
                seat_count = int(fleet_item.get("seat_count", 0))
            except (TypeError, ValueError):
                continue
            if seat_count > 0:
                lookup[bus_type] = seat_count
    return lookup


def _make_point_lookup(points: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for point in points:
        key = (
            str(point.get("country", "")).strip().lower(),
            str(point.get("city", "")).strip().lower(),
            str(point.get("address", "")).strip().lower(),
        )
        lookup[key] = point
    return lookup


def _build_assessment_metric_matrices(
    planner: Any,
    points: list[dict[str, Any]],
) -> tuple[list[list[float]], list[list[float]]]:
    if len(points) <= 1:
        return [[0.0]], [[0.0]]

    scenario_osrm_base_url = planner.resolve_osrm_base_url(points)
    previous_osrm_base_url = planner.OSRM_BASE_URL
    planner.OSRM_BASE_URL = scenario_osrm_base_url
    try:
        try:
            planner.log("[BACKEND] Building matrix for current plan assessment.")
            return planner.build_osrm_full_matrix(points)
        except Exception as exc:
            planner.log(
                f"[WARN] Current plan assessment matrix build failed; "
                f"falling back to seed matrix: {exc}"
            )
            return planner.seed_edge_metrics(points)
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url


def _route_metric_cost(node_ids: list[int], metric_matrix: list[list[float]]) -> float:
    return sum(float(metric_matrix[from_node][to_node]) for from_node, to_node in zip(node_ids[:-1], node_ids[1:]))


def _nearest_neighbor_order(start_node: int, candidate_nodes: list[int], metric_matrix: list[list[float]]) -> list[int]:
    remaining = set(candidate_nodes)
    ordered = [start_node]
    current = start_node
    while remaining:
        next_node = min(remaining, key=lambda node: float(metric_matrix[current][node]))
        ordered.append(next_node)
        remaining.remove(next_node)
        current = next_node
    return ordered


def _optimize_route_node_order(route_node_ids: list[int], metric_matrix: list[list[float]]) -> list[int]:
    if len(route_node_ids) <= 2:
        return route_node_ids

    start_node = route_node_ids[0]
    visit_nodes = route_node_ids[1:]
    if len(visit_nodes) <= 8:
        best_order = route_node_ids
        best_cost = _route_metric_cost(best_order, metric_matrix)
        for permutation in itertools.permutations(visit_nodes):
            candidate = [start_node, *permutation]
            candidate_cost = _route_metric_cost(candidate, metric_matrix)
            if candidate_cost < best_cost:
                best_order = candidate
                best_cost = candidate_cost
        return best_order
    return _nearest_neighbor_order(start_node, visit_nodes, metric_matrix)


def assess_current_plan(
    planner: Any,
    current_plan: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    config: PlannerConfig,
    solve_time: list[list[float]] | None = None,
    solve_distance: list[list[float]] | None = None,
    optimize_stop_order: bool = False,
    traffic_calibration: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_current_plan(current_plan)
    if not normalized:
        return None
    service_direction = normalize_service_direction(
        normalized.get("service_direction") or getattr(config, "service_direction", "From School")
    )

    planner.log("[BACKEND] Assessing imported current plan.")
    point_lookup = _make_point_lookup(prepared_original_points)
    bus_capacity_lookup = _build_bus_capacity_lookup(config, normalized)
    if solve_time is None or solve_distance is None:
        solve_time, solve_distance = _build_assessment_metric_matrices(planner, prepared_original_points)
    stop_rows = normalized["stops"]
    assignments = normalized["assignments"]
    route_groups: dict[str, list[dict[str, Any]]] = {}
    stop_id_lookup = {str(item["stop_id"]).strip(): item for item in stop_rows}

    for assignment in assignments:
        route_groups.setdefault(str(assignment["route_id"]).strip(), []).append(assignment)

    route_summaries: list[dict[str, Any]] = []
    total_distance_m = 0.0
    total_duration_s = 0.0
    total_load_factor = 0.0
    bus_mix: dict[str, int] = {}
    recommendation_set: list[str] = []
    low_load_route_count = 0
    overlong_route_count = 0

    for route_id, route_assignments in sorted(route_groups.items()):
        ordered_assignments = sorted(route_assignments, key=lambda item: int(item["stop_sequence"]))
        bus_type = str(ordered_assignments[0].get("bus_type", "")).strip() or "Unknown"
        bus_mix[bus_type] = bus_mix.get(bus_type, 0) + 1
        capacity = int(bus_capacity_lookup.get(bus_type, 0))

        ordered_stops = [stop_id_lookup[str(item["stop_id"]).strip()] for item in ordered_assignments]
        matched_points: list[dict[str, Any]] = []
        for stop in ordered_stops:
            key = (
                str(stop.get("country", "")).strip().lower(),
                str(stop.get("city", "")).strip().lower(),
                str(stop.get("address", "")).strip().lower(),
            )
            matched_point = point_lookup.get(key)
            if matched_point is not None:
                matched_points.append(matched_point)
        if optimize_stop_order and len(matched_points) >= 3:
            terminal_point = next((point for point in matched_points if bool(point.get("is_depot"))), matched_points[0])
            remaining_points = [point for point in matched_points if point is not terminal_point]
            if service_direction == "To School":
                solve_time_working = [list(row) for row in zip(*solve_time)]
            else:
                solve_time_working = solve_time
            optimized_node_ids = _optimize_route_node_order(
                [int(terminal_point.get("node_id", 0)), *[int(point.get("node_id", 0)) for point in remaining_points]],
                solve_time_working,
            )
            point_by_node = {int(point.get("node_id", 0)): point for point in matched_points}
            optimized_points = [point_by_node[node_id] for node_id in optimized_node_ids if node_id in point_by_node]
            matched_points = order_points_for_service_direction(optimized_points, service_direction, optimized=True)
        else:
            matched_points = order_points_for_service_direction(matched_points, service_direction, optimized=False)

        route_distance_m = 0.0
        route_duration_s = 0.0
        if len(matched_points) >= 2:
            for idx in range(len(matched_points) - 1):
                from_node = int(matched_points[idx].get("node_id", idx))
                to_node = int(matched_points[idx + 1].get("node_id", idx + 1))
                route_duration_s += float(solve_time[from_node][to_node])
                route_distance_m += float(solve_distance[from_node][to_node])

        service_stops = [stop for stop in ordered_stops if not bool(stop.get("is_depot"))]
        passenger_count = sum(
            int(stop.get("passenger_count", 0))
            for stop in service_stops
        )
        load_factor = (passenger_count / capacity) if capacity > 0 else 0.0
        total_distance_m += route_distance_m
        total_duration_s += route_duration_s
        total_load_factor += load_factor

        if capacity > 0 and load_factor < 0.5:
            low_load_route_count += 1
            recommendation_set.append(
                f"Route {route_id} uses {bus_type} at only {load_factor * 100:.0f}% load. Consider a smaller vehicle or route merge."
            )
        if route_duration_s > effective_route_duration_limit_minutes(config) * 60:
            overlong_route_count += 1
            recommendation_set.append(
                f"Route {route_id} exceeds the target route duration threshold of {config.max_route_duration_minutes} minutes plus the 10-minute operating buffer."
            )
        if len(service_stops) > 12:
            recommendation_set.append(
                f"Route {route_id} has {len(service_stops)} service stops. Review whether some nearby stops can be consolidated."
            )
        route_summary = {
                "route_id": route_id,
                "bus_type": bus_type,
                "capacity": capacity,
                "stop_count": len(service_stops),
                "service_stop_count": len(service_stops),
                "scheduled_stop_count": len(ordered_stops),
                "depot_stop_count": len(ordered_stops) - len(service_stops),
                "passenger_count": passenger_count,
                "distance_m": route_distance_m,
                "duration_s": route_duration_s,
                "load_factor": load_factor,
                "stop_ids": [str(stop.get("stop_id", "")).strip() for stop in ordered_stops],
                "service_stop_ids": [str(stop.get("stop_id", "")).strip() for stop in service_stops],
                "addresses": [str(stop.get("address", "")).strip() for stop in ordered_stops],
                "service_addresses": [str(stop.get("address", "")).strip() for stop in service_stops],
                "matched_stop_ids": [
                    str(stop.get("stop_id", "")).strip()
                    for stop in ordered_stops
                    if (
                        str(stop.get("country", "")).strip().lower(),
                        str(stop.get("city", "")).strip().lower(),
                        str(stop.get("address", "")).strip().lower(),
                    ) in point_lookup
                ],
                "matched_node_ids": [int(point.get("node_id", 0)) for point in matched_points],
                "matched_addresses": [str(point.get("address", "")).strip() for point in matched_points],
            }
        selected_traffic_stats = _selected_route_traffic_stats(traffic_calibration, route_id)
        if selected_traffic_stats:
            traffic_factor = selected_traffic_stats.get("factor")
            traffic_api_duration_s = selected_traffic_stats.get("amap_duration_s")
            traffic_osrm_duration_s = selected_traffic_stats.get("osrm_duration_s")
            route_summary.update(
                {
                    "traffic_api_duration_s": float(traffic_api_duration_s)
                    if traffic_api_duration_s is not None else None,
                    "traffic_osrm_duration_s": float(traffic_osrm_duration_s)
                    if traffic_osrm_duration_s is not None else None,
                    "traffic_buffer_factor": float(traffic_factor)
                    if traffic_factor is not None else None,
                    "traffic_sample_count": int(selected_traffic_stats.get("sample_count", 0) or 0),
                    "traffic_sampled_leg_count": int(selected_traffic_stats.get("sampled_leg_count", 0) or 0),
                    "traffic_total_leg_count": int(selected_traffic_stats.get("total_leg_count", 0) or 0),
                    "traffic_coverage_complete": bool(selected_traffic_stats.get("coverage_complete")),
                    "traffic_time_source": "AMap peak API current-plan route time",
                }
            )
        route_summaries.append(route_summary)

    route_count = len(route_summaries)
    average_load_factor = (total_load_factor / route_count) if route_count else 0.0
    avg_route_distance_m = (total_distance_m / route_count) if route_count else 0.0
    avg_route_duration_s = (total_duration_s / route_count) if route_count else 0.0
    if route_count >= 2 and average_load_factor < 0.6:
        recommendation_set.append(
            "The imported plan has a low overall average load factor. Review whether some routes can be merged or downsized."
        )

    service_stop_rows = [stop for stop in stop_rows if not bool(stop.get("is_depot"))]
    stop_is_depot = {
        str(stop.get("stop_id", "")).strip(): bool(stop.get("is_depot"))
        for stop in stop_rows
    }
    service_assignments = [
        assignment
        for assignment in assignments
        if not stop_is_depot.get(str(assignment.get("stop_id", "")).strip(), False)
    ]

    return {
        "route_count": route_count,
        "stop_count": len(service_stop_rows),
        "service_stop_count": len(service_stop_rows),
        "scheduled_stop_count": len(stop_rows),
        "depot_stop_count": len(stop_rows) - len(service_stop_rows),
        "assignment_count": len(service_assignments),
        "scheduled_assignment_count": len(assignments),
        "depot_assignment_count": len(assignments) - len(service_assignments),
        "target_route_duration_minutes": int(config.max_route_duration_minutes),
        "effective_route_duration_limit_minutes": int(effective_route_duration_limit_minutes(config)),
        "service_direction": service_direction,
        "bus_mix": bus_mix,
        "total_distance_m": total_distance_m,
        "total_duration_s": total_duration_s,
        "avg_route_distance_m": avg_route_distance_m,
        "avg_route_duration_s": avg_route_duration_s,
        "avg_load_factor": average_load_factor,
        "low_load_route_count": low_load_route_count,
        "overlong_route_count": overlong_route_count,
        "route_summaries": route_summaries,
        "recommendations": recommendation_set,
    }


def build_current_plan_map_scenario(
    planner: Any,
    current_plan_assessment: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
) -> dict[str, Any]:
    if not current_plan_assessment:
        return _build_skipped_scenario_result("No current plan assessment was available for map rendering.")

    points = [dict(point) for point in list(prepared_original_points or [])]
    route_summaries = list(current_plan_assessment.get("route_summaries") or [])
    routes: list[dict[str, Any]] = []
    for route_index, route_summary in enumerate(route_summaries, start=1):
        node_ids = [int(item) for item in list(route_summary.get("matched_node_ids") or [])]
        if not node_ids:
            continue
        routes.append(
            {
                "vehicle_id": route_index,
                "route_id": str(route_summary.get("route_id", "")).strip() or f"Route {route_index}",
                "bus_type_name": str(route_summary.get("bus_type", "")).strip() or "Unknown",
                "bus_capacity": int(route_summary.get("capacity", 0) or 0),
                "nodes": node_ids,
                "time_s": int(float(route_summary.get("duration_s", 0.0) or 0.0)),
                "distance_m": int(float(route_summary.get("distance_m", 0.0) or 0.0)),
                "load": int(route_summary.get("passenger_count", 0) or 0),
                "leg_details": [],
                "traffic_api_duration_s": route_summary.get("traffic_api_duration_s"),
                "traffic_osrm_duration_s": route_summary.get("traffic_osrm_duration_s"),
                "traffic_buffer_factor": route_summary.get("traffic_buffer_factor"),
                "traffic_sampled_leg_count": route_summary.get("traffic_sampled_leg_count"),
                "traffic_total_leg_count": route_summary.get("traffic_total_leg_count"),
                "traffic_coverage_complete": bool(route_summary.get("traffic_coverage_complete", False)),
                "traffic_time_source": route_summary.get("traffic_time_source"),
            }
        )

    if routes and len(points) > 1:
        scenario_osrm_base_url = planner.resolve_osrm_base_url(points)
        previous_osrm_base_url = planner.OSRM_BASE_URL
        planner.OSRM_BASE_URL = scenario_osrm_base_url
        try:
            planner.enrich_routes_with_actual_driving(points, routes)
            planner.annotate_and_price_routes(points, routes)
        finally:
            planner.OSRM_BASE_URL = previous_osrm_base_url

    result = {
        "points": points,
        "routes": routes,
        "output_html": "",
        "bus_count": len(routes),
        "stop_count": int(current_plan_assessment.get("service_stop_count", current_plan_assessment.get("stop_count", 0)) or 0),
        "service_stop_count": int(current_plan_assessment.get("service_stop_count", current_plan_assessment.get("stop_count", 0)) or 0),
        "map_point_count": len(points),
        "bus_mix": dict(current_plan_assessment.get("bus_mix", {})),
        "enabled": True,
        "avg_route_distance_m": float(current_plan_assessment.get("avg_route_distance_m", 0.0) or 0.0),
        "avg_route_duration_s": float(current_plan_assessment.get("avg_route_duration_s", 0.0) or 0.0),
    }
    return result


def compare_current_plan_to_like_for_like_baseline(
    current_plan_assessment: dict[str, Any] | None,
    like_for_like_baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return compare_current_plan_to_assessment_baseline(
        current_plan_assessment,
        like_for_like_baseline,
        baseline_name="like-for-like baseline",
        optimization_phrase="route-order optimization alone",
    )


def compare_current_plan_to_assessment_baseline(
    current_plan_assessment: dict[str, Any] | None,
    baseline_assessment: dict[str, Any] | None,
    *,
    baseline_name: str,
    optimization_phrase: str,
    constrained_package_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not current_plan_assessment or not baseline_assessment:
        return None

    current_route_count = int(current_plan_assessment.get("route_count", 0))
    current_avg_route_distance_m = float(current_plan_assessment.get("avg_route_distance_m", 0.0))
    current_avg_route_duration_s = float(current_plan_assessment.get("avg_route_duration_s", 0.0))
    current_avg_load_factor = float(current_plan_assessment.get("avg_load_factor", 0.0))

    baseline_route_count = int(baseline_assessment.get("route_count", 0))
    baseline_avg_route_distance_m = float(baseline_assessment.get("avg_route_distance_m", 0.0))
    baseline_avg_route_duration_s = float(baseline_assessment.get("avg_route_duration_s", 0.0))
    baseline_avg_load_factor = float(baseline_assessment.get("avg_load_factor", 0.0))
    baseline_bus_mix = dict(baseline_assessment.get("bus_mix", {}))

    route_gap = current_route_count - baseline_route_count
    avg_distance_gap_m = current_avg_route_distance_m - baseline_avg_route_distance_m
    avg_duration_gap_s = current_avg_route_duration_s - baseline_avg_route_duration_s
    avg_distance_gap_pct = (
        avg_distance_gap_m / baseline_avg_route_distance_m * 100.0
    ) if baseline_avg_route_distance_m else 0.0
    avg_duration_gap_pct = (
        avg_duration_gap_s / baseline_avg_route_duration_s * 100.0
    ) if baseline_avg_route_duration_s else 0.0

    recommendations: list[str] = []
    if abs(route_gap) > 0:
        recommendations.append(
            f"The current plan route count differs from the {baseline_name} by {route_gap:+d}."
        )
    if avg_distance_gap_pct > 5:
        recommendations.append(
            f"Compared with the {baseline_name}, {optimization_phrase} can reduce average route distance by about {avg_distance_gap_pct:.1f}%."
        )
    if avg_duration_gap_pct > 5:
        recommendations.append(
            f"Compared with the {baseline_name}, {optimization_phrase} can reduce average route time by about {avg_duration_gap_pct:.1f}%."
        )
    if abs(current_avg_load_factor - baseline_avg_load_factor) < 1e-9:
        recommendations.append(
            f"Load factor remains unchanged in the {baseline_name}, which confirms that this comparison isolates network design quality rather than vehicle capacity."
        )

    constrained_package_summaries = list(constrained_package_summaries or [])
    if constrained_package_summaries:
        removable_now = [
            item for item in constrained_package_summaries
            if bool(item.get("route_eliminated"))
        ]
        removal_paths = [
            item for item in constrained_package_summaries
            if not bool(item.get("route_eliminated")) and bool(item.get("route_removal_candidate"))
        ]
        consolidation_paths = [
            item for item in constrained_package_summaries
            if not bool(item.get("route_eliminated"))
            and not bool(item.get("route_removal_candidate"))
            and bool(item.get("route_consolidation_candidate"))
        ]
        if removable_now:
            recommendations.append(
                f"The constrained-improvement baseline includes {len(removable_now)} package(s) that fully empty a route, creating immediate route-removal candidates."
            )
        elif removal_paths:
            recommendations.append(
                f"The constrained-improvement baseline includes {len(removal_paths)} package(s) that leave a route with very limited residual demand, creating a strong removal path."
            )
        elif consolidation_paths:
            recommendations.append(
                f"The constrained-improvement baseline includes {len(consolidation_paths)} package(s) that move a route materially closer to consolidation."
            )

        top_package = constrained_package_summaries[0]
        top_package_summary = str(top_package.get("package_summary", "")).strip()
        if top_package_summary:
            recommendations.append(top_package_summary)
        projected_to_duration_min = float(top_package.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0
        projected_to_load_pct = float(top_package.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0
        target_minutes = float(baseline_assessment.get("target_route_duration_minutes", 60.0) or 60.0)
        if projected_to_duration_min <= target_minutes and projected_to_load_pct <= 85.0:
            recommendations.append(
                f"The leading constrained package is a practical merge candidate because the receiving route still lands near {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load."
            )
        elif projected_to_duration_min <= target_minutes * 1.1 and projected_to_load_pct <= 92.0:
            recommendations.append(
                f"The leading constrained package is feasible but tight because the receiving route rises to about {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load."
            )
        else:
            recommendations.append(
                f"The leading constrained package is not yet a clean merge because the receiving route would be pushed to about {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load."
            )

    return {
        "baseline_name": baseline_name,
        "current_route_count": current_route_count,
        "baseline_route_count": baseline_route_count,
        "route_gap": route_gap,
        "current_avg_route_distance_m": current_avg_route_distance_m,
        "baseline_avg_route_distance_m": baseline_avg_route_distance_m,
        "avg_distance_gap_m": avg_distance_gap_m,
        "avg_distance_gap_pct": avg_distance_gap_pct,
        "current_avg_route_duration_s": current_avg_route_duration_s,
        "baseline_avg_route_duration_s": baseline_avg_route_duration_s,
        "target_route_duration_minutes": float(baseline_assessment.get("target_route_duration_minutes", 0.0) or 0.0),
        "avg_duration_gap_s": avg_duration_gap_s,
        "avg_duration_gap_pct": avg_duration_gap_pct,
        "current_avg_load_factor": current_avg_load_factor,
        "baseline_avg_load_factor": baseline_avg_load_factor,
        "current_bus_mix": dict(current_plan_assessment.get("bus_mix", {})),
        "baseline_bus_mix": baseline_bus_mix,
        "recommendations": recommendations,
    }


def _extract_current_plan_route_rows(current_plan: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    normalized = _normalize_current_plan(current_plan)
    if not normalized:
        return {}

    stop_lookup = {
        str(item.get("stop_id", "")).strip(): dict(item)
        for item in list(normalized.get("stops") or [])
    }
    route_groups: dict[str, list[dict[str, Any]]] = {}
    for assignment in list(normalized.get("assignments") or []):
        route_id = str(assignment.get("route_id", "")).strip()
        stop_id = str(assignment.get("stop_id", "")).strip()
        if not route_id or not stop_id or stop_id not in stop_lookup:
            continue
        route_groups.setdefault(route_id, []).append(
            {
                "route_id": route_id,
                "stop_id": stop_id,
                "stop_sequence": int(assignment.get("stop_sequence", 0) or 0),
                "bus_type": str(assignment.get("bus_type", "")).strip(),
                **dict(stop_lookup[stop_id]),
            }
        )
    for route_id, rows in route_groups.items():
        route_groups[route_id] = sorted(rows, key=lambda item: int(item.get("stop_sequence", 0) or 0))
    return route_groups


def _rebuild_current_plan_from_route_rows(
    route_rows_by_id: dict[str, list[dict[str, Any]]],
    fleet: list[dict[str, Any]],
    service_direction: str = "From School",
) -> dict[str, Any]:
    stops: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    route_ids = sorted(route_id for route_id, rows in route_rows_by_id.items() if rows)
    bus_types: set[str] = set()

    normalized_direction = normalize_service_direction(service_direction)
    for route_id in route_ids:
        rows = route_rows_by_id[route_id]
        if not rows:
            continue
        route_bus_type = str(rows[0].get("bus_type", "")).strip()
        if route_bus_type:
            bus_types.add(route_bus_type)
        terminal_idx = route_terminal_index(rows, normalized_direction)
        for index, row in enumerate(rows, start=1):
            stop_id = f"{route_id}__{index}"
            is_depot = (index - 1) == terminal_idx
            stop_item = {
                "stop_id": stop_id,
                "route_id": route_id,
                "stop_sequence": index,
                "country": str(row.get("country", "")).strip(),
                "city": str(row.get("city", "")).strip(),
                "address": str(row.get("address", "")).strip(),
                "passenger_count": int(row.get("passenger_count", 0) or 0),
                "is_depot": is_depot,
            }
            assignment_item = {
                "route_id": route_id,
                "stop_id": stop_id,
                "stop_sequence": index,
                "bus_type": route_bus_type,
            }
            stops.append(stop_item)
            assignments.append(assignment_item)

    service_stops = [item for item in stops if not bool(item.get("is_depot"))]
    stop_is_depot = {
        str(item.get("stop_id", "")).strip(): bool(item.get("is_depot"))
        for item in stops
    }
    service_assignments = [
        item
        for item in assignments
        if not stop_is_depot.get(str(item.get("stop_id", "")).strip(), False)
    ]

    return {
        "stops": stops,
        "assignments": assignments,
        "fleet": [dict(item) for item in list(fleet or [])],
        "service_direction": normalized_direction,
        "summary": {
            "stop_count": len(service_stops),
            "service_stop_count": len(service_stops),
            "scheduled_stop_count": len(stops),
            "depot_stop_count": len(stops) - len(service_stops),
            "assignment_count": len(service_assignments),
            "scheduled_assignment_count": len(assignments),
            "depot_assignment_count": len(assignments) - len(service_assignments),
            "route_count": len(route_ids),
            "service_direction": normalized_direction,
            "bus_types": sorted(bus_types),
            "route_ids": route_ids,
            "fleet_count": len(list(fleet or [])),
        },
    }


def _select_constrained_improvement_moves(
    recommendations: list[dict[str, Any]],
    route_opportunity_profiles: list[dict[str, Any]] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    route_opportunity_profiles = list(route_opportunity_profiles or [])
    selected: list[dict[str, Any]] = []
    touched_routes: set[str] = set()
    touched_stop_ids: set[str] = set()
    recommendations_by_from_route: dict[str, list[dict[str, Any]]] = {}
    for item in recommendations:
        from_route_id = str(item.get("from_route_id", "")).strip()
        if not from_route_id:
            continue
        recommendations_by_from_route.setdefault(from_route_id, []).append(item)

    ordered_profiles = sorted(
        route_opportunity_profiles,
        key=lambda item: (
            -(
                3 if str(item.get("route_action_stage", "")).strip() == "route_removable_now"
                else 2 if str(item.get("route_action_stage", "")).strip() == "route_removal_path"
                else 1 if str(item.get("route_action_stage", "")).strip() == "route_consolidation_path"
                else 0
            ),
            -int(item.get("supporting_stage_move_count", 0) or 0),
            -int(item.get("supporting_move_count", 0) or 0),
            -float(item.get("best_network_time_saving_s", 0.0) or 0.0),
            -float(item.get("best_network_distance_saving_m", 0.0) or 0.0),
        ),
    )

    def try_add(item: dict[str, Any]) -> bool:
        from_route_id = str(item.get("from_route_id", "")).strip()
        to_route_id = str(item.get("to_route_id", "")).strip()
        stop_ids = [str(stop_id).strip() for stop_id in list(item.get("stop_ids") or []) if str(stop_id).strip()]
        if not from_route_id or not to_route_id or not stop_ids:
            return False
        if from_route_id in touched_routes or to_route_id in touched_routes:
            return False
        if any(stop_id in touched_stop_ids for stop_id in stop_ids):
            return False
        selected.append(item)
        touched_routes.add(from_route_id)
        touched_routes.add(to_route_id)
        touched_stop_ids.update(stop_ids)
        return True

    def try_add_package(items: list[dict[str, Any]]) -> bool:
        if not items:
            return False
        normalized_items: list[dict[str, Any]] = []
        package_stop_ids: set[str] = set()
        package_routes: set[str] = set()
        for item in items:
            from_route_id = str(item.get("from_route_id", "")).strip()
            to_route_id = str(item.get("to_route_id", "")).strip()
            stop_ids = [str(stop_id).strip() for stop_id in list(item.get("stop_ids") or []) if str(stop_id).strip()]
            if not from_route_id or not to_route_id or not stop_ids:
                return False
            if from_route_id in touched_routes or to_route_id in touched_routes:
                return False
            if any(stop_id in touched_stop_ids or stop_id in package_stop_ids for stop_id in stop_ids):
                return False
            package_routes.add(from_route_id)
            package_routes.add(to_route_id)
            package_stop_ids.update(stop_ids)
            normalized_items.append(item)

        if len(selected) + len(normalized_items) > limit:
            return False

        selected.extend(normalized_items)
        touched_routes.update(package_routes)
        touched_stop_ids.update(package_stop_ids)
        return True

    def build_route_package(profile: dict[str, Any]) -> list[dict[str, Any]]:
        route_id = str(profile.get("route_id", "")).strip()
        if not route_id:
            return []
        route_stage = str(profile.get("route_action_stage", "")).strip()
        if route_stage not in {"route_removal_path", "route_removable_now", "route_consolidation_path"}:
            return []
        if int(profile.get("supporting_stage_move_count", 0) or 0) < 2:
            return []

        preferred_to_route_id = str(profile.get("best_to_route_id", "")).strip()
        max_package_size = 2 if route_stage == "route_consolidation_path" else 3
        route_candidates = sorted(recommendations_by_from_route.get(route_id, []), key=_reallocation_sort_key)
        if not route_candidates:
            return []

        grouped_by_to_route: dict[str, list[dict[str, Any]]] = {}
        for candidate in route_candidates:
            candidate_to_route_id = str(candidate.get("to_route_id", "")).strip()
            if not candidate_to_route_id:
                continue
            grouped_by_to_route.setdefault(candidate_to_route_id, []).append(candidate)

        def build_candidate_package(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            package: list[dict[str, Any]] = []
            package_stop_ids: set[str] = set()
            for candidate in items:
                stop_ids = [
                    str(stop_id).strip()
                    for stop_id in list(candidate.get("stop_ids") or [])
                    if str(stop_id).strip()
                ]
                if any(stop_id in package_stop_ids for stop_id in stop_ids):
                    continue
                package.append(candidate)
                package_stop_ids.update(stop_ids)
                if len(package) >= max_package_size:
                    break
            return package if len(package) >= 2 else []

        def score_candidate_package(package: list[dict[str, Any]], receiving_route_id: str) -> tuple[float, float, float, float, int, int, int]:
            total_time_saving_s = sum(float(item.get("network_total_duration_saving_s", 0.0) or 0.0) for item in package)
            total_distance_saving_m = sum(float(item.get("network_total_distance_saving_m", 0.0) or 0.0) for item in package)
            total_score = sum(float(item.get("score", 0.0) or 0.0) for item in package)
            stop_count = sum(len(list(item.get("stop_ids") or [])) for item in package)
            receiving_route_support = sum(
                1 for item in route_candidates if str(item.get("to_route_id", "")).strip() == receiving_route_id
            )
            preferred_bonus = 1 if preferred_to_route_id and receiving_route_id == preferred_to_route_id else 0
            return (
                total_score,
                total_time_saving_s,
                total_distance_saving_m,
                float(stop_count),
                len(package),
                receiving_route_support,
                preferred_bonus,
            )

        package_options: list[tuple[tuple[float, float, float, float, int, int, int], list[dict[str, Any]]]] = []
        for receiving_route_id, grouped_candidates in grouped_by_to_route.items():
            candidate_package = build_candidate_package(grouped_candidates)
            if candidate_package:
                package_options.append(
                    (score_candidate_package(candidate_package, receiving_route_id), candidate_package)
                )

        if not package_options:
            return []

        package_options.sort(
            key=lambda item: (
                -item[0][0],
                -item[0][1],
                -item[0][2],
                -item[0][3],
                -item[0][4],
                -item[0][5],
                -item[0][6],
            )
        )
        return package_options[0][1]

    for profile in ordered_profiles:
        if len(selected) >= limit:
            break
        route_id = str(profile.get("route_id", "")).strip()
        preferred_to_route_id = str(profile.get("best_to_route_id", "")).strip()
        route_package = build_route_package(profile)
        if route_package and try_add_package(route_package):
            continue
        route_candidates = sorted(recommendations_by_from_route.get(route_id, []), key=_reallocation_sort_key)
        if preferred_to_route_id:
            route_candidates = sorted(
                route_candidates,
                key=lambda item: (
                    0 if str(item.get("to_route_id", "")).strip() == preferred_to_route_id else 1,
                    *_reallocation_sort_key(item),
                ),
            )
        for candidate in route_candidates:
            if try_add(candidate):
                break

    for item in sorted(recommendations, key=_reallocation_sort_key):
        from_route_id = str(item.get("from_route_id", "")).strip()
        if from_route_id and from_route_id in touched_routes:
            continue
        if try_add(item):
            pass
        if len(selected) >= limit:
            break
    return selected


def _annotate_constrained_move_packages(selected_moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    package_keys: dict[tuple[str, str], int] = {}
    annotated: list[dict[str, Any]] = []
    next_package_index = 1
    for item in selected_moves:
        from_route_id = str(item.get("from_route_id", "")).strip()
        to_route_id = str(item.get("to_route_id", "")).strip()
        package_key = (from_route_id, to_route_id)
        if package_key not in package_keys:
            package_keys[package_key] = next_package_index
            next_package_index += 1
        annotated_item = dict(item)
        annotated_item["constrained_package_id"] = f"P{package_keys[package_key]}"
        annotated.append(annotated_item)
    return annotated


def _summarize_constrained_move_packages(
    selected_moves: list[dict[str, Any]],
    current_plan: dict[str, Any] | None,
    config: PlannerConfig,
) -> list[dict[str, Any]]:
    normalized = _normalize_current_plan(current_plan)
    if not normalized or not selected_moves:
        return []

    route_rows_by_id = _extract_current_plan_route_rows(normalized)
    if not route_rows_by_id:
        return []

    bus_capacity_lookup = _build_bus_capacity_lookup(config, normalized)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in selected_moves:
        package_id = str(item.get("constrained_package_id", "")).strip()
        if not package_id:
            continue
        grouped.setdefault(package_id, []).append(item)

    package_summaries: list[dict[str, Any]] = []
    label_lookup = {
        "route_removable_now": "Route removable now",
        "route_removal_path": "Strong removal path",
        "route_consolidation_path": "Consolidation path",
        "local_improvement": "Local improvement",
    }

    for package_id, items in sorted(grouped.items()):
        first = items[0]
        from_route_id = str(first.get("from_route_id", "")).strip()
        to_route_id = str(first.get("to_route_id", "")).strip()
        if not from_route_id or not to_route_id:
            continue
        from_rows = list(route_rows_by_id.get(from_route_id) or [])
        to_rows = list(route_rows_by_id.get(to_route_id) or [])
        if len(from_rows) <= 1 or not to_rows:
            continue

        service_direction = normalize_service_direction(normalized.get("service_direction") or config.service_direction)
        from_terminal, original_from_service_rows = split_route_terminal_rows(from_rows, service_direction)
        from_bus_type = str(from_terminal.get("bus_type", "")).strip()
        from_capacity = int(bus_capacity_lookup.get(from_bus_type, 0) or 0)
        original_from_stop_count = len(original_from_service_rows)
        original_from_passenger_count = sum(int(row.get("passenger_count", 0) or 0) for row in original_from_service_rows)

        package_stop_ids: list[str] = []
        package_addresses: list[str] = []
        moved_passenger_count = 0
        total_time_saving_s = 0.0
        total_distance_saving_m = 0.0
        transfer_distances_m: list[float] = []
        strongest_move_stage = "local_improvement"
        stage_priority = {
            "local_improvement": 0,
            "route_consolidation_path": 1,
            "route_removal_path": 2,
            "route_removable_now": 3,
        }

        seen_stop_ids: set[str] = set()
        for item in items:
            for stop_id in list(item.get("stop_ids") or []):
                normalized_stop_id = str(stop_id).strip()
                if normalized_stop_id and normalized_stop_id not in seen_stop_ids:
                    seen_stop_ids.add(normalized_stop_id)
                    package_stop_ids.append(normalized_stop_id)
            for address in list(item.get("addresses") or []):
                normalized_address = str(address).strip()
                if normalized_address:
                    package_addresses.append(normalized_address)
            moved_passenger_count += int(item.get("moved_passenger_count", 0) or 0)
            total_time_saving_s += float(item.get("network_total_duration_saving_s", 0.0) or 0.0)
            total_distance_saving_m += float(item.get("network_total_distance_saving_m", 0.0) or 0.0)
            transfer_distance_m = float(item.get("transfer_to_route_min_distance_m", 0.0) or 0.0)
            if transfer_distance_m > 0:
                transfer_distances_m.append(transfer_distance_m)
            move_stage = str(item.get("route_action_stage", "")).strip() or "local_improvement"
            if stage_priority.get(move_stage, 0) > stage_priority.get(strongest_move_stage, 0):
                strongest_move_stage = move_stage

        remaining_from_rows = [
            row for row in original_from_service_rows
            if str(row.get("stop_id", "")).strip() not in seen_stop_ids
        ]
        remaining_from_stop_count = len(remaining_from_rows)
        remaining_from_passenger_count = sum(int(row.get("passenger_count", 0) or 0) for row in remaining_from_rows)
        moved_stop_share = len(seen_stop_ids) / max(1, original_from_stop_count)
        moved_passenger_share = moved_passenger_count / max(1, original_from_passenger_count)
        package_transfer_distance_m = min(transfer_distances_m) if transfer_distances_m else None
        classification_transfer_distance_m = (
            package_transfer_distance_m
            if package_transfer_distance_m is not None
            else float("inf")
        )

        (
            package_action_stage,
            package_action_label,
            route_removal_candidate,
            route_eliminated,
            route_consolidation_candidate,
        ) = _classify_route_action_stage(
            projected_from_non_depot_count=remaining_from_stop_count,
            projected_from_passenger_count=remaining_from_passenger_count,
            projected_from_capacity=from_capacity,
            moved_stop_share=moved_stop_share,
            moved_passenger_share=moved_passenger_share,
            network_total_distance_saving_m=total_distance_saving_m,
            network_total_duration_saving_s=total_time_saving_s,
            transfer_to_route_min_distance_m=classification_transfer_distance_m,
        )

        package_summary_parts = [
            f"{package_id} moves {len(seen_stop_ids)} stop(s) from {from_route_id} to {to_route_id}.",
        ]
        if total_time_saving_s > 0:
            package_summary_parts.append(
                f"Estimated network time saving is {total_time_saving_s / 60.0:.1f} minutes."
            )
        if total_distance_saving_m > 0:
            package_summary_parts.append(
                f"Estimated network distance saving is {total_distance_saving_m / 1000.0:.1f} km."
            )
        if route_eliminated:
            package_summary_parts.append(f"{from_route_id} would become empty after this package.")
        elif route_removal_candidate:
            package_summary_parts.append(
                f"{from_route_id} would be reduced to {remaining_from_stop_count} stop(s) and "
                f"{remaining_from_passenger_count} rider(s), creating a strong removal path."
            )
        elif route_consolidation_candidate:
            package_summary_parts.append(
                f"{from_route_id} would be reduced to {remaining_from_stop_count} stop(s) and "
                f"{remaining_from_passenger_count} rider(s), making consolidation more realistic."
            )
        else:
            package_summary_parts.append(
                f"{from_route_id} would still retain {remaining_from_stop_count} stop(s) and "
                f"{remaining_from_passenger_count} rider(s), so this remains a local improvement package."
            )

        package_summaries.append(
            {
                "package_id": package_id,
                "from_route_id": from_route_id,
                "to_route_id": to_route_id,
                "move_count": len(items),
                "stop_ids": package_stop_ids,
                "addresses": package_addresses,
                "moved_stop_count": len(seen_stop_ids),
                "moved_passenger_count": moved_passenger_count,
                "network_total_duration_saving_s": total_time_saving_s,
                "network_total_distance_saving_m": total_distance_saving_m,
                "package_action_stage": package_action_stage,
                "package_action_label": package_action_label,
                "strongest_move_stage": strongest_move_stage,
                "strongest_move_label": label_lookup.get(strongest_move_stage, "Local improvement"),
                "remaining_from_route_stop_count": remaining_from_stop_count,
                "remaining_from_route_passenger_count": remaining_from_passenger_count,
                "original_from_route_stop_count": original_from_stop_count,
                "original_from_route_passenger_count": original_from_passenger_count,
                "moved_stop_share": moved_stop_share,
                "moved_passenger_share": moved_passenger_share,
                "route_removal_candidate": route_removal_candidate,
                "route_eliminated": route_eliminated,
                "route_consolidation_candidate": route_consolidation_candidate,
                "package_transfer_distance_m": package_transfer_distance_m,
                "package_summary": " ".join(package_summary_parts),
            }
        )

    package_summaries.sort(
        key=lambda item: (
            -(3 if bool(item.get("route_eliminated")) else 0),
            -(2 if bool(item.get("route_removal_candidate")) else 0),
            -(1 if bool(item.get("route_consolidation_candidate")) else 0),
            -float(item.get("network_total_duration_saving_s", 0.0) or 0.0),
            -float(item.get("network_total_distance_saving_m", 0.0) or 0.0),
        )
    )
    return package_summaries


def _enrich_constrained_package_summaries(
    package_summaries: list[dict[str, Any]],
    current_plan_assessment: dict[str, Any] | None,
    constrained_improvement_baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not package_summaries:
        return []

    current_route_lookup = {
        str(item.get("route_id", "")).strip(): dict(item)
        for item in list((current_plan_assessment or {}).get("route_summaries") or [])
        if str(item.get("route_id", "")).strip()
    }
    constrained_route_lookup = {
        str(item.get("route_id", "")).strip(): dict(item)
        for item in list((constrained_improvement_baseline or {}).get("route_summaries") or [])
        if str(item.get("route_id", "")).strip()
    }

    enriched: list[dict[str, Any]] = []
    for item in package_summaries:
        package = dict(item)
        from_route_id = str(package.get("from_route_id", "")).strip()
        to_route_id = str(package.get("to_route_id", "")).strip()
        current_from = dict(current_route_lookup.get(from_route_id) or {})
        current_to = dict(current_route_lookup.get(to_route_id) or {})
        constrained_from = dict(constrained_route_lookup.get(from_route_id) or {})
        constrained_to = dict(constrained_route_lookup.get(to_route_id) or {})

        package["original_to_route_stop_count"] = int(current_to.get("stop_count", 0) or 0)
        package["original_to_route_passenger_count"] = int(current_to.get("passenger_count", 0) or 0)
        package["original_to_route_load_factor"] = float(current_to.get("load_factor", 0.0) or 0.0)
        package["original_to_route_duration_s"] = float(current_to.get("duration_s", 0.0) or 0.0)
        package["original_to_route_distance_m"] = float(current_to.get("distance_m", 0.0) or 0.0)
        package["original_from_route_duration_s"] = float(current_from.get("duration_s", 0.0) or 0.0)
        package["original_from_route_distance_m"] = float(current_from.get("distance_m", 0.0) or 0.0)
        package["original_from_route_load_factor"] = float(current_from.get("load_factor", 0.0) or 0.0)

        package["projected_from_route_duration_s"] = float(constrained_from.get("duration_s", 0.0) or 0.0)
        package["projected_from_route_distance_m"] = float(constrained_from.get("distance_m", 0.0) or 0.0)
        package["projected_from_route_load_factor"] = float(constrained_from.get("load_factor", 0.0) or 0.0)

        package["projected_to_route_stop_count"] = int(constrained_to.get("stop_count", 0) or 0)
        package["projected_to_route_passenger_count"] = int(constrained_to.get("passenger_count", 0) or 0)
        package["projected_to_route_load_factor"] = float(constrained_to.get("load_factor", 0.0) or 0.0)
        package["projected_to_route_duration_s"] = float(constrained_to.get("duration_s", 0.0) or 0.0)
        package["projected_to_route_distance_m"] = float(constrained_to.get("distance_m", 0.0) or 0.0)

        projected_to_stop_count = int(package.get("projected_to_route_stop_count", 0) or 0)
        projected_to_passenger_count = int(package.get("projected_to_route_passenger_count", 0) or 0)
        projected_to_duration_s = float(package.get("projected_to_route_duration_s", 0.0) or 0.0)
        projected_to_load_factor = float(package.get("projected_to_route_load_factor", 0.0) or 0.0)
        projected_to_distance_m = float(package.get("projected_to_route_distance_m", 0.0) or 0.0)

        summary_parts = [str(package.get("package_summary", "")).strip()]
        if to_route_id and projected_to_stop_count > 0:
            summary_parts.append(
                f"After the package, {to_route_id} would run with {projected_to_stop_count} stop(s), "
                f"{projected_to_passenger_count} rider(s), about {projected_to_duration_s / 60.0:.1f} minutes, "
                f"about {projected_to_distance_m / 1000.0:.1f} km, and a load factor near {projected_to_load_factor * 100.0:.1f}%."
            )
        package["package_summary"] = " ".join(part for part in summary_parts if part)
        enriched.append(package)

    return enriched


def build_constrained_improvement_current_plan(
    current_plan: dict[str, Any] | None,
    route_reallocation_analysis: dict[str, Any] | None,
    config: PlannerConfig,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = _normalize_current_plan(current_plan)
    if not normalized or not route_reallocation_analysis:
        return None, [], []
    service_direction = normalize_service_direction(normalized.get("service_direction") or config.service_direction)

    recommendations = list(route_reallocation_analysis.get("recommendations") or [])
    route_opportunity_profiles = list(route_reallocation_analysis.get("route_opportunity_profiles") or [])
    selected_moves = _select_constrained_improvement_moves(
        recommendations,
        route_opportunity_profiles=route_opportunity_profiles,
        limit=3,
    )
    selected_moves = _annotate_constrained_move_packages(selected_moves)
    if not selected_moves:
        return None, [], []

    route_rows_by_id = _extract_current_plan_route_rows(normalized)
    if not route_rows_by_id:
        return None, [], []

    for move in selected_moves:
        from_route_id = str(move.get("from_route_id", "")).strip()
        to_route_id = str(move.get("to_route_id", "")).strip()
        transfer_stop_ids = {
            str(stop_id).strip()
            for stop_id in list(move.get("stop_ids") or [])
            if str(stop_id).strip()
        }
        if from_route_id not in route_rows_by_id or to_route_id not in route_rows_by_id:
            continue

        from_rows = list(route_rows_by_id[from_route_id])
        to_rows = list(route_rows_by_id[to_route_id])
        if not from_rows or not to_rows:
            continue

        from_terminal, from_non_terminal = split_route_terminal_rows(from_rows, service_direction)
        to_terminal, to_non_terminal = split_route_terminal_rows(to_rows, service_direction)
        moved_rows = [dict(row) for row in from_non_terminal if str(row.get("stop_id", "")).strip() in transfer_stop_ids]
        remaining_from_rows = [dict(row) for row in from_non_terminal if str(row.get("stop_id", "")).strip() not in transfer_stop_ids]
        if not moved_rows:
            continue

        for row in moved_rows:
            row["route_id"] = to_route_id
            row["bus_type"] = str(to_terminal.get("bus_type", "")).strip()

        if service_direction == "To School":
            route_rows_by_id[from_route_id] = [*remaining_from_rows, dict(from_terminal)]
            route_rows_by_id[to_route_id] = [*to_non_terminal, *moved_rows, dict(to_terminal)]
        else:
            route_rows_by_id[from_route_id] = [dict(from_terminal), *remaining_from_rows]
            route_rows_by_id[to_route_id] = [dict(to_terminal), *to_non_terminal, *moved_rows]

        if bool(move.get("route_eliminated")) and len(route_rows_by_id[from_route_id]) <= 1:
            route_rows_by_id.pop(from_route_id, None)

    constrained_plan = _rebuild_current_plan_from_route_rows(
        route_rows_by_id,
        list(normalized.get("fleet") or []),
        service_direction=service_direction,
    )
    package_summaries = _summarize_constrained_move_packages(selected_moves, normalized, config)
    return constrained_plan, selected_moves, package_summaries


def compare_current_plan_to_baseline(
    current_plan_assessment: dict[str, Any] | None,
    baseline_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not current_plan_assessment or not baseline_result:
        return None

    current_route_count = int(current_plan_assessment.get("route_count", 0))
    current_avg_route_distance_m = float(current_plan_assessment.get("avg_route_distance_m", 0.0))
    current_avg_route_duration_s = float(current_plan_assessment.get("avg_route_duration_s", 0.0))
    current_avg_load_factor = float(current_plan_assessment.get("avg_load_factor", 0.0))

    baseline_route_count = int(baseline_result.get("bus_count", 0))
    baseline_avg_route_distance_m = float(baseline_result.get("avg_route_distance_m", 0.0))
    baseline_avg_route_duration_s = float(baseline_result.get("avg_route_duration_s", 0.0))
    baseline_bus_mix = dict(baseline_result.get("bus_mix", {}))

    route_gap = current_route_count - baseline_route_count
    avg_distance_gap_m = current_avg_route_distance_m - baseline_avg_route_distance_m
    avg_duration_gap_s = current_avg_route_duration_s - baseline_avg_route_duration_s
    avg_distance_gap_pct = (
        avg_distance_gap_m / baseline_avg_route_distance_m * 100.0
    ) if baseline_avg_route_distance_m else 0.0
    avg_duration_gap_pct = (
        avg_duration_gap_s / baseline_avg_route_duration_s * 100.0
    ) if baseline_avg_route_duration_s else 0.0

    recommendations: list[str] = []
    if route_gap > 0:
        recommendations.append(
            f"The current plan uses {route_gap} more routes than the free-optimization baseline."
        )
    if avg_distance_gap_pct > 10:
        recommendations.append(
            f"The current plan average route distance is {avg_distance_gap_pct:.1f}% above the free-optimization baseline."
        )
    if avg_duration_gap_pct > 10:
        recommendations.append(
            f"The current plan average route duration is {avg_duration_gap_pct:.1f}% above the free-optimization baseline."
        )
    if current_avg_load_factor < 0.6:
        recommendations.append(
            "The current plan average load factor is low. Review whether some routes can be merged or downsized."
        )
    current_bus_mix = dict(current_plan_assessment.get("bus_mix", {}))
    for bus_type, current_count in sorted(current_bus_mix.items()):
        baseline_count = int(baseline_bus_mix.get(bus_type, 0))
        if current_count > baseline_count:
            recommendations.append(
                f"The current plan uses {current_count - baseline_count} more {bus_type} vehicles than the free-optimization baseline."
            )

    return {
        "current_route_count": current_route_count,
        "baseline_route_count": baseline_route_count,
        "route_gap": route_gap,
        "current_avg_route_distance_m": current_avg_route_distance_m,
        "baseline_avg_route_distance_m": baseline_avg_route_distance_m,
        "avg_distance_gap_m": avg_distance_gap_m,
        "avg_distance_gap_pct": avg_distance_gap_pct,
        "current_avg_route_duration_s": current_avg_route_duration_s,
        "baseline_avg_route_duration_s": baseline_avg_route_duration_s,
        "avg_duration_gap_s": avg_duration_gap_s,
        "avg_duration_gap_pct": avg_duration_gap_pct,
        "current_avg_load_factor": current_avg_load_factor,
        "current_bus_mix": current_bus_mix,
        "baseline_bus_mix": baseline_bus_mix,
        "recommendations": recommendations,
    }


def _compute_route_centroid(
    prepared_original_points: list[dict[str, Any]],
    node_ids: list[int],
) -> tuple[float, float] | None:
    centroid_points = [
        prepared_original_points[node_id]
        for node_id in node_ids
        if 0 <= int(node_id) < len(prepared_original_points)
    ]
    if not centroid_points:
        return None
    avg_lat = sum(float(point.get("lat", 0.0)) for point in centroid_points) / len(centroid_points)
    avg_lng = sum(float(point.get("lng", 0.0)) for point in centroid_points) / len(centroid_points)
    return avg_lat, avg_lng


def _min_inter_route_distance_m(
    from_node_ids: list[int],
    to_node_ids: list[int],
    solve_distance: list[list[float]],
) -> float:
    if not from_node_ids or not to_node_ids:
        return float("inf")
    best = float("inf")
    for from_node in from_node_ids:
        for to_node in to_node_ids:
            if from_node == to_node:
                continue
            best = min(best, float(solve_distance[from_node][to_node]))
    return best


def _min_stopset_to_route_distance_m(
    stop_ids: list[str],
    route_model: dict[str, Any],
    global_stop_to_node_id: dict[str, int],
    solve_distance: list[list[float]],
) -> float:
    transfer_node_ids = [
        int(global_stop_to_node_id[str(stop_id).strip()])
        for stop_id in stop_ids
        if str(stop_id).strip() in global_stop_to_node_id
    ]
    route_node_ids = [int(node_id) for node_id in list(route_model.get("non_depot_node_ids") or [])]
    return _min_inter_route_distance_m(transfer_node_ids, route_node_ids, solve_distance)


def _nearest_route_stop_connection(
    stop_id: str,
    route_model: dict[str, Any],
    stop_lookup: dict[str, dict[str, Any]],
    global_stop_to_node_id: dict[str, int],
    solve_time: list[list[float]],
    solve_distance: list[list[float]],
) -> dict[str, Any] | None:
    normalized_stop_id = str(stop_id).strip()
    if not normalized_stop_id or normalized_stop_id not in global_stop_to_node_id:
        return None
    source_node_id = int(global_stop_to_node_id[normalized_stop_id])
    best_connection: dict[str, Any] | None = None
    best_duration_s = float("inf")
    for candidate_stop_id in list(route_model.get("non_depot_stop_ids") or []):
        normalized_candidate_stop_id = str(candidate_stop_id).strip()
        if not normalized_candidate_stop_id or normalized_candidate_stop_id not in global_stop_to_node_id:
            continue
        candidate_node_id = int(global_stop_to_node_id[normalized_candidate_stop_id])
        private_drive_time_s = float(solve_time[source_node_id][candidate_node_id])
        private_drive_distance_m = float(solve_distance[source_node_id][candidate_node_id])
        if private_drive_time_s < best_duration_s:
            candidate_stop = dict(stop_lookup.get(normalized_candidate_stop_id) or {})
            best_duration_s = private_drive_time_s
            best_connection = {
                "pickup_route_id": str(route_model.get("route_id", "")).strip(),
                "pickup_stop_id": normalized_candidate_stop_id,
                "pickup_address": str(candidate_stop.get("address", "")).strip(),
                "private_drive_time_s": private_drive_time_s,
                "private_drive_distance_m": private_drive_distance_m,
            }
    return best_connection


def _build_route_models(
    current_plan: dict[str, Any] | None,
    current_plan_assessment: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    config: PlannerConfig,
) -> dict[str, dict[str, Any]]:
    normalized = _normalize_current_plan(current_plan)
    if not normalized or not current_plan_assessment:
        return {}
    service_direction = normalize_service_direction(normalized.get("service_direction") or config.service_direction)

    stop_lookup = {
        str(item.get("stop_id", "")).strip(): dict(item)
        for item in list(normalized.get("stops") or [])
    }
    bus_capacity_lookup = _build_bus_capacity_lookup(config, normalized)
    route_models: dict[str, dict[str, Any]] = {}

    for route_summary in list(current_plan_assessment.get("route_summaries") or []):
        route_id = str(route_summary.get("route_id", "")).strip()
        if not route_id:
            continue
        stop_ids = [str(item).strip() for item in list(route_summary.get("stop_ids") or []) if str(item).strip()]
        matched_stop_ids = [
            str(item).strip()
            for item in list(route_summary.get("matched_stop_ids") or [])
            if str(item).strip()
        ]
        matched_node_ids = [int(item) for item in list(route_summary.get("matched_node_ids") or [])]
        if not stop_ids:
            continue

        terminal_stop_id = stop_ids[route_terminal_index([{"stop_id": stop_id} for stop_id in stop_ids], service_direction)]
        non_depot_stop_ids = [stop_id for stop_id in stop_ids if stop_id != terminal_stop_id]
        stop_to_node_id = {
            stop_id: node_id
            for stop_id, node_id in zip(matched_stop_ids, matched_node_ids)
        }
        depot_node_id = stop_to_node_id.get(terminal_stop_id)
        non_depot_node_ids = [
            stop_to_node_id[stop_id]
            for stop_id in non_depot_stop_ids
            if stop_id in stop_to_node_id
        ]
        matched_complete = depot_node_id is not None and len(non_depot_node_ids) == len(non_depot_stop_ids)
        centroid = _compute_route_centroid(prepared_original_points, non_depot_node_ids or ([depot_node_id] if depot_node_id is not None else []))
        bus_type = str(route_summary.get("bus_type", "")).strip() or "Unknown"
        capacity = int(route_summary.get("capacity", bus_capacity_lookup.get(bus_type, 0)))
        route_models[route_id] = {
            "route_id": route_id,
            "bus_type": bus_type,
            "capacity": capacity,
            "passenger_count": int(route_summary.get("passenger_count", 0) or 0),
            "load_factor": float(route_summary.get("load_factor", 0.0) or 0.0),
            "distance_m": float(route_summary.get("distance_m", 0.0) or 0.0),
            "duration_s": float(route_summary.get("duration_s", 0.0) or 0.0),
            "stop_count": int(route_summary.get("stop_count", 0) or 0),
            "stop_ids": stop_ids,
            "non_depot_stop_ids": non_depot_stop_ids,
            "depot_stop_id": terminal_stop_id,
            "depot_node_id": depot_node_id,
            "stop_to_node_id": stop_to_node_id,
            "non_depot_node_ids": non_depot_node_ids,
            "matched_complete": matched_complete,
            "centroid": centroid,
            "addresses": [str(item).strip() for item in list(route_summary.get("addresses") or []) if str(item).strip()],
            "stop_records": [stop_lookup[stop_id] for stop_id in stop_ids if stop_id in stop_lookup],
        }
    return route_models


def _build_transfer_units(route_model: dict[str, Any]) -> list[list[str]]:
    non_depot_stop_ids = [str(item).strip() for item in list(route_model.get("non_depot_stop_ids") or []) if str(item).strip()]
    transfer_units: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    edge_first_units: list[tuple[str, ...]] = []
    for width in (1, 2, 3):
        if len(non_depot_stop_ids) >= width:
            edge_first_units.append(tuple(non_depot_stop_ids[:width]))
            edge_first_units.append(tuple(non_depot_stop_ids[-width:]))

    all_contiguous_units: list[tuple[str, ...]] = []
    for width in (1, 2, 3):
        for index in range(len(non_depot_stop_ids) - width + 1):
            unit = tuple(non_depot_stop_ids[index:index + width])
            if len(unit) == width:
                all_contiguous_units.append(unit)

    for unit in [*edge_first_units, *all_contiguous_units]:
        if unit and unit not in seen:
            transfer_units.append(list(unit))
            seen.add(unit)
    return transfer_units


def _route_metric_from_stop_ids(
    stop_ids: list[str],
    global_stop_to_node_id: dict[str, int],
    solve_time: list[list[float]],
    solve_distance: list[list[float]],
    service_direction: str = "From School",
) -> tuple[float, float, list[int]] | None:
    if not stop_ids:
        return 0.0, 0.0, []
    normalized_direction = normalize_service_direction(service_direction)
    terminal_idx = route_terminal_index([{"stop_id": stop_id} for stop_id in stop_ids], normalized_direction)
    terminal_stop_id = str(stop_ids[terminal_idx]).strip()
    if terminal_stop_id not in global_stop_to_node_id:
        return None
    service_stop_ids = [str(stop_id).strip() for idx, stop_id in enumerate(stop_ids) if idx != terminal_idx]
    node_ids = [int(global_stop_to_node_id[terminal_stop_id])]
    for stop_id in service_stop_ids:
        normalized_stop_id = str(stop_id).strip()
        if normalized_stop_id not in global_stop_to_node_id:
            return None
        node_ids.append(int(global_stop_to_node_id[normalized_stop_id]))
    optimize_matrix = transpose_matrix(solve_time) if normalized_direction == "To School" else solve_time
    optimized_node_ids = _optimize_route_node_order(node_ids, optimize_matrix) if len(node_ids) >= 3 else node_ids
    if normalized_direction == "To School":
        optimized_node_ids = list(reversed(optimized_node_ids))
    route_distance_m = _route_metric_cost(optimized_node_ids, solve_distance) if len(optimized_node_ids) >= 2 else 0.0
    route_duration_s = _route_metric_cost(optimized_node_ids, solve_time) if len(optimized_node_ids) >= 2 else 0.0
    return route_distance_m, route_duration_s, optimized_node_ids


def _identify_weak_route_reasons(route_model: dict[str, Any], config: PlannerConfig) -> tuple[list[str], list[str], int]:
    reasons: list[str] = []
    reason_codes: list[str] = []
    severity_score = 0
    load_factor = float(route_model.get("load_factor", 0.0) or 0.0)
    duration_s = float(route_model.get("duration_s", 0.0) or 0.0)
    stop_count = int(route_model.get("stop_count", 0) or 0)
    bus_type = str(route_model.get("bus_type", "")).strip()

    if load_factor < 0.5:
        reasons.append("Low load")
        reason_codes.append("low_load")
        severity_score += 3
    elif load_factor < 0.6:
        reasons.append("Below target load")
        reason_codes.append("below_target_load")
        severity_score += 2

    if duration_s > effective_route_duration_limit_minutes(config) * 60:
        reasons.append("Overlong")
        reason_codes.append("overlong")
        severity_score += 3

    if stop_count > 6:
        reasons.append("Many stops")
        reason_codes.append("many_stops")
        severity_score += 1

    if bus_type == (str(config.large_bus_name).strip() or "Large Bus") and load_factor < 0.65:
        reasons.append("Large bus appears oversized")
        reason_codes.append("oversized_large_bus")
        severity_score += 2

    return reasons, reason_codes, severity_score


def _select_candidate_receiving_routes(
    weak_route: dict[str, Any],
    route_models: dict[str, dict[str, Any]],
    solve_distance: list[list[float]],
    limit: int = 4,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    weak_route_non_depot_nodes = list(weak_route.get("non_depot_node_ids") or [])
    for route_id, route_model in sorted(route_models.items()):
        if route_id == weak_route["route_id"]:
            continue
        if not bool(route_model.get("matched_complete")):
            continue
        min_inter_route_distance_m = _min_inter_route_distance_m(
            weak_route_non_depot_nodes,
            list(route_model.get("non_depot_node_ids") or []),
            solve_distance,
        )
        if min_inter_route_distance_m == float("inf"):
            continue
        spare_seats = int(route_model.get("capacity", 0) or 0) - int(route_model.get("passenger_count", 0) or 0)
        if spare_seats <= 0:
            continue
        candidates.append(
            {
                "route_id": route_id,
                "bus_type": str(route_model.get("bus_type", "")).strip() or "Unknown",
                "spare_seats": spare_seats,
                "current_load_factor_pct": float(route_model.get("load_factor", 0.0) or 0.0) * 100.0,
                "min_inter_route_distance_m": min_inter_route_distance_m,
            }
        )
    candidates.sort(key=lambda item: (float(item["min_inter_route_distance_m"]), -int(item["spare_seats"])))
    return candidates[:limit]


def _classify_route_action_stage(
    *,
    projected_from_non_depot_count: int,
    projected_from_passenger_count: int,
    projected_from_capacity: int,
    moved_stop_share: float,
    moved_passenger_share: float,
    network_total_distance_saving_m: float,
    network_total_duration_saving_s: float,
    transfer_to_route_min_distance_m: float,
) -> tuple[str, str, bool, bool, bool]:
    if projected_from_non_depot_count == 0:
        return (
            "route_removable_now",
            "Route removable now",
            True,
            True,
            False,
        )

    removal_passenger_limit = max(4, int(round(projected_from_capacity * 0.15))) if projected_from_capacity > 0 else 4
    consolidation_passenger_limit = max(8, int(round(projected_from_capacity * 0.30))) if projected_from_capacity > 0 else 8
    has_material_network_benefit = (
        network_total_duration_saving_s >= 180.0
        or network_total_distance_saving_m >= 500.0
    )
    is_local_enough_for_structural_change = transfer_to_route_min_distance_m <= 5000.0

    route_removal_path = (
        projected_from_non_depot_count <= 2
        and projected_from_passenger_count <= removal_passenger_limit
        and (moved_stop_share >= 0.5 or moved_passenger_share >= 0.5)
        and has_material_network_benefit
        and is_local_enough_for_structural_change
    )
    if route_removal_path:
        return (
            "route_removal_path",
            "Strong removal path",
            True,
            True,
            False,
        )

    route_consolidation_path = (
        projected_from_non_depot_count <= 3
        and projected_from_passenger_count <= consolidation_passenger_limit
        and (moved_stop_share >= 0.35 or moved_passenger_share >= 0.35)
        and transfer_to_route_min_distance_m <= 6500.0
    )
    if route_consolidation_path:
        return (
            "route_consolidation_path",
            "Consolidation path",
            False,
            False,
            True,
        )

    return (
        "local_improvement",
        "Local improvement",
        False,
        False,
        False,
    )


def _simulate_transfer_opportunity(
    from_route: dict[str, Any],
    to_route: dict[str, Any],
    transfer_stop_ids: list[str],
    stop_lookup: dict[str, dict[str, Any]],
    global_stop_to_node_id: dict[str, int],
    solve_time: list[list[float]],
    solve_distance: list[list[float]],
    config: PlannerConfig,
    total_route_count: int,
    weak_reason_codes: list[str],
) -> dict[str, Any] | None:
    service_direction = normalize_service_direction(config.service_direction)
    transfer_stop_ids = [str(item).strip() for item in transfer_stop_ids if str(item).strip()]
    if not transfer_stop_ids:
        return None

    moved_passenger_count = sum(
        int(stop_lookup[stop_id].get("passenger_count", 0) or 0)
        for stop_id in transfer_stop_ids
        if stop_id in stop_lookup
    )
    projected_to_passenger_count = int(to_route.get("passenger_count", 0) or 0) + moved_passenger_count
    projected_to_capacity = int(to_route.get("capacity", 0) or 0)
    if projected_to_capacity <= 0 or projected_to_passenger_count > projected_to_capacity:
        return None

    transfer_to_route_min_distance_m = _min_stopset_to_route_distance_m(
        transfer_stop_ids,
        to_route,
        global_stop_to_node_id,
        solve_distance,
    )
    if transfer_to_route_min_distance_m == float("inf"):
        return None

    current_from_stop_ids = list(from_route.get("stop_ids") or [])
    current_to_stop_ids = list(to_route.get("stop_ids") or [])
    current_from_terminal_idx = route_terminal_index([{"stop_id": stop_id} for stop_id in current_from_stop_ids], service_direction)
    current_to_terminal_idx = route_terminal_index([{"stop_id": stop_id} for stop_id in current_to_stop_ids], service_direction)
    current_from_terminal_stop_id = current_from_stop_ids[current_from_terminal_idx]
    current_to_terminal_stop_id = current_to_stop_ids[current_to_terminal_idx]
    current_from_service_stop_ids = [stop_id for idx, stop_id in enumerate(current_from_stop_ids) if idx != current_from_terminal_idx]
    current_to_service_stop_ids = [stop_id for idx, stop_id in enumerate(current_to_stop_ids) if idx != current_to_terminal_idx]
    projected_from_service_stop_ids = [
        stop_id for stop_id in current_from_service_stop_ids
        if stop_id not in set(transfer_stop_ids)
    ]
    projected_to_service_stop_ids = [*current_to_service_stop_ids, *[
        stop_id for stop_id in transfer_stop_ids
        if stop_id not in set(current_to_service_stop_ids)
    ]]
    if service_direction == "To School":
        projected_from_stop_ids = [*projected_from_service_stop_ids, current_from_terminal_stop_id]
        projected_to_stop_ids = [*projected_to_service_stop_ids, current_to_terminal_stop_id]
    else:
        projected_from_stop_ids = [current_from_terminal_stop_id, *projected_from_service_stop_ids]
        projected_to_stop_ids = [current_to_terminal_stop_id, *projected_to_service_stop_ids]
    projected_from_non_depot_count = max(0, len(projected_from_stop_ids) - 1)
    projected_from_passenger_count = int(from_route.get("passenger_count", 0) or 0) - moved_passenger_count

    projected_from_metrics = _route_metric_from_stop_ids(
        projected_from_stop_ids,
        global_stop_to_node_id,
        solve_time,
        solve_distance,
        service_direction=service_direction,
    )
    projected_to_metrics = _route_metric_from_stop_ids(
        projected_to_stop_ids,
        global_stop_to_node_id,
        solve_time,
        solve_distance,
        service_direction=service_direction,
    )
    if projected_from_metrics is None or projected_to_metrics is None:
        return None

    projected_from_distance_m, projected_from_duration_s, _ = projected_from_metrics
    projected_to_distance_m, projected_to_duration_s, _ = projected_to_metrics

    current_pair_distance_m = float(from_route.get("distance_m", 0.0) or 0.0) + float(to_route.get("distance_m", 0.0) or 0.0)
    current_pair_duration_s = float(from_route.get("duration_s", 0.0) or 0.0) + float(to_route.get("duration_s", 0.0) or 0.0)
    projected_pair_distance_m = projected_from_distance_m + projected_to_distance_m
    projected_pair_duration_s = projected_from_duration_s + projected_to_duration_s
    network_total_distance_saving_m = current_pair_distance_m - projected_pair_distance_m
    network_total_duration_saving_s = current_pair_duration_s - projected_pair_duration_s

    projected_from_capacity = int(from_route.get("capacity", 0) or 0)
    projected_from_load_factor = (
        projected_from_passenger_count / projected_from_capacity
        if projected_from_capacity > 0 else 0.0
    )
    projected_to_load_factor = (
        projected_to_passenger_count / projected_to_capacity
        if projected_to_capacity > 0 else 0.0
    )

    current_from_non_depot_count = max(0, len(current_from_stop_ids) - 1)
    moved_stop_share = len(transfer_stop_ids) / max(1, current_from_non_depot_count)
    moved_passenger_share = moved_passenger_count / max(1, int(from_route.get("passenger_count", 0) or 0))
    transfer_locality_limit_m = 6000.0 if len(transfer_stop_ids) == 1 else 7500.0

    (
        route_action_stage,
        route_action_label,
        route_removal_candidate,
        route_eliminated,
        route_consolidation_candidate,
    ) = _classify_route_action_stage(
        projected_from_non_depot_count=projected_from_non_depot_count,
        projected_from_passenger_count=projected_from_passenger_count,
        projected_from_capacity=projected_from_capacity,
        moved_stop_share=moved_stop_share,
        moved_passenger_share=moved_passenger_share,
        network_total_distance_saving_m=network_total_distance_saving_m,
        network_total_duration_saving_s=network_total_duration_saving_s,
        transfer_to_route_min_distance_m=transfer_to_route_min_distance_m,
    )

    effective_limit_seconds = effective_route_duration_limit_minutes(config) * 60
    if projected_to_duration_s > max(effective_limit_seconds * 1.15, float(to_route.get("duration_s", 0.0) or 0.0) * 1.3):
        return None
    if transfer_to_route_min_distance_m > transfer_locality_limit_m and not route_removal_candidate:
        return None
    if (
        "low_load" in weak_reason_codes
        and "overlong" not in weak_reason_codes
        and "many_stops" not in weak_reason_codes
        and not route_consolidation_candidate
        and moved_stop_share < 0.2
        and moved_passenger_share < 0.2
        and network_total_distance_saving_m < 500.0
        and network_total_duration_saving_s < 300.0
    ):
        return None
    if network_total_distance_saving_m <= 0 and network_total_duration_saving_s <= 0 and not route_removal_candidate:
        return None

    score = max(0.0, network_total_duration_saving_s / 60.0) + max(0.0, network_total_distance_saving_m / 1000.0) * 2.0
    if route_removal_candidate:
        score += 20.0
    if route_eliminated:
        score += 35.0
    if route_action_stage == "route_consolidation_path":
        score += 10.0
    if (
        "overlong" in weak_reason_codes
        and projected_from_duration_s <= effective_limit_seconds
        and projected_from_non_depot_count > 0
    ):
        score += 12.0
    if transfer_to_route_min_distance_m <= 1000.0:
        score += 5.0
    elif transfer_to_route_min_distance_m <= 2000.0:
        score += 2.0
    if projected_to_load_factor > 0.9:
        score -= 5.0
    elif projected_to_load_factor > 0.85:
        score -= 2.0

    rationale: list[str] = []
    if network_total_duration_saving_s > 0:
        rationale.append(
            f"Reduces network operating time by about {network_total_duration_saving_s / 60.0:.1f} minutes."
        )
    if network_total_distance_saving_m > 0:
        rationale.append(
            f"Reduces network operating distance by about {network_total_distance_saving_m / 1000.0:.1f} km."
        )
    if projected_to_load_factor > float(to_route.get("load_factor", 0.0) or 0.0):
        rationale.append(
            f"Improves load utilization on {to_route['route_id']} from {float(to_route.get('load_factor', 0.0) or 0.0) * 100.0:.1f}% to {projected_to_load_factor * 100.0:.1f}%."
        )
    if transfer_to_route_min_distance_m < float("inf"):
        rationale.append(
            f"The transferred stop set is only about {transfer_to_route_min_distance_m / 1000.0:.1f} km away from {to_route['route_id']}, so the move is geographically plausible."
        )
    if route_eliminated:
        rationale.append(
            f"This transfer empties {from_route['route_id']} and makes the route removable."
        )
    elif route_removal_candidate:
        rationale.append(
            f"After the transfer, {from_route['route_id']} would have only {projected_from_non_depot_count} stop(s) and {projected_from_passenger_count} rider(s) remaining, which creates a strong path to route removal."
        )
    elif route_consolidation_candidate:
        rationale.append(
            f"After the transfer, {from_route['route_id']} would have only {projected_from_non_depot_count} stop(s) and {projected_from_passenger_count} rider(s) remaining, which is a realistic next step toward route consolidation."
        )

    explanation_parts = [
        f"Move {len(transfer_stop_ids)} stop(s) from {from_route['route_id']} to {to_route['route_id']}.",
    ]
    if network_total_duration_saving_s > 0:
        explanation_parts.append(
            f"This is estimated to save about {network_total_duration_saving_s / 60.0:.1f} minutes of network operating time."
        )
    if network_total_distance_saving_m > 0:
        explanation_parts.append(
            f"It also reduces network operating distance by about {network_total_distance_saving_m / 1000.0:.1f} km."
        )
    if transfer_to_route_min_distance_m < float("inf"):
        explanation_parts.append(
            f"The transferred stop set sits about {transfer_to_route_min_distance_m / 1000.0:.1f} km from {to_route['route_id']}."
        )
    if route_eliminated:
        explanation_parts.append(
            f"{from_route['route_id']} would become empty after the move, so the route could be removed."
        )
    elif route_consolidation_candidate:
        explanation_parts.append(
            f"{from_route['route_id']} would be left with only {projected_from_non_depot_count} stop(s) and {projected_from_passenger_count} rider(s), which moves it closer to route consolidation."
        )
    explanation = " ".join(explanation_parts)
    return {
        "recommendation_type": "move_stop" if len(transfer_stop_ids) == 1 else "move_stop_cluster",
        "from_route_id": from_route["route_id"],
        "to_route_id": to_route["route_id"],
        "stop_ids": transfer_stop_ids,
        "addresses": [
            str(stop_lookup[stop_id].get("address", "")).strip()
            for stop_id in transfer_stop_ids
            if stop_id in stop_lookup
        ],
        "stop_count": len(transfer_stop_ids),
        "moved_passenger_count": moved_passenger_count,
        "network_total_distance_saving_m": network_total_distance_saving_m,
        "network_total_duration_saving_s": network_total_duration_saving_s,
        "transfer_to_route_min_distance_m": transfer_to_route_min_distance_m,
        "network_avg_route_distance_improvement_m": (
            network_total_distance_saving_m / total_route_count if total_route_count else 0.0
        ),
        "network_avg_route_duration_improvement_s": (
            network_total_duration_saving_s / total_route_count if total_route_count else 0.0
        ),
        "from_load_factor_before": float(from_route.get("load_factor", 0.0) or 0.0),
        "from_load_factor_after": projected_from_load_factor,
        "to_load_factor_before": float(to_route.get("load_factor", 0.0) or 0.0),
        "to_load_factor_after": projected_to_load_factor,
        "from_route_duration_before_s": float(from_route.get("duration_s", 0.0) or 0.0),
        "from_route_duration_after_s": projected_from_duration_s,
        "to_route_duration_before_s": float(to_route.get("duration_s", 0.0) or 0.0),
        "to_route_duration_after_s": projected_to_duration_s,
        "from_route_passenger_count_after": projected_from_passenger_count,
        "to_route_passenger_count_after": projected_to_passenger_count,
        "moved_stop_share": moved_stop_share,
        "moved_passenger_share": moved_passenger_share,
        "route_action_stage": route_action_stage,
        "route_action_label": route_action_label,
        "route_removal_candidate": route_removal_candidate,
        "route_consolidation_candidate": route_consolidation_candidate,
        "route_eliminated": route_eliminated,
        "remaining_from_route_stop_count": projected_from_non_depot_count,
        "rationale": rationale,
        "explanation": explanation,
        "score": score,
    }


def _reallocation_sort_key(item: dict[str, Any]) -> tuple[float, ...]:
    return (
        -float(bool(item.get("route_eliminated"))),
        -float(bool(item.get("route_removal_candidate"))),
        -float(bool(item.get("route_consolidation_candidate"))),
        -float(item.get("score", 0.0) or 0.0),
        -float(item.get("network_total_duration_saving_s", 0.0) or 0.0),
        -float(item.get("network_total_distance_saving_m", 0.0) or 0.0),
    )


def _select_diverse_reallocation_recommendations(
    recommendations: list[dict[str, Any]],
    limit: int = 10,
    max_per_from_route: int = 2,
) -> list[dict[str, Any]]:
    if not recommendations:
        return []

    sorted_recommendations = sorted(recommendations, key=_reallocation_sort_key)
    selected: list[dict[str, Any]] = []
    selected_signatures: set[tuple[str, str, tuple[str, ...]]] = set()
    selections_by_from_route: dict[str, int] = {}

    def maybe_add(item: dict[str, Any], enforce_first_pick: bool) -> None:
        from_route_id = str(item.get("from_route_id", "")).strip()
        to_route_id = str(item.get("to_route_id", "")).strip()
        stop_ids = tuple(str(stop_id).strip() for stop_id in list(item.get("stop_ids") or []))
        signature = (from_route_id, to_route_id, stop_ids)
        if not from_route_id or signature in selected_signatures:
            return
        current_count = selections_by_from_route.get(from_route_id, 0)
        if enforce_first_pick and current_count > 0:
            return
        if current_count >= max_per_from_route:
            return
        selected.append(item)
        selected_signatures.add(signature)
        selections_by_from_route[from_route_id] = current_count + 1

    for item in sorted_recommendations:
        if len(selected) >= limit:
            break
        maybe_add(item, enforce_first_pick=True)

    for item in sorted_recommendations:
        if len(selected) >= limit:
            break
        maybe_add(item, enforce_first_pick=False)

    selected.sort(key=_reallocation_sort_key)
    return selected[:limit]


def _build_route_reallocation_summary(
    weak_routes: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    route_opportunity_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    route_opportunity_profiles = list(route_opportunity_profiles or [])
    weak_reason_counts: dict[str, int] = {}
    for weak_route in weak_routes:
        for reason in list(weak_route.get("reasons") or []):
            normalized_reason = str(reason).strip()
            if not normalized_reason:
                continue
            weak_reason_counts[normalized_reason] = weak_reason_counts.get(normalized_reason, 0) + 1

    actionable_route_ids = {
        str(item.get("route_id", "")).strip()
        for item in route_opportunity_profiles
        if str(item.get("route_id", "")).strip()
    }
    route_removal_candidate_ids = {
        str(item.get("route_id", "")).strip()
        for item in route_opportunity_profiles
        if str(item.get("route_action_stage", "")).strip() == "route_removal_path"
    }
    route_consolidation_candidate_ids = {
        str(item.get("route_id", "")).strip()
        for item in route_opportunity_profiles
        if str(item.get("route_action_stage", "")).strip() == "route_consolidation_path"
    }
    route_removable_now_ids = {
        str(item.get("route_id", "")).strip()
        for item in route_opportunity_profiles
        if str(item.get("route_action_stage", "")).strip() == "route_removable_now"
    }
    stage_counts: dict[str, int] = {}
    for item in recommendations:
        stage = str(item.get("route_action_stage", "")).strip()
        if not stage:
            continue
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    best_network_time_saving_s = max(
        (float(item.get("network_total_duration_saving_s", 0.0) or 0.0) for item in recommendations),
        default=0.0,
    )
    best_network_distance_saving_m = max(
        (float(item.get("network_total_distance_saving_m", 0.0) or 0.0) for item in recommendations),
        default=0.0,
    )
    return {
        "weak_route_count": len(weak_routes),
        "actionable_weak_route_count": len(actionable_route_ids),
        "route_removable_now_count": len(route_removable_now_ids),
        "route_removal_candidate_count": len(route_removal_candidate_ids),
        "route_consolidation_candidate_count": len(route_consolidation_candidate_ids),
        "best_network_time_saving_s": best_network_time_saving_s,
        "best_network_distance_saving_m": best_network_distance_saving_m,
        "priority_recommendations": recommendations[:5],
        "priority_route_profiles": route_opportunity_profiles[:5],
        "weak_reason_counts": weak_reason_counts,
        "route_action_stage_counts": stage_counts,
    }


def _build_route_opportunity_profiles(
    recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in recommendations:
        route_id = str(item.get("from_route_id", "")).strip()
        if not route_id:
            continue
        grouped.setdefault(route_id, []).append(item)

    profiles: list[dict[str, Any]] = []
    stage_priority = {
        "route_removable_now": 3,
        "route_removal_path": 2,
        "route_consolidation_path": 1,
        "local_improvement": 0,
    }

    for route_id, items in grouped.items():
        sorted_items = sorted(items, key=_reallocation_sort_key)
        best_item = sorted_items[0]
        stage_counts: dict[str, int] = {}
        to_route_counts: dict[str, int] = {}
        for item in sorted_items:
            stage = str(item.get("route_action_stage", "")).strip() or "local_improvement"
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            to_route_id = str(item.get("to_route_id", "")).strip()
            if to_route_id:
                to_route_counts[to_route_id] = to_route_counts.get(to_route_id, 0) + 1

        support_count = len(sorted_items)
        top_stage = str(best_item.get("route_action_stage", "")).strip() or "local_improvement"
        top_stage_support_count = int(stage_counts.get(top_stage, 0))
        best_to_route_id = str(best_item.get("to_route_id", "")).strip()
        best_to_route_support_count = int(to_route_counts.get(best_to_route_id, 0))

        stabilized_stage = top_stage
        if top_stage == "route_removal_path" and top_stage_support_count < 2 and not bool(best_item.get("route_eliminated")):
            stabilized_stage = "route_consolidation_path"
        if top_stage == "route_consolidation_path" and support_count < 2:
            stabilized_stage = "local_improvement"

        label_lookup = {
            "route_removable_now": "Route removable now",
            "route_removal_path": "Strong removal path",
            "route_consolidation_path": "Consolidation path",
            "local_improvement": "Local improvement",
        }
        profiles.append(
            {
                "route_id": route_id,
                "route_action_stage": stabilized_stage,
                "route_action_label": label_lookup[stabilized_stage],
                "supporting_move_count": support_count,
                "supporting_stage_move_count": top_stage_support_count if stabilized_stage == top_stage else int(stage_counts.get(stabilized_stage, 0)),
                "best_to_route_id": best_to_route_id,
                "best_to_route_support_count": best_to_route_support_count,
                "best_network_time_saving_s": float(best_item.get("network_total_duration_saving_s", 0.0) or 0.0),
                "best_network_distance_saving_m": float(best_item.get("network_total_distance_saving_m", 0.0) or 0.0),
                "best_remaining_stop_count": int(best_item.get("remaining_from_route_stop_count", 0) or 0),
                "best_remaining_passenger_count": int(best_item.get("from_route_passenger_count_after", 0) or 0),
                "best_transfer_to_route_distance_m": float(best_item.get("transfer_to_route_min_distance_m", 0.0) or 0.0),
                "best_explanation": str(best_item.get("explanation", "")).strip(),
                "score": float(best_item.get("score", 0.0) or 0.0),
                "top_stage_priority": stage_priority.get(stabilized_stage, 0),
            }
        )

    profiles.sort(
        key=lambda item: (
            -int(item.get("top_stage_priority", 0) or 0),
            -int(item.get("supporting_stage_move_count", 0) or 0),
            -int(item.get("supporting_move_count", 0) or 0),
            -float(item.get("best_network_time_saving_s", 0.0) or 0.0),
            -float(item.get("best_network_distance_saving_m", 0.0) or 0.0),
            -float(item.get("score", 0.0) or 0.0),
        )
    )
    return profiles


def analyze_route_reallocation_opportunities(
    planner: Any,
    current_plan: dict[str, Any] | None,
    current_plan_assessment: dict[str, Any] | None,
    prepared_original_points: list[dict[str, Any]],
    config: PlannerConfig,
    solve_time: list[list[float]] | None = None,
    solve_distance: list[list[float]] | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_current_plan(current_plan)
    if not normalized or not current_plan_assessment:
        return None

    route_models = _build_route_models(
        current_plan,
        current_plan_assessment,
        prepared_original_points,
        config,
    )
    if not route_models:
        return None
    if solve_time is None or solve_distance is None:
        solve_time, solve_distance = _build_assessment_metric_matrices(planner, prepared_original_points)

    stop_lookup = {
        str(item.get("stop_id", "")).strip(): dict(item)
        for item in list(normalized.get("stops") or [])
    }
    global_stop_to_node_id: dict[str, int] = {}
    for route_model in route_models.values():
        global_stop_to_node_id.update(
            {
                str(stop_id).strip(): int(node_id)
                for stop_id, node_id in dict(route_model.get("stop_to_node_id") or {}).items()
            }
        )

    weak_routes: list[dict[str, Any]] = []
    candidate_route_pair_count = 0
    evaluated_move_count = 0
    recommendations: list[dict[str, Any]] = []

    for route_model in route_models.values():
        if not bool(route_model.get("matched_complete")):
            continue
        reasons, reason_codes, severity_score = _identify_weak_route_reasons(route_model, config)
        if not reasons:
            continue
        candidate_receiving_routes = _select_candidate_receiving_routes(route_model, route_models, solve_distance)
        candidate_route_pair_count += len(candidate_receiving_routes)
        weak_route_entry = {
            "route_id": route_model["route_id"],
            "bus_type": route_model["bus_type"],
            "stop_count": route_model["stop_count"],
            "passenger_count": route_model["passenger_count"],
            "load_factor_pct": float(route_model["load_factor"]) * 100.0,
            "distance_km": float(route_model["distance_m"]) / 1000.0,
            "duration_min": float(route_model["duration_s"]) / 60.0,
            "reasons": reasons,
            "severity_score": severity_score,
            "candidate_receiving_routes": candidate_receiving_routes,
        }
        weak_routes.append(weak_route_entry)

        for candidate_receiving_route in candidate_receiving_routes:
            receiving_route = route_models.get(str(candidate_receiving_route["route_id"]))
            if not receiving_route:
                continue
            for transfer_stop_ids in _build_transfer_units(route_model):
                evaluated_move_count += 1
                recommendation = _simulate_transfer_opportunity(
                    route_model,
                    receiving_route,
                    transfer_stop_ids,
                    stop_lookup,
                    global_stop_to_node_id,
                    solve_time,
                    solve_distance,
                    config,
                    total_route_count=len(route_models),
                    weak_reason_codes=reason_codes,
                )
                if recommendation:
                    recommendations.append(recommendation)

    weak_routes.sort(key=lambda item: (-int(item["severity_score"]), float(item["load_factor_pct"]), -float(item["duration_min"])))
    recommendations = _select_diverse_reallocation_recommendations(recommendations, limit=10, max_per_from_route=2)
    route_opportunity_profiles = _build_route_opportunity_profiles(recommendations)
    summary = _build_route_reallocation_summary(weak_routes, recommendations, route_opportunity_profiles)

    return {
        "weak_routes": weak_routes,
        "candidate_route_pair_count": candidate_route_pair_count,
        "evaluated_move_count": evaluated_move_count,
        "recommendations": recommendations,
        "route_opportunity_profiles": route_opportunity_profiles,
        "summary": summary,
    }


def _private_drive_route_between_points(
    planner: Any,
    from_point: dict[str, Any],
    to_point: dict[str, Any],
) -> tuple[float, float, list[tuple[float, float]]]:
    from_lat, from_lng = planner.point_osrm_lat_lng(from_point)
    to_lat, to_lng = planner.point_osrm_lat_lng(to_point)
    previous_osrm_base_url = planner.OSRM_BASE_URL
    planner.OSRM_BASE_URL = planner.resolve_osrm_base_url([from_point, to_point])
    try:
        coordinates = f"{from_lng:.6f},{from_lat:.6f};{to_lng:.6f},{to_lat:.6f}"
        payload = planner.osrm_request_json("route", coordinates, {"overview": "full", "geometries": "geojson"})
        route = dict((payload.get("routes") or [{}])[0])
        duration_s = float(route.get("duration", 0.0) or 0.0)
        distance_m = float(route.get("distance", 0.0) or 0.0)
        geometry = [
            (float(lat), float(lng))
            for lng, lat in list(((route.get("geometry") or {}).get("coordinates") or []))
        ]
        if duration_s > 0 and distance_m > 0:
            if geometry:
                geometry[0] = (float(from_point.get("plot_lat", from_point.get("lat", 0.0)) or 0.0), float(from_point.get("plot_lng", from_point.get("lng", 0.0)) or 0.0))
                geometry[-1] = (float(to_point.get("plot_lat", to_point.get("lat", 0.0)) or 0.0), float(to_point.get("plot_lng", to_point.get("lng", 0.0)) or 0.0))
            return duration_s, distance_m, geometry
    except Exception:
        pass
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url

    fallback_distance_km = planner.haversine_distance_km(
        float(from_point.get("lat", 0.0) or 0.0),
        float(from_point.get("lng", 0.0) or 0.0),
        float(to_point.get("lat", 0.0) or 0.0),
        float(to_point.get("lng", 0.0) or 0.0),
    )
    fallback_geometry = [
        (
            float(from_point.get("plot_lat", from_point.get("lat", 0.0)) or 0.0),
            float(from_point.get("plot_lng", from_point.get("lng", 0.0)) or 0.0),
        ),
        (
            float(to_point.get("plot_lat", to_point.get("lat", 0.0)) or 0.0),
            float(to_point.get("plot_lng", to_point.get("lng", 0.0)) or 0.0),
        ),
    ]
    return fallback_distance_km * 150.0, fallback_distance_km * 1000.0, fallback_geometry


def analyze_nearby_private_access(
    planner: Any,
    original_points: list[dict[str, Any]],
    nearby_points: list[dict[str, Any]],
    nearby_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not original_points or not nearby_points or not nearby_result:
        return None

    original_point_lookup = {
        str(point.get("address", "")).strip(): dict(point)
        for point in original_points
        if str(point.get("address", "")).strip()
    }
    nearby_route_by_node: dict[int, str] = {}
    for route in list(nearby_result.get("routes") or []):
        route_id = f"Bus {int(route.get('vehicle_id', 0) or 0)}"
        for node_id in list(route.get("nodes") or []):
            nearby_route_by_node[int(node_id)] = route_id

    rows: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    for nearby_point in nearby_points[1:]:
        if str(nearby_point.get("aggregated_type", "")).strip() != "nearby":
            continue
        original_members = [
            str(member).strip()
            for member in list(nearby_point.get("original_members") or [])
            if str(member).strip()
        ]
        if len(original_members) <= 1:
            continue
        pickup_address = str(nearby_point.get("address", "")).strip()
        pickup_route_id = str(nearby_route_by_node.get(int(nearby_point.get("node_id", -1)), "")).strip()
        pickup_passenger_count = int(nearby_point.get("passenger_count", 0) or 0)
        cluster_members: list[dict[str, Any]] = []
        for member_address in original_members:
            if member_address == pickup_address:
                continue
            member_point = original_point_lookup.get(member_address)
            if not member_point:
                continue
            private_drive_time_s, private_drive_distance_m, private_drive_geometry = _private_drive_route_between_points(
                planner,
                member_point,
                nearby_point,
            )
            rows.append(
                {
                    "address": member_address,
                    "passenger_count": int(member_point.get("passenger_count", 0) or 0),
                    "plot_lat": float(member_point.get("plot_lat", member_point.get("lat", 0.0)) or 0.0),
                    "plot_lng": float(member_point.get("plot_lng", member_point.get("lng", 0.0)) or 0.0),
                    "pickup_address": pickup_address,
                    "pickup_route_id": pickup_route_id,
                    "pickup_passenger_count": pickup_passenger_count,
                    "pickup_plot_lat": float(nearby_point.get("plot_lat", nearby_point.get("lat", 0.0)) or 0.0),
                    "pickup_plot_lng": float(nearby_point.get("plot_lng", nearby_point.get("lng", 0.0)) or 0.0),
                    "private_drive_time_s": private_drive_time_s,
                    "private_drive_distance_m": private_drive_distance_m,
                    "private_drive_geometry": private_drive_geometry,
                    "private_access_type": "clustered_rider",
                    "reason": (
                        f"This rider would likely self-drive or be dropped off at the nearby aggregated pickup "
                        f"`{pickup_address}` instead of receiving direct door-to-door service."
                    ),
                }
            )
            cluster_members.append(
                {
                    "address": member_address,
                    "passenger_count": int(member_point.get("passenger_count", 0) or 0),
                    "plot_lat": float(member_point.get("plot_lat", member_point.get("lat", 0.0)) or 0.0),
                    "plot_lng": float(member_point.get("plot_lng", member_point.get("lng", 0.0)) or 0.0),
                    "private_drive_time_s": private_drive_time_s,
                    "private_drive_distance_m": private_drive_distance_m,
                    "private_drive_geometry": private_drive_geometry,
                }
            )

        if cluster_members:
            cluster_members.sort(
                key=lambda item: (
                    -float(item.get("private_drive_time_s", 0.0) or 0.0),
                    -int(item.get("passenger_count", 0) or 0),
                    str(item.get("address", "")).strip().lower(),
                ),
            )
            cluster_drive_times = [float(item.get("private_drive_time_s", 0.0) or 0.0) for item in cluster_members]
            cluster_drive_distances = [float(item.get("private_drive_distance_m", 0.0) or 0.0) for item in cluster_members]
            clusters.append(
                {
                    "pickup_address": pickup_address,
                    "pickup_route_id": pickup_route_id,
                    "pickup_passenger_count": pickup_passenger_count,
                    "clustered_rider_count": len(cluster_members),
                    "clustered_passenger_count": sum(int(item.get("passenger_count", 0) or 0) for item in cluster_members),
                    "avg_private_drive_time_s": sum(cluster_drive_times) / len(cluster_drive_times),
                    "max_private_drive_time_s": max(cluster_drive_times),
                    "avg_private_drive_distance_m": sum(cluster_drive_distances) / len(cluster_drive_distances),
                    "max_private_drive_distance_m": max(cluster_drive_distances),
                    "members": cluster_members,
                }
            )

    if not rows:
        return None

    rows.sort(
        key=lambda item: (
            -float(item.get("private_drive_time_s", 0.0) or 0.0),
            -int(item.get("passenger_count", 0) or 0),
            str(item.get("address", "")).strip().lower(),
        ),
    )
    private_drive_times = [float(item.get("private_drive_time_s", 0.0) or 0.0) for item in rows]
    private_drive_distances = [float(item.get("private_drive_distance_m", 0.0) or 0.0) for item in rows]
    top_outlier = rows[0]
    return {
        "summary": {
            "candidate_stop_count": len(rows),
            "cluster_center_count": len(clusters),
            "avg_private_drive_time_s": sum(private_drive_times) / len(private_drive_times),
            "max_private_drive_time_s": max(private_drive_times),
            "avg_private_drive_distance_m": sum(private_drive_distances) / len(private_drive_distances),
            "max_private_drive_distance_m": max(private_drive_distances),
            "furthest_stop_address": str(top_outlier.get("address", "")).strip(),
            "furthest_pickup_address": str(top_outlier.get("pickup_address", "")).strip(),
            "furthest_pickup_route_id": str(top_outlier.get("pickup_route_id", "")).strip(),
        },
        "rows": rows,
        "clusters": clusters,
    }


def build_further_most_stop_scenario(
    planner: Any,
    points: list[dict[str, Any]],
    source_result: dict[str, Any] | None,
    service_direction: str = "From School",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not points or not source_result or not bool(source_result.get("enabled", True)):
        return deepcopy(source_result or {}), None

    scenario = deepcopy(source_result)
    scenario_points = scenario.get("points")
    if isinstance(scenario_points, list) and scenario_points:
        points = scenario_points
    routes = [dict(route) for route in list(scenario.get("routes") or [])]
    if not routes:
        scenario["outlying_private_access_rows"] = []
        scenario["private_drive_stop_count"] = 0
        return scenario, None

    normalized_direction = normalize_service_direction(service_direction)
    is_to_school = normalized_direction == "To School"

    private_drive_rows: list[dict[str, Any]] = []
    truncated_routes: list[dict[str, Any]] = []
    for route in routes:
        route_nodes = list(route.get("nodes") or [])
        leg_details = list(route.get("leg_details") or [])
        route_id = f"Bus {int(route.get('vehicle_id', 0) or 0)}"
        if not route_nodes:
            truncated_routes.append(route)
            continue

        if is_to_school:
            # To School: depot is at the end. Calculate limit from depot backward.
            cumulative_from_depot = 0.0
            limit_idx = len(route_nodes) - 1  # depot index
            for i in range(len(leg_details) - 1, -1, -1):
                cumulative_from_depot += float(leg_details[i].get("duration_s", 0.0) or 0.0)
                if cumulative_from_depot <= planner.ANNOTATION_ROUTE_DURATION_SECONDS:
                    limit_idx = i
                else:
                    break
            if limit_idx <= 0:
                # First pickup is already within xx min from depot; nothing to convert.
                truncated_routes.append(route)
                continue
            limit_stop_node = int(route_nodes[limit_idx])
            limit_point = points[limit_stop_node]
            private_drive_stops: list[dict[str, Any]] = []
            for pre_limit_node in route_nodes[:limit_idx]:
                stop_node = int(pre_limit_node)
                stop_point = points[stop_node]
                private_drive_time_s, private_drive_distance_m, private_drive_geometry = _private_drive_route_between_points(
                    planner,
                    stop_point,
                    limit_point,
                )
                private_drive_item = {
                    "address": str(stop_point.get("address", "")).strip(),
                    "passenger_count": int(stop_point.get("passenger_count", 0) or 0),
                    "plot_lat": float(stop_point.get("plot_lat", stop_point.get("lat", 0.0)) or 0.0),
                    "plot_lng": float(stop_point.get("plot_lng", stop_point.get("lng", 0.0)) or 0.0),
                    "pickup_address": str(limit_point.get("address", "")).strip(),
                    "pickup_route_id": route_id,
                    "pickup_passenger_count": int(limit_point.get("passenger_count", 0) or 0),
                    "pickup_plot_lat": float(limit_point.get("plot_lat", limit_point.get("lat", 0.0)) or 0.0),
                    "pickup_plot_lng": float(limit_point.get("plot_lng", limit_point.get("lng", 0.0)) or 0.0),
                    "private_drive_time_s": private_drive_time_s,
                    "private_drive_distance_m": private_drive_distance_m,
                    "private_drive_geometry": private_drive_geometry,
                    "private_access_type": "private_drive_stop",
                    "reason": (
                        f"This stop sits beyond the {planner.ANNOTATION_ROUTE_DURATION_SECONDS // 60}-minute mark from school and is modeled "
                        f"as a private-drive stop connecting to `{limit_point.get('address', '')}`."
                    ),
                }
                private_drive_rows.append(private_drive_item)
                private_drive_stops.append(private_drive_item)

            truncated_route = dict(route)
            truncated_route["nodes"] = route_nodes[limit_idx:]
            truncated_leg_details = leg_details[limit_idx:]
            truncated_route["leg_details"] = truncated_leg_details
            truncated_route["time_s"] = sum(float(item.get("duration_s", 0.0) or 0.0) for item in truncated_leg_details)
            truncated_route["distance_m"] = sum(float(item.get("distance_m", 0.0) or 0.0) for item in truncated_leg_details)
            truncated_route["private_drive_stops"] = private_drive_stops
            # Recalculate limit metadata for the truncated route (from depot backward on the truncated legs)
            recalc_cumulative = 0.0
            recalc_limit_idx = len(truncated_route["nodes"]) - 1
            for i in range(len(truncated_leg_details) - 1, -1, -1):
                recalc_cumulative += float(truncated_leg_details[i].get("duration_s", 0.0) or 0.0)
                if recalc_cumulative <= planner.ANNOTATION_ROUTE_DURATION_SECONDS:
                    recalc_limit_idx = i
                else:
                    break
            truncated_route["limit_stop_order"] = recalc_limit_idx
            truncated_route["limit_stop_node"] = int(truncated_route["nodes"][recalc_limit_idx])
            truncated_route["limit_stop_elapsed_s"] = recalc_cumulative
            truncated_routes.append(truncated_route)
        else:
            # From School: use existing forward logic (limit from start/depot)
            limit_stop_order = route.get("limit_stop_order")
            limit_stop_node = route.get("limit_stop_node")
            if limit_stop_order is None or limit_stop_node is None:
                truncated_routes.append(route)
                continue

            limit_stop_order = int(limit_stop_order)
            limit_stop_node = int(limit_stop_node)
            if limit_stop_order >= len(route_nodes) - 1:
                truncated_routes.append(route)
                continue

            limit_point = points[limit_stop_node]
            private_drive_stops: list[dict[str, Any]] = []
            for post_limit_node in route_nodes[limit_stop_order + 1:]:
                stop_node = int(post_limit_node)
                stop_point = points[stop_node]
                private_drive_time_s, private_drive_distance_m, private_drive_geometry = _private_drive_route_between_points(
                    planner,
                    stop_point,
                    limit_point,
                )
                private_drive_item = {
                    "address": str(stop_point.get("address", "")).strip(),
                    "passenger_count": int(stop_point.get("passenger_count", 0) or 0),
                    "plot_lat": float(stop_point.get("plot_lat", stop_point.get("lat", 0.0)) or 0.0),
                    "plot_lng": float(stop_point.get("plot_lng", stop_point.get("lng", 0.0)) or 0.0),
                    "pickup_address": str(limit_point.get("address", "")).strip(),
                    "pickup_route_id": route_id,
                    "pickup_passenger_count": int(limit_point.get("passenger_count", 0) or 0),
                    "pickup_plot_lat": float(limit_point.get("plot_lat", limit_point.get("lat", 0.0)) or 0.0),
                    "pickup_plot_lng": float(limit_point.get("plot_lng", limit_point.get("lng", 0.0)) or 0.0),
                    "private_drive_time_s": private_drive_time_s,
                    "private_drive_distance_m": private_drive_distance_m,
                    "private_drive_geometry": private_drive_geometry,
                    "private_access_type": "private_drive_stop",
                    "reason": (
                        f"This stop sits beyond the {planner.ANNOTATION_ROUTE_DURATION_SECONDS // 60}-minute mark and is modeled "
                        f"as a private-drive stop connecting to `{limit_point.get('address', '')}`."
                    ),
                }
                private_drive_rows.append(private_drive_item)
                private_drive_stops.append(private_drive_item)

            truncated_route = dict(route)
            truncated_route["nodes"] = route_nodes[: limit_stop_order + 1]
            truncated_leg_details = list(route.get("leg_details") or [])[:limit_stop_order]
            truncated_route["leg_details"] = truncated_leg_details
            truncated_route["time_s"] = sum(float(item.get("duration_s", 0.0) or 0.0) for item in truncated_leg_details)
            truncated_route["distance_m"] = sum(float(item.get("distance_m", 0.0) or 0.0) for item in truncated_leg_details)
            truncated_route["private_drive_stops"] = private_drive_stops
            truncated_routes.append(truncated_route)

    scenario["routes"] = truncated_routes
    scenario["outlying_private_access_rows"] = private_drive_rows
    scenario["private_drive_stop_count"] = len(private_drive_rows)
    service_point_count = len([point for point in points if not bool(point.get("is_depot"))])
    scenario["stop_count"] = int(scenario.get("service_stop_count", scenario.get("stop_count", service_point_count)) or 0)
    scenario["service_stop_count"] = int(scenario.get("service_stop_count", scenario["stop_count"]) or 0)
    scenario["map_point_count"] = len(points)
    if private_drive_rows:
        scenario["avg_route_distance_m"] = (
            sum(float(route.get("distance_m", 0.0) or 0.0) for route in truncated_routes) / len(truncated_routes)
            if truncated_routes else 0.0
        )
        scenario["avg_route_duration_s"] = (
            sum(float(route.get("time_s", 0.0) or 0.0) for route in truncated_routes) / len(truncated_routes)
            if truncated_routes else 0.0
        )
    summary = None
    if private_drive_rows:
        drive_times = [float(item.get("private_drive_time_s", 0.0) or 0.0) for item in private_drive_rows]
        drive_distances = [float(item.get("private_drive_distance_m", 0.0) or 0.0) for item in private_drive_rows]
        top_item = max(private_drive_rows, key=lambda item: float(item.get("private_drive_time_s", 0.0) or 0.0))
        summary = {
            "private_drive_stop_count": len(private_drive_rows),
            "avg_private_drive_time_s": sum(drive_times) / len(drive_times),
            "max_private_drive_time_s": max(drive_times),
            "avg_private_drive_distance_m": sum(drive_distances) / len(drive_distances),
            "max_private_drive_distance_m": max(drive_distances),
            "furthest_stop_address": str(top_item.get("address", "")).strip(),
            "furthest_pickup_address": str(top_item.get("pickup_address", "")).strip(),
            "furthest_pickup_route_id": str(top_item.get("pickup_route_id", "")).strip(),
        }
    return scenario, summary


def prepare_client_payload(
    input_records: list[dict[str, Any]] | list[str],
    config: PlannerConfig | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    normalized_records = _normalize_input_records(input_records)
    if not normalized_records:
        raise ValueError("Address list is empty.")

    config = config or PlannerConfig()
    planner = load_legacy_planner()
    _apply_config(planner, config, normalized_records)

    log_stream = _StreamingLogCapture(callback=progress_callback)
    started_at = time.perf_counter()
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        planner.CURRENT_CURRENCY_CODE = planner.determine_currency_code(normalized_records)
        planner.log("[CLIENT] Preparing client-side data before backend submission.")
        points, geocode_warnings = planner.geocode_records(normalized_records)
        planner.log(f"Valid stops: {len(points)} / {len(normalized_records)}")
        original_points = [dict(point) for point in points]
        for idx, point in enumerate(original_points):
            point["node_id"] = idx
        subway_points = (
            planner.build_subway_aggregated_points(original_points)
            if config.include_subway_aggregation_scenario
            else []
        )
        nearby_points = (
            planner.build_nearby_aggregated_points(original_points)
            if config.include_nearby_aggregation_scenario
            else []
        )
    log_stream.flush()

    return {
        "prepared_payload": {
            "input_records": normalized_records,
            "currency_code": str(planner.CURRENT_CURRENCY_CODE),
            "original_points": original_points,
            "subway_points": subway_points,
            "nearby_points": nearby_points,
        },
        "logs": log_stream.getvalue(),
        "geocode_warnings": geocode_warnings,
        "excluded_stops": build_excluded_stops_from_warnings(geocode_warnings),
        "elapsed_seconds": time.perf_counter() - started_at,
    }


def _compute_scenario_without_render(
    planner: Any,
    points: list[dict[str, Any]],
    scenario_label: str,
    bus_type_configs: list[dict[str, Any]] | None = None,
    node_time_upper_bounds_builder: Any | None = None,
    time_constraint_metadata: dict[str, Any] | None = None,
    route_traffic_attribution_context: dict[str, Any] | None = None,
    route_traffic_fallback_multiplier: float | None = None,
) -> dict[str, Any]:
    if len(points) <= 1:
        routes: list[dict[str, Any]] = []
        result = planner.build_scenario_result(points, routes, "")
        result["output_html"] = ""
        return result

    planner.log(f"[BACKEND] Building {scenario_label} scenario with {len(points)} total points.")
    previous_bus_type_configs = deepcopy(getattr(planner, "BUS_TYPE_CONFIGS", []))
    previous_node_time_upper_bounds = deepcopy(getattr(planner, "NODE_TIME_UPPER_BOUNDS", {}))
    previous_min_solver_vehicle_count = int(getattr(planner, "MIN_SOLVER_VEHICLE_COUNT", 0) or 0)
    if bus_type_configs is not None:
        planner.BUS_TYPE_CONFIGS = deepcopy(bus_type_configs)
    full_fleet = planner.build_vehicle_fleet()
    if full_fleet:
        max_physical_capacity = max(planner.solver_capacity_for_vehicle(item) for item in full_fleet)
        expanded_points = planner.split_oversized_demand_points(points, max_physical_capacity)
        if len(expanded_points) != len(points):
            planner.log(
                f"[BACKEND] Split oversized demand stops for {scenario_label}: "
                f"{len(points)} points -> {len(expanded_points)} solver points."
            )
        points = expanded_points
    scenario_osrm_base_url = planner.resolve_osrm_base_url(points)
    previous_osrm_base_url = planner.OSRM_BASE_URL
    planner.OSRM_BASE_URL = scenario_osrm_base_url
    planner.log(f"[BACKEND] Using OSRM backend {scenario_osrm_base_url} for {scenario_label} scenario.")
    try:
        try:
            planner.log(f"[BACKEND] Building full OSRM matrix for {scenario_label} scenario.")
            solve_time, solve_distance = planner.build_osrm_full_matrix(points)
        except Exception as exc:
            planner.log(f"[WARN] OSRM full matrix failed for {scenario_label}; falling back to seed matrix: {exc}")
            solve_time, solve_distance = planner.seed_edge_metrics(points)
        scenario_constraint_metadata = deepcopy(time_constraint_metadata or {})
        if node_time_upper_bounds_builder is not None:
            node_time_upper_bounds = node_time_upper_bounds_builder(points)
            planner.NODE_TIME_UPPER_BOUNDS = dict(node_time_upper_bounds)
            planner.MIN_SOLVER_VEHICLE_COUNT = max(
                0,
                int(scenario_constraint_metadata.get("current_route_count", 0) or 0),
            )
            scenario_constraint_metadata.update(
                {
                    "enabled": bool(node_time_upper_bounds),
                    "bounded_solver_stop_count": len(node_time_upper_bounds),
                    "min_solver_vehicle_count": int(planner.MIN_SOLVER_VEHICLE_COUNT),
                }
            )
            if isinstance(time_constraint_metadata, dict):
                time_constraint_metadata.update(scenario_constraint_metadata)
            planner.log(
                f"[BACKEND] Applied {len(node_time_upper_bounds)} stop time-impact "
                f"constraint(s) for {scenario_label}; solver vehicle floor "
                f"{planner.MIN_SOLVER_VEHICLE_COUNT}."
            )
            if not node_time_upper_bounds:
                raise RuntimeError("No solver stops matched the current-plan time-impact constraints.")
        else:
            planner.NODE_TIME_UPPER_BOUNDS = {}
            planner.MIN_SOLVER_VEHICLE_COUNT = 0
        final_routes = planner.solve_routes(points, solve_time, solve_distance)
        planner.enrich_routes_with_actual_driving(points, final_routes)
        route_attribution_estimates = apply_attributed_traffic_to_scenario_routes(
            final_routes,
            route_traffic_attribution_context,
            float(route_traffic_fallback_multiplier or getattr(planner, "TRAFFIC_TIME_MULTIPLIER", 1.0) or 1.0),
            scenario_label,
            points=points,
        )
        planner.annotate_and_price_routes(points, final_routes)
        result = planner.build_scenario_result(points, final_routes, "")
        result["output_html"] = ""
        if route_attribution_estimates:
            result["traffic_route_attribution"] = {
                "enabled": True,
                "method": "route_similarity",
                "scenario": scenario_label,
                "route_count": len(route_attribution_estimates),
                "observed_route_sample_count": int(
                    dict(route_traffic_attribution_context or {}).get("observed_route_sample_count", 0) or 0
                ),
                "route_estimates": route_attribution_estimates,
            }
        if scenario_constraint_metadata:
            result["time_constraint"] = scenario_constraint_metadata
        return result
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url
        planner.BUS_TYPE_CONFIGS = previous_bus_type_configs
        planner.NODE_TIME_UPPER_BOUNDS = previous_node_time_upper_bounds
        planner.MIN_SOLVER_VEHICLE_COUNT = previous_min_solver_vehicle_count


def _build_skipped_scenario_result(reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        **dict(extra or {}),
    }
    result.update(
        {
            "points": None,
            "routes": None,
            "output_html": "",
            "bus_count": 0,
            "stop_count": 0,
            "service_stop_count": 0,
            "map_point_count": 0,
            "bus_mix": {},
            "enabled": False,
            "skipped_reason": reason,
        }
    )
    return result

def _attach_free_baseline_metadata(
    result: dict[str, Any],
    config: PlannerConfig,
    bus_type_configs: list[dict[str, Any]],
) -> dict[str, Any]:
    enriched = deepcopy(result)
    enriched["baseline_name"] = "free_optimization_baseline"
    enriched["configured_bus_type_max_counts"] = {
        str(item["name"]): int(item["max_count"])
        for item in bus_type_configs
    }
    enriched["configured_vehicle_ratio"] = {
        str(config.large_bus_name).strip() or "Large Bus": float(config.free_baseline_large_bus_ratio),
        str(config.mid_bus_name).strip() or "Mid Bus": float(config.free_baseline_mid_bus_ratio),
        str(config.small_bus_name).strip() or "Small Bus": float(config.free_baseline_small_bus_ratio),
    }
    return enriched


def _normalize_time_constraint_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _point_time_constraint_keys(point: dict[str, Any], fallback_index: int) -> list[str]:
    keys: list[str] = []
    try:
        node_id = int(point.get("node_id", fallback_index))
        keys.append(f"node:{node_id}")
    except (TypeError, ValueError):
        pass

    demand_batch_key = _normalize_time_constraint_text(point.get("demand_batch_key"))
    if demand_batch_key:
        keys.append(f"batch:{demand_batch_key}")

    country = _normalize_time_constraint_text(point.get("country"))
    city = _normalize_time_constraint_text(point.get("city"))
    for field in ("requested_address", "display_address", "address"):
        address = _normalize_time_constraint_text(point.get(field))
        if not address:
            continue
        if country or city:
            keys.append(f"addr:{country}|{city}|{address}")
        keys.append(f"addr:{address}")
    return keys


def _route_nodes_for_time_constraint(route_summary: dict[str, Any], service_direction: str) -> list[int]:
    route_nodes = [int(item) for item in list(route_summary.get("matched_node_ids") or [])]
    if not route_nodes:
        return []
    if normalize_service_direction(service_direction) == "To School":
        route_nodes = list(reversed(route_nodes))
    if 0 not in route_nodes:
        return []
    depot_index = route_nodes.index(0)
    return route_nodes[depot_index:]


def _build_time_acceptance_constraint_builder(
    current_plan_assessment: dict[str, Any] | None,
    original_points: list[dict[str, Any]],
    assessment_time: list[list[float]] | None,
    service_direction: str,
    threshold_minutes: float,
) -> tuple[Any | None, dict[str, Any]]:
    threshold_seconds = max(0, int(round(float(threshold_minutes) * 60.0)))
    metadata: dict[str, Any] = {
        "enabled": False,
        "threshold_minutes": float(threshold_minutes),
        "threshold_seconds": threshold_seconds,
        "source": "current_plan",
        "service_direction": normalize_service_direction(service_direction),
        "current_route_count": int(current_plan_assessment.get("route_count", 0) or 0)
        if current_plan_assessment
        else 0,
        "bounded_current_stop_count": 0,
        "bounded_solver_stop_count": 0,
    }
    if not current_plan_assessment:
        metadata["skipped_reason"] = "No current plan assessment was available."
        return None, metadata
    if not assessment_time:
        metadata["skipped_reason"] = "No current-plan timing matrix was available."
        return None, metadata
    if len(original_points) <= 1:
        metadata["skipped_reason"] = "No service stops were available for time-impact constraints."
        return None, metadata

    working_time = (
        transpose_matrix(assessment_time)
        if normalize_service_direction(service_direction) == "To School"
        else assessment_time
    )
    limit_by_key: dict[str, int] = {}
    bounded_nodes: set[int] = set()

    for route_summary in list(current_plan_assessment.get("route_summaries") or []):
        route_nodes = _route_nodes_for_time_constraint(route_summary, service_direction)
        if len(route_nodes) <= 1:
            continue
        cumulative_s = 0.0
        for from_node, to_node in zip(route_nodes[:-1], route_nodes[1:]):
            if (
                from_node < 0
                or to_node < 0
                or from_node >= len(working_time)
                or to_node >= len(working_time)
            ):
                continue
            cumulative_s += float(working_time[from_node][to_node] or 0.0)
            if to_node == 0 or to_node >= len(original_points):
                continue
            bounded_nodes.add(to_node)
            point = dict(original_points[to_node] or {})
            upper_bound_s = int(round(cumulative_s + threshold_seconds))
            for key in _point_time_constraint_keys(point, to_node):
                existing = limit_by_key.get(key)
                if existing is None or upper_bound_s < existing:
                    limit_by_key[key] = upper_bound_s

    if not limit_by_key:
        metadata["skipped_reason"] = "No matched current-plan stops could be converted into time constraints."
        return None, metadata

    metadata["enabled"] = True
    metadata["bounded_current_stop_count"] = len(bounded_nodes)

    def build_node_time_upper_bounds(solver_points: list[dict[str, Any]]) -> dict[int, int]:
        bounds: dict[int, int] = {}
        for index, point in enumerate(list(solver_points or [])):
            if index == 0 or bool(dict(point or {}).get("is_depot")):
                continue
            matched_limits = [
                limit_by_key[key]
                for key in _point_time_constraint_keys(dict(point or {}), index)
                if key in limit_by_key
            ]
            if matched_limits:
                bounds[index] = min(matched_limits)
        return bounds

    return build_node_time_upper_bounds, metadata


def run_backend_planner_with_prepared_data(
    prepared_payload: dict[str, Any],
    config: PlannerConfig | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    config = create_request_scoped_config(config or PlannerConfig())
    input_records = _normalize_input_records(prepared_payload.get("input_records") or [])
    if not input_records:
        raise ValueError("Prepared payload does not contain any input records.")

    planner = load_legacy_planner()
    _apply_config(planner, config, input_records)
    planner.CURRENT_CURRENCY_CODE = str(prepared_payload.get("currency_code") or planner.determine_currency_code(input_records))
    traffic_profile_name, traffic_time_multiplier, traffic_profile_context = resolve_traffic_profile(
        config.traffic_profile_name,
        input_records,
    )
    live_traffic_sample = summarize_live_traffic_samples(
        service_direction=config.service_direction,
        input_records=input_records,
    )
    if live_traffic_sample:
        traffic_profile_name = str(live_traffic_sample["traffic_profile_name"])
        traffic_time_multiplier = float(live_traffic_sample["traffic_time_multiplier"])
        traffic_profile_context = str(live_traffic_sample["traffic_profile_context"])

    original_points = deepcopy(prepared_payload.get("original_points") or [])
    subway_points = deepcopy(prepared_payload.get("subway_points") or [])
    nearby_points = deepcopy(prepared_payload.get("nearby_points") or [])
    current_plan = deepcopy(prepared_payload.get("current_plan") or {})

    log_stream = _StreamingLogCapture(callback=progress_callback)
    started_at = time.perf_counter()
    traffic_calibration: dict[str, Any] = {}
    traffic_attribution: dict[str, Any] = {
        "enabled": False,
        "mode": normalize_traffic_coefficient_mode(config.traffic_coefficient_mode),
        "reason": "legacy_mode",
    }
    route_traffic_attribution_context: dict[str, Any] = {}
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        planner.log(
            f"[BACKEND] Starting route optimization for service direction `{normalize_service_direction(config.service_direction)}`."
        )
        try:
            if normalize_traffic_coefficient_mode(config.traffic_coefficient_mode) == TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED:
                route_traffic_attribution_context = build_traffic_attribution_context(
                    input_records,
                    config,
                )
                traffic_attribution = resolve_attributed_traffic_profile(
                    planner,
                    current_plan,
                    original_points,
                    input_records,
                    config,
                    traffic_profile_name,
                    traffic_time_multiplier,
                    traffic_profile_context,
                )
                traffic_calibration = {"enabled": False, "reason": "attributed_coefficient_mode"}
                if traffic_attribution.get("succeeded"):
                    traffic_profile_name = str(traffic_attribution["traffic_profile_name"])
                    traffic_time_multiplier = float(traffic_attribution["traffic_time_multiplier"])
                    traffic_profile_context = str(traffic_attribution["traffic_profile_context"])
                    planner.log(
                        f"[BACKEND] Attributed traffic coefficient selected {traffic_time_multiplier:.2f}x "
                        f"({traffic_attribution.get('confidence')} confidence, "
                        f"{traffic_attribution.get('observed_route_sample_count')} observed route sample(s))."
                    )
                else:
                    planner.log(
                        "[WARN] Attributed traffic coefficient did not produce a usable factor; "
                        f"falling back to {traffic_time_multiplier:.2f}x."
                    )
                planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
                planner.TRAFFIC_TIME_MULTIPLIER = traffic_time_multiplier
                planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
            else:
                traffic_calibration = calibrate_peak_traffic_multiplier(
                    planner,
                    current_plan,
                    original_points,
                    input_records,
                    config,
                    traffic_profile_name,
                    traffic_time_multiplier,
                    traffic_profile_context,
                )
                if traffic_calibration.get("succeeded"):
                    traffic_profile_name = str(traffic_calibration["traffic_profile_name"])
                    traffic_time_multiplier = float(traffic_calibration["traffic_time_multiplier"])
                    traffic_profile_context = str(traffic_calibration["traffic_profile_context"])
                    planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
                    planner.TRAFFIC_TIME_MULTIPLIER = traffic_time_multiplier
                    planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
                    planner.log(
                        f"[BACKEND] AMap peak calibration selected {traffic_time_multiplier:.2f}x "
                        f"for {traffic_calibration.get('selected_period')} "
                        f"({traffic_calibration.get('sampled_edge_count')} edge(s))."
                    )
                elif traffic_calibration.get("enabled"):
                    planner.log(
                        "[WARN] AMap peak calibration did not produce a usable factor; "
                        f"falling back to {traffic_time_multiplier:.2f}x."
                    )
                else:
                    reason = str(traffic_calibration.get("reason", "not_applicable"))
                    planner.log(f"[BACKEND] AMap peak calibration skipped: {reason}.")
            if live_traffic_sample and not traffic_calibration.get("succeeded") and not traffic_attribution.get("succeeded"):
                planner.log(
                    f"[BACKEND] Live traffic sample selected {traffic_time_multiplier:.2f}x "
                    f"for {live_traffic_sample.get('city')} {live_traffic_sample.get('period')} "
                    f"({live_traffic_sample.get('route_sample_count')} route sample(s))."
                )
        except Exception as exc:
            traffic_calibration = {
                "enabled": True,
                "succeeded": False,
                "reason": "calibration_exception",
                "error": str(exc),
                "fallback_profile_name": traffic_profile_name,
                "fallback_multiplier": float(traffic_time_multiplier),
                "fallback_context": traffic_profile_context,
            }
            traffic_attribution = {
                "enabled": normalize_traffic_coefficient_mode(config.traffic_coefficient_mode) == TRAFFIC_COEFFICIENT_MODE_ATTRIBUTED,
                "succeeded": False,
                "mode": normalize_traffic_coefficient_mode(config.traffic_coefficient_mode),
                "reason": "coefficient_selection_exception",
                "error": str(exc),
            }
            planner.log(f"[WARN] Traffic coefficient selection failed; falling back to fixed traffic profile: {exc}")
        planner.log(
            f"[BACKEND] Using traffic profile `{traffic_profile_name}` "
            f"with travel-time multiplier {traffic_time_multiplier:.2f}x "
            f"({traffic_profile_context})."
        )
        free_baseline_bus_type_configs = build_free_baseline_bus_type_configs(config)
        original_result = _compute_scenario_without_render(
            planner,
            original_points,
            "Free optimization baseline",
            bus_type_configs=free_baseline_bus_type_configs,
            route_traffic_attribution_context=route_traffic_attribution_context,
            route_traffic_fallback_multiplier=traffic_time_multiplier,
        )
        free_optimization_baseline = _attach_free_baseline_metadata(
            original_result,
            config,
            free_baseline_bus_type_configs,
        )
        if config.include_subway_aggregation_scenario:
            subway_result = _compute_scenario_without_render(
                planner,
                subway_points,
                "Subway aggregated",
                route_traffic_attribution_context=route_traffic_attribution_context,
                route_traffic_fallback_multiplier=traffic_time_multiplier,
            )
        else:
            planner.log("[BACKEND] Skipping subway aggregation scenario for this run.")
            subway_result = _build_skipped_scenario_result(
                "Subway alternative baseline was disabled for this run."
            )
        if config.include_nearby_aggregation_scenario:
            nearby_result = _compute_scenario_without_render(
                planner,
                nearby_points,
                "Nearby-address aggregated",
                route_traffic_attribution_context=route_traffic_attribution_context,
                route_traffic_fallback_multiplier=traffic_time_multiplier,
            )
        else:
            planner.log("[BACKEND] Skipping nearby aggregation scenario for this run.")
            nearby_result = _build_skipped_scenario_result(
                "Nearby alternative baseline was disabled for this run."
            )
        assessment_time = None
        assessment_distance = None
        if current_plan:
            assessment_time, assessment_distance = _build_assessment_metric_matrices(planner, original_points)
        current_plan_assessment = assess_current_plan(
            planner,
            current_plan,
            original_points,
            config,
            solve_time=assessment_time,
            solve_distance=assessment_distance,
            traffic_calibration=traffic_calibration,
        )
        current_plan_scenario = build_current_plan_map_scenario(
            planner,
            current_plan_assessment,
            original_points,
        )
        time_constraint_builder, time_constraint_metadata = _build_time_acceptance_constraint_builder(
            current_plan_assessment,
            original_points,
            assessment_time,
            config.service_direction,
            TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES,
        )
        if time_constraint_builder is not None:
            try:
                time_constrained_result = _compute_scenario_without_render(
                    planner,
                    original_points,
                    "15-minute time-impact constrained optimization",
                    bus_type_configs=free_baseline_bus_type_configs,
                    node_time_upper_bounds_builder=time_constraint_builder,
                    time_constraint_metadata=time_constraint_metadata,
                    route_traffic_attribution_context=route_traffic_attribution_context,
                    route_traffic_fallback_multiplier=traffic_time_multiplier,
                )
                time_constrained_result["baseline_name"] = "time_constrained_optimization"
                time_constrained_result["time_constraint"] = {
                    **dict(time_constrained_result.get("time_constraint") or {}),
                    **time_constraint_metadata,
                    "enabled": True,
                    "bounded_solver_stop_count": int(
                        dict(time_constrained_result.get("time_constraint") or {}).get(
                            "bounded_solver_stop_count",
                            time_constraint_metadata.get("bounded_solver_stop_count", 0),
                        )
                        or 0
                    ),
                }
            except Exception as exc:
                planner.log(f"[WARN] 15-minute time-impact constrained optimization skipped: {exc}")
                time_constrained_result = _build_skipped_scenario_result(
                    f"15-minute time-impact constrained optimization was infeasible: {exc}",
                    {
                        "time_constraint": {
                            **time_constraint_metadata,
                            "enabled": False,
                            "error": str(exc),
                        }
                    },
                )
        else:
            time_constrained_result = _build_skipped_scenario_result(
                str(time_constraint_metadata.get("skipped_reason") or "Time-impact constraints were not available."),
                {"time_constraint": time_constraint_metadata},
            )
        route_reallocation_analysis = analyze_route_reallocation_opportunities(
            planner,
            current_plan,
            current_plan_assessment,
            original_points,
            config,
            solve_time=assessment_time,
            solve_distance=assessment_distance,
        )
        current_plan_comparison = compare_current_plan_to_baseline(current_plan_assessment, free_optimization_baseline)
        nearby_private_access_analysis = analyze_nearby_private_access(
            planner,
            original_points,
            nearby_points,
            nearby_result,
        )
        nearby_result["outlying_private_access_rows"] = list(
            (nearby_private_access_analysis or {}).get("rows") or []
        )
        service_direction = normalize_service_direction(config.service_direction)
        further_most_result, further_most_private_access_analysis = build_further_most_stop_scenario(
            planner,
            original_points,
            original_result,
            service_direction=service_direction,
        )
        further_most_nearby_result, further_most_nearby_private_access_analysis = build_further_most_stop_scenario(
            planner,
            nearby_points,
            nearby_result,
            service_direction=service_direction,
        )
        if traffic_attribution.get("succeeded"):
            scenario_route_estimates: dict[str, Any] = {}
            for scenario_key, scenario_result in (
                ("free_optimization_baseline", free_optimization_baseline),
                ("subway", subway_result),
                ("nearby", nearby_result),
                ("time_constrained", time_constrained_result),
            ):
                route_attribution = dict((scenario_result or {}).get("traffic_route_attribution") or {})
                if route_attribution:
                    scenario_route_estimates[scenario_key] = route_attribution
            if scenario_route_estimates:
                traffic_attribution["route_level_applied"] = True
                traffic_attribution["route_level_note"] = (
                    "Scenario routes use their own attributed traffic factor; "
                    "the job-level multiplier is retained as a solver/fallback coefficient."
                )
                traffic_attribution["scenario_route_estimates"] = scenario_route_estimates
    log_stream.flush()

    service_original_points = [point for point in original_points if not bool(point.get("is_depot"))]
    input_address_review = build_input_address_review(
        planner,
        original_points,
        service_direction=service_direction,
        current_plan_assessment=current_plan_assessment,
        current_plan_distance_matrix=assessment_distance,
        current_plan_routes=list((current_plan_scenario or {}).get("routes") or []),
    )

    structured_results = {
        "original": original_result,
        "current_plan": current_plan_scenario,
        "subway": subway_result,
        "nearby": nearby_result,
        "time_constrained": time_constrained_result,
        "further_most": further_most_result,
        "further_most_nearby": further_most_nearby_result,
        "input_address_count": len([item for item in input_records if int(item.get("passenger_count", 0) or 0) > 0]),
        "input_point_count": len(input_records),
        "valid_stop_count": len(service_original_points),
        "valid_point_count": len(original_points),
        "currency_code": planner.CURRENT_CURRENCY_CODE,
        "job_id": config.output_directory_name,
        "current_plan_assessment": current_plan_assessment,
        "current_plan_scenario": current_plan_scenario,
        "free_optimization_baseline": free_optimization_baseline,
        "time_constrained_optimization": time_constrained_result,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "further_most_private_access_analysis": further_most_private_access_analysis,
        "further_most_nearby_private_access_analysis": further_most_nearby_private_access_analysis,
        "input_address_review": input_address_review,
        "traffic_profile_name": traffic_profile_name,
        "traffic_time_multiplier": traffic_time_multiplier,
        "traffic_profile_context": traffic_profile_context,
        "traffic_coefficient_mode": normalize_traffic_coefficient_mode(config.traffic_coefficient_mode),
        "traffic_attribution": traffic_attribution,
        "traffic_calibration": traffic_calibration,
        "live_traffic_sample": live_traffic_sample,
        "service_direction": normalize_service_direction(config.service_direction),
    }
    structured_results = attach_output_paths_to_structured_results(structured_results, config)
    service_input_record_count = len([item for item in input_records if int(item.get("passenger_count", 0) or 0) > 0])
    return {
        "structured_results": structured_results,
        "summary": summarize_structured_results(structured_results, service_input_record_count),
        "logs": log_stream.getvalue(),
        "elapsed_seconds": time.perf_counter() - started_at,
        "current_plan_assessment": current_plan_assessment,
        "current_plan_scenario": current_plan_scenario,
        "free_optimization_baseline": free_optimization_baseline,
        "time_constrained_optimization": time_constrained_result,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "further_most_private_access_analysis": further_most_private_access_analysis,
        "further_most_nearby_private_access_analysis": further_most_nearby_private_access_analysis,
        "input_address_review": input_address_review,
        "traffic_profile_name": traffic_profile_name,
        "traffic_time_multiplier": traffic_time_multiplier,
        "traffic_profile_context": traffic_profile_context,
        "traffic_coefficient_mode": normalize_traffic_coefficient_mode(config.traffic_coefficient_mode),
        "traffic_attribution": traffic_attribution,
        "traffic_calibration": traffic_calibration,
        "live_traffic_sample": live_traffic_sample,
        "service_direction": normalize_service_direction(config.service_direction),
    }


def attach_output_paths_to_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    hydrated = deepcopy(results)
    # Persisted job records may carry absolute output paths from another
    # machine. Rebuild paths for the current runtime before rerendering.
    scoped_config = deepcopy(config)
    if hydrated.get("job_id") and not scoped_config.output_directory_name:
        scoped_config.output_directory_name = str(hydrated["job_id"])
    path_map = build_output_path_map(scoped_config)
    for scenario_key, output_html in path_map.items():
        scenario = hydrated.setdefault(scenario_key, {})
        scenario["output_html"] = output_html
    hydrated["output_paths"] = path_map
    return hydrated


def submit_prepared_payload_to_backend(
    prepared_payload: dict[str, Any],
    config: PlannerConfig,
    backend_base_url: str,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    compute_url = urljoin(backend_base_url.rstrip("/") + "/", "compute")
    response = requests.post(
        compute_url,
        json={
            "config": asdict(config),
            "prepared_payload": prepared_payload,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "structured_results" not in payload:
        raise RuntimeError("Backend returned an unexpected response payload.")
    return payload

def friendly_error_message(exc: Exception) -> str:
    message = str(exc)

    if "depot/start point" in message:
        return "The depot address could not be geocoded. Please check that the first address in Excel is complete and searchable."
    if "No usable addresses found" in message:
        return "The selected address column does not contain any usable addresses. Please make sure it includes non-empty, complete addresses."
    if "Column not found" in message:
        return "The selected address column could not be found. Please choose the correct column name."
    if "Passenger count is invalid" in message or "Passenger count cannot be negative" in message:
        return "One or more passenger-count values are invalid. Please use non-negative whole numbers in the selected passenger-count column."
    if "No feasible fleet composition exists under the configured Large / Mid / Small bus max-count limits" in message:
        return "The configured Large / Mid / Small bus max-count limits do not provide enough vehicles for this run. Increase the available fleet counts or reduce the stop set."
    if "OR-Tools could not find a feasible routing solution" in message:
        return "No feasible routing plan was found under the current constraints. This can happen when too many stops effectively need dedicated vehicles, too many edges are unreachable, or the available fleet is too small."
    if "Unable to read Excel" in message or "Unable to read worksheet" in message:
        return "The Excel file could not be read. Please make sure the file is not corrupted and that the worksheet format is valid."

    return message


def run_legacy_planner_with_addresses(
    input_records: list[dict[str, Any]] | list[str],
    config: PlannerConfig | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    normalized_records = _normalize_input_records(input_records)
    if not normalized_records:
        raise ValueError("Address list is empty.")

    config = config or PlannerConfig()
    planner = load_legacy_planner()
    output_paths = _apply_config(planner, config, normalized_records)
    original_addresses = [record["address"] for record in normalized_records]

    log_stream = _StreamingLogCapture(callback=progress_callback)
    started_at = time.perf_counter()
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        run_results = planner.main()
    log_stream.flush()
    elapsed_seconds = time.perf_counter() - started_at

    logs = log_stream.getvalue()
    structured_results = run_results or getattr(planner, "LAST_RUN_RESULTS", {})
    service_normalized_record_count = len([item for item in normalized_records if int(item.get("passenger_count", 0) or 0) > 0])
    if structured_results:
        summary = summarize_structured_results(structured_results, service_normalized_record_count)
    else:
        summary = summarize_logs(logs, service_normalized_record_count)

    result = {
        "original_html": output_paths["original_html"],
        "subway_html": output_paths["subway_html"],
        "nearby_html": output_paths["nearby_html"],
        "logs": logs,
        "summary": summary,
        "excluded_stops": extract_excluded_stops(logs, original_addresses),
        "geocode_warnings": extract_geocode_warnings(logs, original_addresses),
        "elapsed_seconds": elapsed_seconds,
        "structured_results": structured_results,
        "cache_hit": False,
    }
    return result
