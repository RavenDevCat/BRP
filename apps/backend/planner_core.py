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
    from .api_rate_limit import CrossProcessRateLimiter
    from .BusingProblem import transpose_matrix
    from .json_cache_store import clear_json_object, load_json_object, save_json_object
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from api_rate_limit import CrossProcessRateLimiter
    from BusingProblem import transpose_matrix
    from json_cache_store import clear_json_object, load_json_object, save_json_object
import requests


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent
LEGACY_PLANNER_PATH = BASE_DIR / "BusingProblem.py"
OUTPUT_DIR = BASE_DIR / "outputs"
CACHE_DIR = Path(os.environ.get("BRP_BACKEND_CACHE_DIR", str(BASE_DIR / "cache"))).expanduser()
PLANNER_RESULT_CACHE_PATH = CACHE_DIR / "planner_result_cache.json"
ROUTE_METRICS_CACHE_PATH = CACHE_DIR / "route_metrics_cache.json"
ROUTE_GEOMETRY_CACHE_PATH = CACHE_DIR / "route_geometry_cache.json"
FINAL_ROUTE_TRAFFIC_CACHE_PATH = CACHE_DIR / "final_route_traffic_cache.json"


def _default_runtime_path(*parts: str) -> str:
    if os.name == "nt":
        return str(ROOT_DIR / "state" / Path(*parts))
    return str(Path("/opt/brp/shared/runtime", *parts))


DIRECT_PROVIDER_CACHE_TZ = ZoneInfo("Asia/Shanghai")
KAKAO_NAVI_MAX_QPS = max(
    0.1,
    float(os.environ.get("BRP_KAKAO_NAVI_MAX_QPS", "2.8") or 2.8),
)
KAKAO_NAVI_FINAL_ROUTE_LIMITER = CrossProcessRateLimiter("kakao-navi-final-route", KAKAO_NAVI_MAX_QPS)
KAKAO_NAVI_FUTURE_DIRECTIONS_URL = (
    os.environ.get("BRP_KAKAO_NAVI_FUTURE_DIRECTIONS_URL", "").strip()
    or "https://apis-navi.kakaomobility.com/v1/future/directions"
)
KAKAO_NAVI_API_KEY = (
    os.environ.get("KAKAO_MOBILITY_API_KEY", "").strip()
    or os.environ.get("KAKAO_REST_API_KEY", "").strip()
    or os.environ.get("KAKAO_API_KEY", "").strip()
)
KAKAO_NAVI_PRIORITY = os.environ.get("BRP_KAKAO_NAVI_PRIORITY", "RECOMMEND").strip() or "RECOMMEND"
KAKAO_NAVI_MAX_WAYPOINTS = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_MAX_WAYPOINTS", "5") or 5),
)
KAKAO_NAVI_TIMEOUT_SECONDS = max(
    1,
    int(os.environ.get("BRP_KAKAO_NAVI_TIMEOUT_SECONDS", "20") or 20),
)
KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS", "0") or 0),
)
FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED = os.environ.get(
    "BRP_FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED",
    "true",
).strip().lower() not in {"0", "false", "no", "off"}
FINAL_ROUTE_TRAFFIC_MAX_CALLS = max(
    0,
    int(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_MAX_CALLS", "40") or 40),
)
FINAL_ROUTE_TRAFFIC_MAX_WAYPOINTS = max(
    0,
    int(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_MAX_WAYPOINTS", "16") or 16),
)
AM_ARRIVAL_GATE_GRACE_MINUTES = max(
    0.0,
    float(os.environ.get("BRP_AM_ARRIVAL_GATE_GRACE_MINUTES", "0") or 0),
)
PM_ROUTE_GATE_GRACE_MINUTES = max(
    0.0,
    float(os.environ.get("BRP_PM_ROUTE_GATE_GRACE_MINUTES", "0") or 0),
)
AM_EARLIEST_DEPARTURE_MINUTES = 6 * 60
AM_LATEST_ARRIVAL_MINUTES = 8 * 60
PM_MAX_ROUTE_WINDOW_MINUTES = max(
    1,
    int(os.environ.get("BRP_PM_MAX_ROUTE_WINDOW_MINUTES", "120") or 120),
)
FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED = os.environ.get(
    "BRP_FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED",
    "true",
).strip().lower() not in {"0", "false", "no", "off"}
FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS = max(
    0,
    int(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS", "2") or 2),
)
FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS = max(
    0,
    int(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS", "5") or 5),
)
FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_REPLAN_ATTEMPTS = max(
    0,
    int(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_REPLAN_ATTEMPTS", "1") or 1),
)
FINAL_ROUTE_TRAFFIC_REPLAN_STEP_MINUTES = max(
    1.0,
    float(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_REPLAN_STEP_MINUTES", "5") or 5),
)
FINAL_ROUTE_TRAFFIC_REPLAN_MAX_STEP_MINUTES = max(
    FINAL_ROUTE_TRAFFIC_REPLAN_STEP_MINUTES,
    float(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_REPLAN_MAX_STEP_MINUTES", "15") or 15),
)
FINAL_ROUTE_TRAFFIC_REPLAN_MIN_TARGET_MINUTES = max(
    10.0,
    float(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_REPLAN_MIN_TARGET_MINUTES", "10") or 10),
)
FINAL_ROUTE_TRAFFIC_REPLAN_TARGET_FLOOR_RATIO = min(
    1.0,
    max(
        0.1,
        float(os.environ.get("BRP_FINAL_ROUTE_TRAFFIC_REPLAN_TARGET_FLOOR_RATIO", "0.7") or 0.7),
    ),
)
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
TRAFFIC_PROFILE_NAMES = {"Off-Peak", "AM Peak", "PM Peak"}
KOREA_TRAFFIC_METRO_CITY = "SEOUL METRO"
KOREA_TRAFFIC_METRO_CITY_ALIASES = {
    "SEOUL",
    "SEOUL SI",
    "SEOUL-SI",
    "SEOUL SPECIAL CITY",
    "서울",
    "서울시",
    "서울특별시",
    "INCHEON",
    "INCHON",
    "INCHEON SI",
    "INCHEON-SI",
    "INCHEON METROPOLITAN CITY",
    "인천",
    "인천시",
    "인천광역시",
    "GYEONGGI",
    "GYEONGGI DO",
    "GYEONGGI-DO",
    "GYEONGGI PROVINCE",
    "KYONGGI",
    "경기",
    "경기도",
    "SEONGNAM",
    "SEONGNAM SI",
    "SEONGNAM-SI",
    "BUNDANG",
    "BUNDANG GU",
    "BUNDANG-GU",
    "성남",
    "성남시",
    "분당",
    "분당구",
    "GIMPO",
    "GIMPO SI",
    "GIMPO-SI",
    "김포",
    "김포시",
    "SUWON",
    "SUWON SI",
    "SUWON-SI",
    "수원",
    "수원시",
    "YONGIN",
    "YONGIN SI",
    "YONGIN-SI",
    "용인",
    "용인시",
    "GOYANG",
    "GOYANG SI",
    "GOYANG-SI",
    "고양",
    "고양시",
    "BUCHEON",
    "BUCHEON SI",
    "BUCHEON-SI",
    "부천",
    "부천시",
    "ANYANG",
    "ANYANG SI",
    "ANYANG-SI",
    "안양",
    "안양시",
    "GWACHEON",
    "GWACHEON SI",
    "GWACHEON-SI",
    "과천",
    "과천시",
    "GWANGMYEONG",
    "GWANGMYEONG SI",
    "GWANGMYEONG-SI",
    "광명",
    "광명시",
    "HANAM",
    "HANAM SI",
    "HANAM-SI",
    "하남",
    "하남시",
    "NAMYANGJU",
    "NAMYANGJU SI",
    "NAMYANGJU-SI",
    "남양주",
    "남양주시",
    "UIJEONGBU",
    "UIJEONGBU SI",
    "UIJEONGBU-SI",
    "의정부",
    "의정부시",
    "PAJU",
    "PAJU SI",
    "PAJU-SI",
    "파주",
    "파주시",
    "HWASEONG",
    "HWASEONG SI",
    "HWASEONG-SI",
    "화성",
    "화성시",
    "OSAN",
    "OSAN SI",
    "OSAN-SI",
    "오산",
    "오산시",
    "SIHEUNG",
    "SIHEUNG SI",
    "SIHEUNG-SI",
    "시흥",
    "시흥시",
    "ANSAN",
    "ANSAN SI",
    "ANSAN-SI",
    "안산",
    "안산시",
    "GUNPO",
    "GUNPO SI",
    "GUNPO-SI",
    "군포",
    "군포시",
    "UIWANG",
    "UIWANG SI",
    "UIWANG-SI",
    "의왕",
    "의왕시",
    "GURI",
    "GURI SI",
    "GURI-SI",
    "구리",
    "구리시",
}
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
    express_threshold_km: float = 15.0
    reserved_express_buses: int = 4
    express_skip_inner_km: float = 8.0
    max_route_duration_minutes: int = 60
    time_impact_limit_minutes: int = int(TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES)
    stop_service_minutes: int = 1
    subway_search_radius_m: int = 1500
    max_subway_walk_distance_m: int = 800
    nearby_cluster_radius_m: int = 500
    comfort_load_factor: float = 1.0
    traffic_profile_name: str = "Off-Peak"
    service_direction: str = "From School"
    to_school_arrival_time: str = "08:00"
    from_school_departure_time: str = "15:40"
    time_window_start: str = "06:30"
    time_window_end: str = "08:00"
    route_stop_limit: int | None = None
    minimum_vehicle_reduction: int = 2
    matrix_nearest_neighbors: int = 10
    matrix_candidate_radius_km: float = 15.0
    operating_cost_per_km: float = 0.0
    revenue_rules: list[dict[str, float | None]] | None = None
    original_output_name: str = "school_bus_routes.html"
    current_plan_output_name: str = "current_plan_routes.html"
    subway_output_name: str = "school_bus_routes_subway_aggregated.html"
    nearby_output_name: str = "school_bus_routes_nearby_aggregated.html"
    time_constrained_output_name: str = "school_bus_routes_time_constrained.html"
    exception_preserving_output_name: str = "school_bus_routes_exception_preserving.html"
    further_most_output_name: str = "school_bus_routes_further_most.html"
    further_most_nearby_output_name: str = "school_bus_routes_further_most_nearby.html"
    output_directory_name: str | None = None
    include_subway_aggregation_scenario: bool = True
    include_nearby_aggregation_scenario: bool = True


@dataclass(frozen=True)
class TrafficPolicy:
    provider: str
    country: str
    city: str
    final_validation_enabled: bool
    final_validation_applicable: bool
    unavailable_reason: str | None = None

    def status(self) -> str:
        if not self.final_validation_enabled:
            return "disabled"
        if not self.final_validation_applicable:
            return "not_applicable"
        if self.unavailable_reason:
            return "unavailable"
        return "ready"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status()
        return payload


def _normalize_location_value(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"KR", "KOREA", "SOUTH KOREA", "REPUBLIC OF KOREA", "KOREA, REPUBLIC OF", "대한민국", "한국"}:
        return "SOUTH KOREA"
    return normalized


def _normalize_traffic_city(country: str | None, city: str | None) -> str:
    normalized_country = _normalize_location_value(country)
    normalized_city = _normalize_location_value(city).replace("_", " ")
    normalized_city = " ".join(normalized_city.replace("-", " ").split())
    if normalized_country == "SOUTH KOREA" and normalized_city in KOREA_TRAFFIC_METRO_CITY_ALIASES:
        return KOREA_TRAFFIC_METRO_CITY
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
    service_direction = normalize_service_direction(config.service_direction)
    if service_direction == "To School":
        window_start = _parse_minutes_clock(config.time_window_start, 6 * 60 + 30)
        window_end = _parse_minutes_clock(config.time_window_end, AM_LATEST_ARRIVAL_MINUTES)
        if window_end > window_start:
            return window_end - window_start
    else:
        window_start, window_end = _from_school_time_window(config)
        if window_end > window_start:
            return window_end - window_start
    return max(1, int(config.max_route_duration_minutes))


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


def normalize_traffic_profile_name(profile_name: str | None) -> str:
    normalized = str(profile_name or "").strip()
    return normalized if normalized in TRAFFIC_PROFILE_NAMES else "Off-Peak"


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


def _parse_minutes_clock(value: Any, default_minutes: int) -> int:
    parts = str(value or "").strip().split(":")
    if len(parts) < 2:
        return default_minutes
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        return default_minutes
    if hours < 0 or minutes < 0 or minutes > 59:
        return default_minutes
    return (hours * 60 + minutes) % (24 * 60)


def _format_minutes_clock(minutes: float | int) -> str:
    total = int(round(float(minutes))) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _to_school_time_window(config: PlannerConfig) -> tuple[int, int]:
    start_minutes = _parse_minutes_clock(config.time_window_start, 6 * 60 + 30)
    end_minutes = _parse_minutes_clock(config.time_window_end, AM_LATEST_ARRIVAL_MINUTES)
    if end_minutes <= start_minutes:
        return 6 * 60 + 30, AM_LATEST_ARRIVAL_MINUTES
    return start_minutes, end_minutes


def _from_school_time_window(config: PlannerConfig) -> tuple[int, int]:
    departure_minutes = _parse_minutes_clock(config.from_school_departure_time, 15 * 60 + 40)
    raw_start = str(config.time_window_start or "").strip()
    raw_end = str(config.time_window_end or "").strip()
    if raw_start == "06:30" and raw_end == "08:00":
        return departure_minutes, departure_minutes + int(PM_MAX_ROUTE_WINDOW_MINUTES)
    start_minutes = _parse_minutes_clock(config.time_window_start, departure_minutes)
    end_minutes = _parse_minutes_clock(config.time_window_end, start_minutes + int(PM_MAX_ROUTE_WINDOW_MINUTES))
    if end_minutes <= start_minutes:
        return start_minutes, start_minutes + int(PM_MAX_ROUTE_WINDOW_MINUTES)
    return start_minutes, end_minutes


def _effective_route_stop_limit(config: PlannerConfig) -> int:
    if config.route_stop_limit is None:
        return 10000
    return max(1, int(config.route_stop_limit))


def _amap_route_point(point: dict[str, Any]) -> tuple[float, float] | None:
    provider = str(point.get("provider") or point.get("geocode_provider") or "").lower()
    if provider == "amap" or str(point.get("adcode") or "").strip():
        lat = _traffic_float(point.get("lat"))
        lng = _traffic_float(point.get("lng"))
        if lat is not None and lng is not None:
            return lat, lng
    return _traffic_point_coordinates(point)


def _route_amap_points(points: list[dict[str, Any]], route: dict[str, Any]) -> list[tuple[float, float]]:
    request_points: list[tuple[float, float]] = []
    for node in list(route.get("nodes") or []):
        try:
            node_index = int(node)
        except (TypeError, ValueError):
            continue
        if node_index < 0 or node_index >= len(points):
            continue
        coords = _amap_route_point(dict(points[node_index] or {}))
        if coords:
            request_points.append(coords)
    return request_points


def _final_route_traffic_cache_key(
    points: list[tuple[float, float]],
    *,
    provider: str = "amap",
    departure_time: datetime | None = None,
) -> str:
    rounded = [[round(lat, 6), round(lng, 6)] for lat, lng in points]
    departure_key = departure_time.isoformat(timespec="minutes") if departure_time else ""
    payload = {"provider": provider, "departure_time": departure_key, "points": rounded}
    digest = hashlib.sha1(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    return f"{provider}-final-route-v2|{digest}"


def _amap_route_segment_stats(planner: Any, request_points: list[tuple[float, float]]) -> dict[str, float]:
    if len(request_points) < 2:
        return {"duration_s": 0.0, "distance_m": 0.0}
    origin_lat, origin_lng = request_points[0]
    dest_lat, dest_lng = request_points[-1]
    params: dict[str, str] = {
        "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
        "destination": f"{dest_lng:.6f},{dest_lat:.6f}",
        "extensions": "base",
        "output": "json",
    }
    waypoint_values = [f"{lng:.6f},{lat:.6f}" for lat, lng in request_points[1:-1]]
    if waypoint_values:
        params["waypoints"] = ";".join(waypoint_values)
    payload = planner.amap_request_json("/v3/direction/driving", params, planner.AMAP_ROUTING_LIMITER)
    paths = list(dict(payload.get("route") or {}).get("paths") or [])
    if not paths:
        return {"duration_s": 0.0, "distance_m": 0.0}
    path = dict(paths[0] or {})
    return {
        "duration_s": float(path.get("duration", 0.0) or 0.0),
        "distance_m": float(path.get("distance", 0.0) or 0.0),
    }


def _amap_route_stats(
    planner: Any,
    request_points: list[tuple[float, float]],
    cache: dict[str, Any],
    state: dict[str, int],
) -> dict[str, Any] | None:
    if len(request_points) < 2:
        return None
    cache_key = _final_route_traffic_cache_key(request_points, provider="amap")
    cached = dict(cache.get(cache_key) or {})
    if cached:
        state["cache_hits"] = int(state.get("cache_hits", 0)) + 1
        return {
            "duration_s": float(cached.get("duration_s", 0.0) or 0.0),
            "distance_m": float(cached.get("distance_m", 0.0) or 0.0),
            "source": "amap_final_route_cache",
            "cache_key": cache_key,
        }
    max_points = max(2, FINAL_ROUTE_TRAFFIC_MAX_WAYPOINTS + 2)
    duration_s = 0.0
    distance_m = 0.0
    start = 0
    while start < len(request_points) - 1:
        end = min(len(request_points), start + max_points)
        stats = None
        for attempt in range(2):
            if int(state.get("api_calls", 0)) >= FINAL_ROUTE_TRAFFIC_MAX_CALLS:
                return None
            state["api_calls"] = int(state.get("api_calls", 0)) + 1
            try:
                stats = _amap_route_segment_stats(planner, request_points[start:end])
                break
            except Exception:
                if attempt:
                    raise
                time.sleep(0.25)
        if stats is None:
            return None
        duration_s += float(stats.get("duration_s", 0.0) or 0.0)
        distance_m += float(stats.get("distance_m", 0.0) or 0.0)
        start = end - 1
    cache[cache_key] = {
        "created_at": datetime.now(DIRECT_PROVIDER_CACHE_TZ).isoformat(timespec="seconds"),
        "duration_s": duration_s,
        "distance_m": distance_m,
        "point_count": len(request_points),
    }
    state["cache_changed"] = 1
    return {
        "duration_s": duration_s,
        "distance_m": distance_m,
        "source": "amap_final_route",
        "cache_key": cache_key,
    }


def _kakao_navi_coord(point: tuple[float, float]) -> str:
    lat, lng = point
    return f"{lng:.7f},{lat:.7f}"


def _kakao_navi_departure_time(value: datetime) -> str:
    return value.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d%H%M")


def _next_service_datetime(minutes: float | int, country: str) -> datetime:
    tz = _traffic_timezone(country)
    now = datetime.now(tz)
    total = int(round(float(minutes))) % (24 * 60)
    candidate = now.replace(hour=total // 60, minute=total % 60, second=0, microsecond=0)
    while candidate <= now + timedelta(minutes=5) or candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _balanced_route_point_chunks(
    route_points: list[tuple[float, float]],
    max_intermediates: int,
) -> list[list[tuple[float, float]]]:
    total_legs = max(0, len(route_points) - 1)
    if total_legs <= 0:
        return []
    max_legs_per_call = max(1, int(max_intermediates) + 1)
    if total_legs <= max_legs_per_call:
        return [route_points]
    chunk_count = (total_legs + max_legs_per_call - 1) // max_legs_per_call
    base_legs = total_legs // chunk_count
    extra = total_legs % chunk_count
    chunks: list[list[tuple[float, float]]] = []
    start = 0
    for index in range(chunk_count):
        leg_count = base_legs + (1 if index < extra else 0)
        end = start + leg_count
        chunks.append(route_points[start : end + 1])
        start = end
    return chunks


def _kakao_route_segment_stats(
    request_points: list[tuple[float, float]],
    departure_time: datetime,
) -> dict[str, float]:
    if len(request_points) < 2:
        return {"duration_s": 0.0, "distance_m": 0.0}
    if not KAKAO_NAVI_API_KEY:
        raise RuntimeError("KAKAO_MOBILITY_API_KEY or KAKAO_REST_API_KEY is required for Kakao Navi final route checks")
    params: dict[str, str] = {
        "origin": _kakao_navi_coord(request_points[0]),
        "destination": _kakao_navi_coord(request_points[-1]),
        "priority": KAKAO_NAVI_PRIORITY,
        "car_fuel": "GASOLINE",
        "car_hipass": "false",
        "alternatives": "false",
        "road_details": "false",
        "departure_time": _kakao_navi_departure_time(departure_time),
    }
    waypoint_values = [_kakao_navi_coord(point) for point in request_points[1:-1]]
    if waypoint_values:
        params["waypoints"] = "|".join(waypoint_values)
    KAKAO_NAVI_FINAL_ROUTE_LIMITER.wait()
    response = requests.get(
        KAKAO_NAVI_FUTURE_DIRECTIONS_URL,
        headers={
            "Authorization": f"KakaoAK {KAKAO_NAVI_API_KEY}",
            "Content-Type": "application/json",
        },
        params=params,
        timeout=KAKAO_NAVI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    routes = list(payload.get("routes") or [])
    if not routes:
        code = payload.get("code") or payload.get("result_code") or payload.get("msg")
        raise RuntimeError(f"Kakao Navi returned no routes: {code or 'empty response'}")
    route = dict(routes[0] or {})
    summary = dict(route.get("summary") or {})
    duration_s = float(summary.get("duration", 0.0) or 0.0)
    distance_m = float(summary.get("distance", 0.0) or 0.0)
    if duration_s <= 0:
        duration_s = sum(float(section.get("duration", 0.0) or 0.0) for section in route.get("sections") or [])
    if distance_m <= 0:
        distance_m = sum(float(section.get("distance", 0.0) or 0.0) for section in route.get("sections") or [])
    if duration_s <= 0:
        raise RuntimeError("Kakao Navi returned no usable duration")
    return {"duration_s": duration_s, "distance_m": distance_m}


def _kakao_route_stats(
    request_points: list[tuple[float, float]],
    cache: dict[str, Any],
    state: dict[str, int],
    *,
    departure_time: datetime,
) -> dict[str, Any] | None:
    if len(request_points) < 2:
        return None
    cache_key = _final_route_traffic_cache_key(
        request_points,
        provider="kakao_navi",
        departure_time=departure_time,
    )
    cached = dict(cache.get(cache_key) or {})
    if cached:
        state["cache_hits"] = int(state.get("cache_hits", 0)) + 1
        return {
            "duration_s": float(cached.get("duration_s", 0.0) or 0.0),
            "distance_m": float(cached.get("distance_m", 0.0) or 0.0),
            "source": "kakao_navi_final_route_cache",
            "cache_key": cache_key,
            "departure_time": cached.get("departure_time") or departure_time.isoformat(timespec="seconds"),
            "segment_count": int(cached.get("segment_count", 0) or 0),
        }
    chunks = _balanced_route_point_chunks(request_points, KAKAO_NAVI_MAX_WAYPOINTS)
    duration_s = 0.0
    distance_m = 0.0
    current_departure = departure_time
    segment_rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        if int(state.get("api_calls", 0)) >= FINAL_ROUTE_TRAFFIC_MAX_CALLS:
            return None
        stats = _kakao_route_segment_stats(chunk, current_departure)
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        segment_duration_s = float(stats.get("duration_s", 0.0) or 0.0)
        segment_distance_m = float(stats.get("distance_m", 0.0) or 0.0)
        duration_s += segment_duration_s
        distance_m += segment_distance_m
        segment_rows.append(
            {
                "segment_index": index,
                "point_count": len(chunk),
                "departure_time": current_departure.isoformat(timespec="seconds"),
                "duration_s": segment_duration_s,
                "distance_m": segment_distance_m,
            }
        )
        current_departure = current_departure + timedelta(
            seconds=segment_duration_s + KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS
        )
    cache[cache_key] = {
        "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "duration_s": duration_s,
        "distance_m": distance_m,
        "point_count": len(request_points),
        "departure_time": departure_time.isoformat(timespec="seconds"),
        "segment_count": len(segment_rows),
        "segments": segment_rows,
    }
    state["cache_changed"] = 1
    return {
        "duration_s": duration_s,
        "distance_m": distance_m,
        "source": "kakao_navi_final_route",
        "cache_key": cache_key,
        "departure_time": departure_time.isoformat(timespec="seconds"),
        "segment_count": len(segment_rows),
        "segments": segment_rows,
    }


def _route_provider_departure_datetime(
    *,
    country: str,
    is_to_school: bool,
    planned_total_s: float,
    latest_arrival_minutes: int,
    departure_minutes: int,
) -> datetime | None:
    if country != "SOUTH KOREA":
        return None
    if is_to_school:
        planned_departure_minutes = latest_arrival_minutes - (planned_total_s / 60.0)
        return _next_service_datetime(planned_departure_minutes, country)
    return _next_service_datetime(departure_minutes, country)


def _final_route_stats(
    planner: Any,
    provider: str,
    request_points: list[tuple[float, float]],
    cache: dict[str, Any],
    state: dict[str, int],
    *,
    departure_time: datetime | None,
) -> dict[str, Any] | None:
    if provider == "amap":
        return _amap_route_stats(planner, request_points, cache, state)
    if provider == "kakao_navi":
        if departure_time is None:
            return None
        return _kakao_route_stats(request_points, cache, state, departure_time=departure_time)
    return None


def resolve_final_route_traffic_policy(
    planner: Any,
    _config: PlannerConfig,
    input_records: list[dict[str, Any]],
) -> TrafficPolicy:
    country, city = infer_traffic_location(input_records)
    country_label = str(country or "").strip().upper()
    if country_label == "CHINA":
        provider = "amap"
    elif country_label == "SOUTH KOREA":
        provider = "kakao_navi"
    else:
        provider = "none"
    applicable = provider != "none"
    unavailable_reason = None
    if provider == "amap" and not str(getattr(planner, "AMAP_KEY", "") or "").strip():
        unavailable_reason = "missing_amap_key"
    elif provider == "kakao_navi" and not KAKAO_NAVI_API_KEY:
        unavailable_reason = "missing_kakao_navi_key"
    return TrafficPolicy(
        provider=provider,
        country=country,
        city=city,
        final_validation_enabled=bool(FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED),
        final_validation_applicable=applicable,
        unavailable_reason=unavailable_reason,
    )


def attach_final_route_traffic_gate(
    planner: Any,
    scenario: dict[str, Any],
    points: list[dict[str, Any]],
    config: PlannerConfig,
    input_records: list[dict[str, Any]],
    scenario_label: str,
) -> dict[str, Any]:
    service_direction = normalize_service_direction(config.service_direction)
    is_to_school = service_direction == "To School"
    earliest_departure_minutes, latest_arrival_minutes = _to_school_time_window(config)
    from_school_departure_minutes, from_school_latest_minutes = _from_school_time_window(config)
    gate_type = "arrival_window" if is_to_school else "route_duration"
    default_from_school_window = (
        str(config.time_window_start or "").strip() == "06:30"
        and str(config.time_window_end or "").strip() == "08:00"
    )
    traffic_policy = resolve_final_route_traffic_policy(planner, config, input_records)
    country, city = traffic_policy.country, traffic_policy.city
    routes = list(scenario.get("routes") or [])
    solver_route_duration_limit_s = float(getattr(planner, "MAX_ROUTE_DURATION_SECONDS", 0.0) or 0.0)
    route_duration_limit_s = float(
        getattr(planner, "_BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS", 0.0)
        or solver_route_duration_limit_s
    )
    final_route_duration_limit_s = route_duration_limit_s
    if not is_to_school:
        pm_window_s = PM_MAX_ROUTE_WINDOW_MINUTES * 60.0
        final_route_duration_limit_s = min(route_duration_limit_s, pm_window_s) if route_duration_limit_s > 0 else pm_window_s
        if default_from_school_window:
            from_school_departure_minutes = _parse_minutes_clock(config.from_school_departure_time, 15 * 60 + 40)
            from_school_latest_minutes = from_school_departure_minutes + (final_route_duration_limit_s / 60.0)
    gate: dict[str, Any] = {
        "enabled": bool(FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED),
        "scenario": scenario_label,
        "service_direction": service_direction,
        "gate_type": gate_type,
        "country": country,
        "city": city,
        "provider": traffic_policy.provider,
        "traffic_policy": traffic_policy.as_dict(),
        "target_arrival_label": _format_minutes_clock(latest_arrival_minutes if is_to_school else from_school_latest_minutes),
        "target_departure_label": _format_minutes_clock(earliest_departure_minutes if is_to_school else from_school_departure_minutes),
        "target_duration_minutes": (
            final_route_duration_limit_s / 60.0 if final_route_duration_limit_s > 0 else None
        ),
        "pm_max_route_window_minutes": PM_MAX_ROUTE_WINDOW_MINUTES if not is_to_school else None,
        "solver_target_duration_minutes": (
            solver_route_duration_limit_s / 60.0 if solver_route_duration_limit_s > 0 else None
        ),
        "checked_route_count": 0,
        "failed_route_count": 0,
        "failed_route_ids": [],
        "unavailable_route_count": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "max_estimated_arrival_delay_minutes": 0.0,
        "max_time_window_overrun_minutes": 0.0,
        "max_route_duration_overrun_minutes": 0.0,
    }
    if traffic_policy.status() == "disabled":
        gate["status"] = "disabled"
        scenario["traffic_gate"] = gate
        return gate
    if traffic_policy.status() == "not_applicable":
        gate["status"] = "not_applicable"
        gate["reason"] = "unsupported_final_route_traffic_provider"
        scenario["traffic_gate"] = gate
        return gate
    if traffic_policy.status() == "unavailable":
        gate["status"] = "unavailable"
        gate["reason"] = traffic_policy.unavailable_reason or "traffic_policy_unavailable"
        scenario["traffic_gate"] = gate
        return gate

    persistent_cache = (
        traffic_policy.provider != "amap"
        and not bool(getattr(config, "_fresh_final_route_traffic_required", False))
    )
    if persistent_cache:
        cache = load_json_object(FINAL_ROUTE_TRAFFIC_CACHE_PATH)
    else:
        # Fresh scheduled validation and AMap current traffic are reusable only
        # inside this planner run.
        cache_attribute = (
            "_amap_final_route_cache"
            if traffic_policy.provider == "amap"
            else "_scheduled_final_route_traffic_cache"
        )
        cache = getattr(config, cache_attribute, None)
        if cache is None:
            cache = {}
            setattr(config, cache_attribute, cache)
    state = {"api_calls": 0, "cache_hits": 0, "cache_changed": 0}
    target_minutes = latest_arrival_minutes
    departure_minutes = from_school_departure_minutes
    grace_s = (AM_ARRIVAL_GATE_GRACE_MINUTES if is_to_school else PM_ROUTE_GATE_GRACE_MINUTES) * 60.0
    reverse_check_routes: list[dict[str, Any]] = []
    for route_index, route in enumerate(routes, start=1):
        planned_total_s = float(route.get("time_s", 0.0) or 0.0)
        stop_service_s = float(route.get("stop_service_time_s", 0.0) or 0.0)
        target_duration_s = (
            max(0, from_school_latest_minutes - from_school_departure_minutes) * 60.0
            if not is_to_school
            else final_route_duration_limit_s if final_route_duration_limit_s > 0 else planned_total_s
        )
        route_id = str(route.get("route_id") or route.get("id") or f"Bus {route_index}")
        verification: dict[str, Any] = {
            "scenario": scenario_label,
            "route_id": route_id,
            "target_arrival_minutes": target_minutes,
            "target_arrival_label": _format_minutes_clock(target_minutes),
            "earliest_departure_minutes": earliest_departure_minutes,
            "earliest_departure_label": _format_minutes_clock(earliest_departure_minutes),
            "latest_arrival_minutes": latest_arrival_minutes,
            "latest_arrival_label": _format_minutes_clock(latest_arrival_minutes),
            "target_departure_minutes": departure_minutes,
            "target_departure_label": _format_minutes_clock(departure_minutes),
            "gate_type": gate_type,
            "target_duration_s": target_duration_s,
            "target_duration_minutes": target_duration_s / 60.0 if target_duration_s > 0 else None,
            "planned_total_duration_s": planned_total_s,
            "planned_stop_service_s": stop_service_s,
            "grace_minutes": AM_ARRIVAL_GATE_GRACE_MINUTES if is_to_school else PM_ROUTE_GATE_GRACE_MINUTES,
        }
        provider_departure_time = _route_provider_departure_datetime(
            country=country,
            is_to_school=is_to_school,
            planned_total_s=planned_total_s,
            latest_arrival_minutes=latest_arrival_minutes,
            departure_minutes=departure_minutes,
        )
        try:
            stats = _final_route_stats(
                planner,
                traffic_policy.provider,
                _route_amap_points(points, route),
                cache,
                state,
                departure_time=provider_departure_time,
            )
        except Exception as exc:
            stats = None
            verification["error"] = str(exc)
        if not stats:
            gate["unavailable_route_count"] += 1
            verification.update({"status": "unavailable", "passes": None})
            route["final_route_traffic_gate"] = verification
            if is_to_school:
                reverse_check = {
                    "available": False,
                    "status": "unavailable",
                    "route_id": route_id,
                    "scenario": scenario_label,
                    "target_arrival_minutes": latest_arrival_minutes,
                    "target_arrival_label": _format_minutes_clock(latest_arrival_minutes),
                    "earliest_departure_minutes": earliest_departure_minutes,
                    "earliest_departure_label": _format_minutes_clock(earliest_departure_minutes),
                    "service_stop_count": max(0, len(list(route.get("nodes") or [])) - 1),
                    "rider_count": route.get("load") or route.get("passenger_count") or route.get("passengers"),
                    "bus_type": route.get("bus_type_name") or route.get("bus_type"),
                }
                route["arrival_reverse_check"] = reverse_check
                reverse_check_routes.append(reverse_check)
            continue
        verified_drive_s = float(stats.get("duration_s", 0.0) or 0.0)
        verified_total_s = verified_drive_s + stop_service_s
        if is_to_school:
            latest_departure_minutes = latest_arrival_minutes - (verified_total_s / 60.0)
            time_window_overrun_s = max(0.0, (earliest_departure_minutes - latest_departure_minutes) * 60.0)
            scheduled_departure_minutes = max(earliest_departure_minutes, latest_departure_minutes)
            scheduled_arrival_minutes = scheduled_departure_minutes + (verified_total_s / 60.0)
        else:
            scheduled_departure_minutes = from_school_departure_minutes
            scheduled_arrival_minutes = scheduled_departure_minutes + (verified_total_s / 60.0)
            time_window_overrun_s = max(0.0, (scheduled_arrival_minutes - from_school_latest_minutes) * 60.0)
        passes = time_window_overrun_s <= grace_s
        gate["checked_route_count"] += 1
        if not passes:
            gate["failed_route_count"] += 1
            gate["failed_route_ids"].append(verification["route_id"])
        gate["max_estimated_arrival_delay_minutes"] = max(
            float(gate["max_estimated_arrival_delay_minutes"]),
            time_window_overrun_s / 60.0,
        )
        gate["max_time_window_overrun_minutes"] = max(
            float(gate["max_time_window_overrun_minutes"]),
            time_window_overrun_s / 60.0,
        )
        gate["max_route_duration_overrun_minutes"] = max(
            float(gate["max_route_duration_overrun_minutes"]),
            time_window_overrun_s / 60.0 if not is_to_school else 0.0,
        )
        verification.update(
            {
                "status": "passed" if passes else "failed",
                "passes": passes,
                "verified_source": stats.get("source"),
                "provider_departure_time": stats.get("departure_time")
                or (provider_departure_time.isoformat(timespec="seconds") if provider_departure_time else None),
                "provider_segment_count": stats.get("segment_count"),
                "verified_drive_duration_s": verified_drive_s,
                "verified_total_duration_s": verified_total_s,
                "verified_distance_m": float(stats.get("distance_m", 0.0) or 0.0),
                "estimated_arrival_delay_s": time_window_overrun_s,
                "estimated_arrival_delay_minutes": time_window_overrun_s / 60.0,
                "time_window_overrun_s": time_window_overrun_s,
                "time_window_overrun_minutes": time_window_overrun_s / 60.0,
                "verified_departure_minutes": scheduled_departure_minutes,
                "verified_departure_label": _format_minutes_clock(scheduled_departure_minutes),
                "verified_arrival_minutes": scheduled_arrival_minutes,
                "verified_arrival_label": _format_minutes_clock(scheduled_arrival_minutes),
            }
        )
        if is_to_school:
            reverse_check = {
                "available": True,
                "status": "passed" if passes else "failed",
                "route_id": route_id,
                "scenario": scenario_label,
                "target_arrival_minutes": latest_arrival_minutes,
                "target_arrival_label": _format_minutes_clock(latest_arrival_minutes),
                "earliest_departure_minutes": earliest_departure_minutes,
                "earliest_departure_label": _format_minutes_clock(earliest_departure_minutes),
                "required_departure_minutes": latest_departure_minutes,
                "required_departure_label": _format_minutes_clock(latest_departure_minutes),
                "scheduled_departure_minutes": scheduled_departure_minutes,
                "scheduled_departure_label": _format_minutes_clock(scheduled_departure_minutes),
                "scheduled_arrival_minutes": scheduled_arrival_minutes,
                "scheduled_arrival_label": _format_minutes_clock(scheduled_arrival_minutes),
                "before_earliest_departure": latest_departure_minutes < earliest_departure_minutes,
                "departure_window_overrun_s": time_window_overrun_s,
                "departure_window_overrun_minutes": time_window_overrun_s / 60.0,
                "verified_drive_duration_s": verified_drive_s,
                "stop_service_time_s": stop_service_s,
                "verified_total_duration_s": verified_total_s,
                "verified_distance_m": float(stats.get("distance_m", 0.0) or 0.0),
                "service_stop_count": max(0, len(list(route.get("nodes") or [])) - 1),
                "rider_count": route.get("load") or route.get("passenger_count") or route.get("passengers"),
                "bus_type": route.get("bus_type_name") or route.get("bus_type"),
            }
            route["arrival_reverse_check"] = reverse_check
            reverse_check_routes.append(reverse_check)
        route["final_route_traffic_gate"] = verification

    if persistent_cache and state.get("cache_changed"):
        save_json_object(FINAL_ROUTE_TRAFFIC_CACHE_PATH, cache, sort_keys=True)
    gate["api_calls"] = int(state.get("api_calls", 0))
    gate["cache_hits"] = int(state.get("cache_hits", 0))
    if gate["failed_route_count"]:
        gate["status"] = "failed"
    elif gate["unavailable_route_count"]:
        gate["status"] = "unavailable"
    elif gate["checked_route_count"]:
        gate["status"] = "passed"
    else:
        gate["status"] = "unavailable"
    if is_to_school:
        checked_reverse_routes = [row for row in reverse_check_routes if row.get("available")]
        warning_reverse_routes = [row for row in checked_reverse_routes if row.get("before_earliest_departure")]
        unavailable_reverse_routes = [row for row in reverse_check_routes if not row.get("available")]
        required_departures = [
            float(row.get("required_departure_minutes"))
            for row in checked_reverse_routes
            if isinstance(row.get("required_departure_minutes"), (int, float))
        ]
        max_overrun_minutes = max(
            [float(row.get("departure_window_overrun_minutes", 0.0) or 0.0) for row in checked_reverse_routes],
            default=0.0,
        )
        earliest_required_departure = min(required_departures) if required_departures else None
        scenario["arrival_reverse_check"] = {
            "available": bool(reverse_check_routes),
            "service_direction": service_direction,
            "target_arrival_minutes": latest_arrival_minutes,
            "target_arrival_label": _format_minutes_clock(latest_arrival_minutes),
            "earliest_departure_minutes": earliest_departure_minutes,
            "earliest_departure_label": _format_minutes_clock(earliest_departure_minutes),
            "route_count": len(routes),
            "checked_route_count": len(checked_reverse_routes),
            "unavailable_route_count": len(unavailable_reverse_routes),
            "warning_route_count": len(warning_reverse_routes),
            "warning_route_ids": [str(row.get("route_id") or "") for row in warning_reverse_routes if row.get("route_id")],
            "max_departure_window_overrun_minutes": max_overrun_minutes,
            "earliest_required_departure_minutes": earliest_required_departure,
            "earliest_required_departure_label": (
                _format_minutes_clock(earliest_required_departure) if earliest_required_departure is not None else ""
            ),
            "routes": reverse_check_routes,
        }
    scenario["traffic_gate"] = gate
    scenario["traffic_feasible"] = gate["status"] in {"passed", "not_applicable", "disabled"}
    return gate


def _traffic_gate_passed(gate: dict[str, Any] | None) -> bool:
    return str(dict(gate or {}).get("status") or "").strip().lower() == "passed"


def _traffic_gate_overrun_minutes(gate: dict[str, Any] | None) -> float:
    gate = dict(gate or {})
    return float(
        gate.get("max_time_window_overrun_minutes")
        or gate.get("max_estimated_arrival_delay_minutes")
        or gate.get("max_route_duration_overrun_minutes")
        or 0.0
    )


def _route_service_stop_count(route: dict[str, Any]) -> int:
    return len([node for node in list(route.get("nodes") or []) if int(node) != 0])


def _route_comfort_capacity(route: dict[str, Any], config: PlannerConfig) -> int:
    physical_capacity = max(0, int(route.get("bus_capacity", 0) or 0))
    if physical_capacity <= 0:
        return 0
    configured = int(route.get("comfort_capacity", 0) or 0)
    if configured > 0:
        return min(physical_capacity, configured)
    factor = min(1.0, max(0.1, float(config.comfort_load_factor)))
    return max(1, min(physical_capacity, int(math.floor(physical_capacity * factor))))


def _route_hard_constraint_failures(
    route: dict[str, Any],
    config: PlannerConfig,
    *,
    route_id: str,
    traffic_failed_route_ids: set[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    load = float(route.get("load", 0.0) or 0.0)
    physical_capacity = max(0, int(route.get("bus_capacity", 0) or 0))
    comfort_capacity = _route_comfort_capacity(route, config)
    stop_limit = _effective_route_stop_limit(config)
    if physical_capacity > 0 and load > physical_capacity:
        failures.append("physical_capacity")
    if comfort_capacity > 0 and load > comfort_capacity:
        failures.append("comfort_capacity")
    if _route_service_stop_count(route) > stop_limit:
        failures.append("stop_limit")
    if route_id in set(traffic_failed_route_ids or set()) or _route_failed_final_gate(
        route,
        set(traffic_failed_route_ids or set()),
        0,
    ):
        failures.append("time_window")
    return failures


def build_route_feasibility_report(
    scenario: dict[str, Any],
    traffic_gate: dict[str, Any] | None,
    config: PlannerConfig,
    *,
    current_min_active_vehicle_count: int = 0,
    max_vehicle_count: int = 0,
    ignored_route_ids: set[str] | None = None,
) -> dict[str, Any]:
    routes = list(scenario.get("routes") or [])
    route_count = int(scenario.get("bus_count", len(routes)) or len(routes))
    service_direction = normalize_service_direction(config.service_direction)
    gate = dict(traffic_gate or {})
    gate_status = str(gate.get("status") or "not_checked").strip().lower()
    gate_type = str(gate.get("gate_type") or ("arrival_window" if service_direction == "To School" else "route_duration"))

    ignored_route_ids = {str(item) for item in (ignored_route_ids or set())}
    physical_overloaded_routes: list[str] = []
    comfort_over_target_routes: list[str] = []
    over_stop_limit_routes: list[str] = []
    for route_index, route in enumerate(routes, start=1):
        route_id = str(route.get("route_id") or route.get("id") or f"Bus {route_index}")
        if route_id in ignored_route_ids:
            continue
        load = float(route.get("load", 0.0) or 0.0)
        physical_capacity = float(route.get("bus_capacity", 0.0) or 0.0)
        comfort_capacity = float(_route_comfort_capacity(dict(route), config))
        if physical_capacity > 0 and load > physical_capacity:
            physical_overloaded_routes.append(route_id)
        if comfort_capacity > 0 and load > comfort_capacity:
            comfort_over_target_routes.append(route_id)
        if _route_service_stop_count(dict(route)) > _effective_route_stop_limit(config):
            over_stop_limit_routes.append(route_id)

    failed_route_ids = [
        str(item)
        for item in list(gate.get("failed_route_ids") or [])
        if str(item) not in ignored_route_ids
    ]
    if not failed_route_ids and gate_status == "failed" and ignored_route_ids:
        gate_status = "passed"
    time_constraint = dict(scenario.get("time_constraint") or {})
    time_impact_required = bool(time_constraint)
    expected_time_stops = int(time_constraint.get("expected_solver_stop_count", 0) or 0)
    bounded_time_stops = int(time_constraint.get("bounded_solver_stop_count", 0) or 0)
    final_time_impact_gate = dict(scenario.get("final_time_impact_gate") or {})
    final_time_impact_required = bool(time_constraint.get("final_validation_required"))
    final_time_impact_status = str(final_time_impact_gate.get("status") or "not_checked")
    time_impact_passed = bool(
        not time_impact_required
        or (
            time_constraint.get("enabled") is True
            and str(time_constraint.get("mode") or "") == "hard"
            and time_constraint.get("strict_satisfied") is True
            and expected_time_stops > 0
            and bounded_time_stops >= expected_time_stops
            and (
                not final_time_impact_required
                or final_time_impact_status == "passed"
            )
        )
    )
    failure_reasons: list[str] = []
    if physical_overloaded_routes:
        failure_reasons.append("physical_capacity")
    if comfort_over_target_routes:
        failure_reasons.append("comfort_capacity")
    if over_stop_limit_routes:
        failure_reasons.append("stop_limit")
    if time_impact_required and not time_impact_passed:
        failure_reasons.append("time_impact")
    if gate_status == "failed":
        failure_reasons.append(gate_type)
    elif gate_status == "unavailable":
        failure_reasons.append("traffic_validation_unavailable")

    current_min_active_vehicle_count = max(0, int(current_min_active_vehicle_count or 0))
    max_vehicle_count = max(0, int(max_vehicle_count or 0))
    vehicle_over_limit = max_vehicle_count > 0 and route_count > max_vehicle_count
    if vehicle_over_limit:
        failure_reasons.append("vehicle_savings_target")
    can_add_vehicle = max_vehicle_count > 0 and route_count < max_vehicle_count
    recommended_min_active_vehicle_count = current_min_active_vehicle_count
    if (
        physical_overloaded_routes
        or comfort_over_target_routes
        or over_stop_limit_routes
        or (time_impact_required and not time_impact_passed)
        or gate_status == "failed"
    ) and can_add_vehicle:
        recommended_min_active_vehicle_count = min(
            max_vehicle_count,
            max(current_min_active_vehicle_count + 1, route_count + 1),
        )

    fleet_status = "ok"
    if vehicle_over_limit:
        fleet_status = "over_vehicle_limit"
    elif (
        physical_overloaded_routes
        or comfort_over_target_routes
        or over_stop_limit_routes
        or (time_impact_required and not time_impact_passed)
        or gate_status == "failed"
    ) and max_vehicle_count > 0 and not can_add_vehicle:
        fleet_status = "at_max_vehicle_count"
        if "fleet_limit" not in failure_reasons:
            failure_reasons.append("fleet_limit")

    if (
        physical_overloaded_routes
        or comfort_over_target_routes
        or over_stop_limit_routes
        or (time_impact_required and not time_impact_passed)
        or gate_status == "failed"
        or vehicle_over_limit
    ):
        status = "failed"
    elif gate_status == "unavailable":
        status = "unavailable"
    else:
        status = "passed"

    return {
        "version": 1,
        "status": status,
        "scenario": gate.get("scenario"),
        "service_direction": service_direction,
        "route_count": route_count,
        "failure_reasons": failure_reasons,
        "hard_constraints": {
            "physical_capacity": {
                "status": "failed" if physical_overloaded_routes else "passed",
                "failed_route_count": len(physical_overloaded_routes),
                "failed_route_ids": physical_overloaded_routes,
            },
            "time_window": {
                "status": gate_status,
                "gate_type": gate_type,
                "failed_route_count": int(gate.get("failed_route_count", 0) or 0),
                "failed_route_ids": failed_route_ids,
                "max_overrun_minutes": _traffic_gate_overrun_minutes(gate),
            },
            "time_impact": {
                "status": (
                    "not_applicable"
                    if not time_impact_required
                    else "passed" if time_impact_passed else "failed"
                ),
                "limit_minutes": float(config.time_impact_limit_minutes),
                "bounded_solver_stop_count": bounded_time_stops,
                "expected_solver_stop_count": expected_time_stops,
                "final_validation_status": final_time_impact_status,
                "final_compared_stop_count": int(final_time_impact_gate.get("compared_stop_count", 0) or 0),
                "final_over_limit_stop_count": int(final_time_impact_gate.get("over_limit_stop_count", 0) or 0),
                "final_over_limit_rider_count": int(final_time_impact_gate.get("over_limit_rider_count", 0) or 0),
                "final_max_adverse_minutes": float(final_time_impact_gate.get("max_adverse_minutes", 0.0) or 0.0),
            },
            "stop_limit": {
                "status": "failed" if over_stop_limit_routes else "passed",
                "limit": _effective_route_stop_limit(config),
                "failed_route_count": len(over_stop_limit_routes),
                "failed_route_ids": over_stop_limit_routes,
            },
            "comfort": {
                "status": "failed" if comfort_over_target_routes else "passed",
                "load_factor_target": float(config.comfort_load_factor),
                "failed_route_count": len(comfort_over_target_routes),
                "failed_route_ids": comfort_over_target_routes,
            },
            "fleet": {
                "status": fleet_status,
                "max_vehicle_count": max_vehicle_count,
                "current_min_active_vehicle_count": current_min_active_vehicle_count,
                "recommended_min_active_vehicle_count": recommended_min_active_vehicle_count,
                "can_add_vehicle": can_add_vehicle,
            },
        },
        "ignored_route_ids": sorted(ignored_route_ids),
        "traffic_policy": dict(gate.get("traffic_policy") or {}),
    }


def _scenario_feasibility_passed(result: dict[str, Any]) -> bool:
    report = dict(result.get("feasibility_report") or {})
    if report:
        return str(report.get("status") or "").strip().lower() == "passed"
    return _traffic_gate_passed(dict(result.get("traffic_gate") or {}))


def _failed_candidate_summary(result: dict[str, Any], max_vehicle_count: int) -> dict[str, Any] | None:
    if not result or _scenario_feasibility_passed(result):
        return None
    gate = dict(result.get("traffic_gate") or {})
    report = dict(result.get("feasibility_report") or {})
    failure_reasons = list(report.get("failure_reasons") or [])
    bus_count = int(result.get("bus_count", 0) or 0)
    failed_route_count = int(gate.get("failed_route_count", 0) or 0)
    return {
        "bus_count": bus_count,
        "max_vehicle_count": max(0, int(max_vehicle_count or 0)),
        "failed_route_count": failed_route_count,
        "failed_route_ids": list(gate.get("failed_route_ids") or []),
        "max_overrun_minutes": _traffic_gate_overrun_minutes(gate),
        "failure_reasons": failure_reasons,
        "extra_vehicle_may_help": (
            max_vehicle_count > 0
            and bus_count >= max_vehicle_count
            and any(reason in {"arrival_window", "route_duration"} for reason in failure_reasons)
            and failed_route_count > 0
        ),
    }


def _better_failed_candidate(
    candidate: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    candidate_key = (
        int(candidate.get("failed_route_count", 0) or 0),
        float(candidate.get("max_overrun_minutes", 0.0) or 0.0),
        int(candidate.get("bus_count", 0) or 0),
    )
    current_key = (
        int(current.get("failed_route_count", 0) or 0),
        float(current.get("max_overrun_minutes", 0.0) or 0.0),
        int(current.get("bus_count", 0) or 0),
    )
    return candidate if candidate_key < current_key else current


def _repair_failed_routes_with_spare_vehicles(
    planner: Any,
    result: dict[str, Any],
    points: list[dict[str, Any]],
    solve_time: list[list[float]],
    config: PlannerConfig,
    input_records: list[dict[str, Any]],
    scenario_label: str,
    max_vehicle_count: int,
    *,
    ignored_failed_route_ids: set[str] | None = None,
    node_time_lower_bounds: dict[int, float] | None = None,
    node_time_upper_bounds: dict[int, float] | None = None,
    available_bus_type_configs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gate = dict(result.get("traffic_gate") or {})
    routes = [deepcopy(route) for route in list(result.get("routes") or [])]
    initial_route_count = len(routes)
    max_vehicle_count = max(0, int(max_vehicle_count or 0))
    ignored_failed_route_ids = {str(item) for item in (ignored_failed_route_ids or set())}
    actionable_failed_route_ids = {
        str(item) for item in list(gate.get("failed_route_ids") or [])
    } - ignored_failed_route_ids
    if (
        gate.get("status") != "failed"
        or not routes
        or len(routes) >= max_vehicle_count
        or not actionable_failed_route_ids
    ):
        return result

    used_counts = Counter(str(route.get("bus_type_name") or "") for route in routes)
    spare_vehicles: list[dict[str, Any]] = []
    available_vehicles = list(planner.build_vehicle_fleet() or [])
    if available_bus_type_configs is not None:
        available_vehicles = []
        for bus_type in available_bus_type_configs:
            for _ in range(max(0, int(bus_type.get("max_count", 0) or 0))):
                vehicle = {
                    "name": str(bus_type.get("name") or ""),
                    "capacity": int(bus_type.get("capacity", 0) or 0),
                }
                vehicle["comfort_capacity"] = int(planner.solver_capacity_for_vehicle(vehicle))
                available_vehicles.append(vehicle)
    for vehicle in available_vehicles:
        name = str(vehicle.get("name") or "")
        if used_counts.get(name, 0) > 0:
            used_counts[name] -= 1
        else:
            spare_vehicles.append(dict(vehicle))
    spare_vehicles.sort(key=lambda item: int(item.get("capacity", 0) or 0))
    spare_vehicles = spare_vehicles[: max(0, max_vehicle_count - len(routes))]
    if not spare_vehicles:
        return result

    is_to_school = normalize_service_direction(config.service_direction) == "To School"
    working_time = transpose_matrix(solve_time) if is_to_school else solve_time
    lower_bounds = dict(
        node_time_lower_bounds
        if node_time_lower_bounds is not None
        else getattr(planner, "NODE_TIME_LOWER_BOUNDS", {}) or {}
    )
    upper_bounds = dict(
        node_time_upper_bounds
        if node_time_upper_bounds is not None
        else getattr(planner, "NODE_TIME_UPPER_BOUNDS", {}) or {}
    )
    min_route_load = max(1, int(getattr(planner, "MIN_ACTIVE_ROUTE_PASSENGERS", 1) or 1))
    original_failed_count = int(gate.get("failed_route_count", 0) or 0)
    evaluated_split_count = 0
    selected_splits: list[dict[str, Any]] = []

    def route_id(route: dict[str, Any], index: int) -> str:
        return str(route.get("route_id") or route.get("id") or f"Bus {index + 1}")

    def service_nodes(route: dict[str, Any]) -> list[int]:
        return [int(node) for node in list(route.get("nodes") or []) if int(node) != 0]

    def node_load(nodes: list[int]) -> int:
        return sum(int(dict(points[node] or {}).get("passenger_count", 0) or 0) for node in nodes)

    def within_time_ranges(candidate_routes: list[dict[str, Any]]) -> bool:
        if not lower_bounds and not upper_bounds:
            return True
        for candidate_route in candidate_routes:
            nodes = list(candidate_route.get("nodes") or [])
            solver_nodes = list(reversed(nodes)) if is_to_school else nodes
            elapsed_s = 0.0
            for from_node, to_node in zip(solver_nodes[:-1], solver_nodes[1:]):
                if to_node == 0:
                    continue
                elapsed_s += float(working_time[from_node][to_node] or 0.0)
                if elapsed_s < float(lower_bounds.get(to_node, 0) or 0):
                    return False
                if to_node in upper_bounds and elapsed_s > float(upper_bounds[to_node]):
                    return False
        return True

    def split_route(
        source: dict[str, Any],
        cut: int,
        spare: dict[str, Any],
        spare_on_first: bool,
    ) -> list[dict[str, Any]]:
        nodes = service_nodes(source)
        node_groups = [nodes[:cut], nodes[cut:]]
        original_vehicle = {
            "name": str(source.get("bus_type_name") or ""),
            "capacity": int(source.get("bus_capacity", 0) or 0),
            "comfort_capacity": int(source.get("comfort_capacity", 0) or 0),
        }
        vehicles = [spare, original_vehicle] if spare_on_first else [original_vehicle, spare]
        split_routes: list[dict[str, Any]] = []
        base_id = route_id(source, 0)
        for index, (group, vehicle) in enumerate(zip(node_groups, vehicles), start=1):
            cloned = deepcopy(source)
            cloned.pop("final_route_traffic_gate", None)
            cloned.pop("arrival_reverse_check", None)
            cloned["route_id"] = f"{base_id}-{chr(64 + index)}"
            cloned["nodes"] = (group + [0]) if is_to_school else ([0] + group)
            cloned["bus_type_name"] = str(vehicle.get("name") or "")
            cloned["bus_capacity"] = int(vehicle.get("capacity", 0) or 0)
            cloned["comfort_capacity"] = int(
                vehicle.get("comfort_capacity")
                or planner.solver_capacity_for_vehicle(vehicle)
            )
            cloned["load"] = node_load(group)
            split_routes.append(cloned)
        return split_routes

    while spare_vehicles and len(routes) < max_vehicle_count:
        failed_ids = {
            str(item)
            for item in list(dict(result.get("traffic_gate") or {}).get("failed_route_ids") or [])
        } - ignored_failed_route_ids
        failed_candidates = [
            (index, route)
            for index, route in enumerate(routes)
            if route_id(route, index) in failed_ids
            and str(route.get("exception_role") or "") != "frozen_current"
            and len(service_nodes(route)) >= 2
        ]
        if not failed_candidates:
            break
        failed_candidates.sort(
            key=lambda item: float(
                dict(item[1].get("final_route_traffic_gate") or {}).get("time_window_overrun_minutes", 0.0)
                or 0.0
            ),
            reverse=True,
        )
        failed_index, failed_route = failed_candidates[0]
        failed_route = deepcopy(failed_route)
        failed_route["route_id"] = route_id(failed_route, failed_index)
        best: tuple[tuple[float, ...], int, list[dict[str, Any]], dict[str, Any]] | None = None
        nodes = service_nodes(failed_route)
        for spare_index, spare in enumerate(spare_vehicles):
            for cut in range(1, len(nodes)):
                for spare_on_first in (False, True):
                    split_routes = split_route(failed_route, cut, spare, spare_on_first)
                    if any(
                        int(route.get("load", 0) or 0) < min_route_load
                        or int(route.get("load", 0) or 0)
                        > int(planner.solver_capacity_for_vehicle({
                            "name": route.get("bus_type_name"),
                            "capacity": route.get("bus_capacity"),
                            "comfort_capacity": route.get("comfort_capacity"),
                        }))
                        for route in split_routes
                    ):
                        continue
                    if not within_time_ranges(split_routes):
                        continue
                    try:
                        planner.enrich_routes_with_actual_driving(points, split_routes)
                        planner.annotate_and_price_routes(points, split_routes)
                        split_result = planner.build_scenario_result(points, split_routes, "")
                        split_gate = attach_final_route_traffic_gate(
                            planner,
                            split_result,
                            points,
                            config,
                            input_records,
                            f"{scenario_label} split repair",
                        )
                    except Exception:
                        continue
                    evaluated_split_count += 1
                    if split_gate.get("status") != "passed":
                        continue
                    loads = [int(route.get("load", 0) or 0) for route in split_routes]
                    stop_counts = [len(service_nodes(route)) for route in split_routes]
                    verified_totals = [
                        float(
                            dict(route.get("final_route_traffic_gate") or {}).get(
                                "verified_total_duration_s",
                                0.0,
                            )
                            or 0.0
                        )
                        for route in split_routes
                    ]
                    rank = (
                        float(abs(loads[0] - loads[1])),
                        float(abs(stop_counts[0] - stop_counts[1])),
                        float(max(verified_totals, default=0.0)),
                        float(int(spare.get("capacity", 0) or 0)),
                    )
                    if best is None or rank < best[0]:
                        best = (rank, spare_index, split_routes, split_gate)
        if best is None:
            break

        _rank, spare_index, split_routes, split_gate = best
        consumed_spare = spare_vehicles.pop(spare_index)
        routes[failed_index : failed_index + 1] = split_routes
        for index, route in enumerate(routes, start=1):
            route["vehicle_id"] = index
        planner.annotate_and_price_routes(points, routes)
        rebuilt = planner.build_scenario_result(points, routes, "")
        repaired_result = deepcopy(result)
        repaired_result.update(rebuilt)
        repaired_result["output_html"] = ""
        attach_final_route_traffic_gate(
            planner,
            repaired_result,
            points,
            config,
            input_records,
            scenario_label,
        )
        selected_splits.append(
            {
                "source_route_id": route_id(failed_route, failed_index),
                "result_route_ids": [route_id(route, index) for index, route in enumerate(split_routes)],
                "loads": [int(route.get("load", 0) or 0) for route in split_routes],
                "service_stop_counts": [len(service_nodes(route)) for route in split_routes],
                "added_bus_type": str(consumed_spare.get("name") or ""),
                "provider_status": split_gate.get("status"),
            }
        )
        result = repaired_result
        remaining_failed_ids = {
            str(item)
            for item in list(dict(result.get("traffic_gate") or {}).get("failed_route_ids") or [])
        } - ignored_failed_route_ids
        if not remaining_failed_ids:
            break

    final_gate = dict(result.get("traffic_gate") or {})
    final_actionable_failed_ids = {
        str(item) for item in list(final_gate.get("failed_route_ids") or [])
    } - ignored_failed_route_ids
    result["traffic_split_repair"] = {
        "enabled": True,
        "accepted": not final_actionable_failed_ids,
        "initial_failed_route_count": original_failed_count,
        "final_failed_route_count": int(final_gate.get("failed_route_count", 0) or 0),
        "ignored_failed_route_ids": sorted(ignored_failed_route_ids),
        "final_actionable_failed_route_count": len(final_actionable_failed_ids),
        "initial_route_count": initial_route_count,
        "final_route_count": len(routes),
        "evaluated_split_count": evaluated_split_count,
        "selected_splits": selected_splits,
    }
    if selected_splits:
        planner.log(
            f"[BACKEND] {scenario_label} split repair added {len(selected_splits)} vehicle(s); "
            f"provider failures {original_failed_count} -> {int(final_gate.get('failed_route_count', 0) or 0)}."
        )
    return result


def _next_active_vehicle_count_from_feasibility(
    report: dict[str, Any] | None,
    current_min_vehicle_count: int,
) -> int:
    fleet = dict(dict(report or {}).get("hard_constraints", {}).get("fleet", {}) or {})
    return max(
        int(current_min_vehicle_count or 0),
        int(fleet.get("recommended_min_active_vehicle_count", current_min_vehicle_count) or 0),
    )


def _route_duration_replan_bounds(gate: dict[str, Any] | None) -> tuple[float | None, float | None]:
    gate = dict(gate or {})
    gate_type = str(gate.get("gate_type") or "").strip()
    if gate_type not in {"arrival_window", "route_duration"}:
        return None, None
    if gate_type == "arrival_window":
        return None, FINAL_ROUTE_TRAFFIC_REPLAN_MAX_STEP_MINUTES
    target_minutes = float(gate.get("target_duration_minutes", 0.0) or 0.0)
    if target_minutes <= 0:
        return None, FINAL_ROUTE_TRAFFIC_REPLAN_MAX_STEP_MINUTES
    return (
        target_minutes * FINAL_ROUTE_TRAFFIC_REPLAN_TARGET_FLOOR_RATIO * 60.0,
        FINAL_ROUTE_TRAFFIC_REPLAN_MAX_STEP_MINUTES,
    )


def _cap_bus_type_configs_for_vehicle_count(
    planner: Any,
    bus_type_configs: list[dict[str, Any]],
    target_vehicle_count: int,
) -> list[dict[str, Any]]:
    target_vehicle_count = max(0, int(target_vehicle_count or 0))
    if target_vehicle_count <= 0:
        return []
    expanded: list[dict[str, Any]] = []
    for item in bus_type_configs:
        count = max(0, int(item.get("max_count", 0) or 0))
        for _ in range(count):
            expanded.append({"name": item.get("name"), "capacity": item.get("capacity")})
    if not expanded:
        return deepcopy(bus_type_configs)
    sorter = getattr(planner, "sort_regular_preference", None)
    ordered = list(sorter(expanded)) if callable(sorter) else expanded
    selected = ordered[:target_vehicle_count]
    counts: dict[tuple[str, int], int] = {}
    for item in selected:
        key = (str(item.get("name")), int(item.get("capacity", 0) or 0))
        counts[key] = counts.get(key, 0) + 1
    capped: list[dict[str, Any]] = []
    for item in bus_type_configs:
        cloned = deepcopy(item)
        key = (str(cloned.get("name")), int(cloned.get("capacity", 0) or 0))
        cloned["max_count"] = int(counts.get(key, 0))
        capped.append(cloned)
    return capped


def _max_vehicle_count_from_bus_type_configs(bus_type_configs: list[dict[str, Any]]) -> int:
    return sum(max(0, int(item.get("max_count", 0) or 0)) for item in bus_type_configs)


def _minimum_vehicle_count_for_hard_constraints(
    points: list[dict[str, Any]],
    bus_type_configs: list[dict[str, Any]],
    config: PlannerConfig,
) -> int:
    service_stop_count = max(0, len(points) - 1)
    if service_stop_count == 0:
        return 0
    stop_floor = math.ceil(service_stop_count / _effective_route_stop_limit(config))
    comfort_factor = min(1.0, max(0.1, float(config.comfort_load_factor)))
    capacities = sorted(
        (
            max(1, int(math.floor(int(item.get("capacity", 0) or 0) * comfort_factor)))
            for item in bus_type_configs
            for _ in range(max(0, int(item.get("max_count", 0) or 0)))
            if int(item.get("capacity", 0) or 0) > 0
        ),
        reverse=True,
    )
    if not capacities:
        return 1
    total_demand = sum(max(0, int(point.get("passenger_count", 0) or 0)) for point in points[1:])
    covered = 0
    demand_floor = 0
    for demand_floor, capacity in enumerate(capacities, start=1):
        covered += capacity
        if covered >= total_demand:
            break
    if covered < total_demand:
        demand_floor = len(capacities) + 1
    return max(1, stop_floor, demand_floor)


def _minimum_solver_vehicle_count(planner: Any, points: list[dict[str, Any]]) -> int:
    fleet = list(getattr(planner, "build_vehicle_fleet")())
    if not fleet:
        return 0
    demand = sum(int(point.get("passenger_count", 0) or 0) for point in points[1:])
    demand_count = int(getattr(planner, "_minimum_vehicle_count_for_demand")(demand, fleet))
    stop_count = math.ceil(max(0, len(points) - 1) / max(1, int(getattr(planner, "route_stop_limit")())))
    return max(1, demand_count, stop_count)


def _next_final_route_replan_limit_seconds(
    current_limit_seconds: float,
    gate: dict[str, Any],
    *,
    minimum_limit_seconds: float | None = None,
    max_step_minutes: float | None = None,
) -> float | None:
    if not FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED:
        return None
    if dict(gate or {}).get("status") != "failed":
        return None
    if int(dict(gate or {}).get("failed_route_count", 0) or 0) <= 0:
        return None
    delay_minutes = float(dict(gate or {}).get("max_estimated_arrival_delay_minutes", 0.0) or 0.0)
    reduction_minutes = max(FINAL_ROUTE_TRAFFIC_REPLAN_STEP_MINUTES, math.ceil(delay_minutes))
    if max_step_minutes is not None:
        reduction_minutes = min(reduction_minutes, max(FINAL_ROUTE_TRAFFIC_REPLAN_STEP_MINUTES, float(max_step_minutes)))
    min_limit_seconds = FINAL_ROUTE_TRAFFIC_REPLAN_MIN_TARGET_MINUTES * 60.0
    if minimum_limit_seconds is not None:
        min_limit_seconds = max(min_limit_seconds, float(minimum_limit_seconds))
    next_limit_seconds = max(min_limit_seconds, float(current_limit_seconds) - reduction_minutes * 60.0)
    if next_limit_seconds >= float(current_limit_seconds) - 1:
        return None
    return next_limit_seconds


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
        "exception_preserving": str(output_dir / config.exception_preserving_output_name),
    }


def create_request_scoped_config(config: PlannerConfig, job_id: str | None = None) -> PlannerConfig:
    scoped = deepcopy(config)
    scoped.output_directory_name = job_id or uuid.uuid4().hex
    return scoped


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_planner_result_cache() -> dict[str, Any]:
    ensure_cache_dir()
    return load_json_object(PLANNER_RESULT_CACHE_PATH)


def save_planner_result_cache(cache_data: dict[str, Any]) -> None:
    ensure_cache_dir()
    save_json_object(PLANNER_RESULT_CACHE_PATH, cache_data)


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
        clear_json_object(cache_path)


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
    source_label: str = "Free Optimization Baseline",
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
        am_gate = dict(route.get("am_arrival_gate") or route.get("final_route_traffic_gate") or {})
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
                    "am window status": str(am_gate.get("status") or "").strip(),
                    "am departure": str(am_gate.get("verified_departure_label") or "").strip(),
                    "am arrival": str(am_gate.get("verified_arrival_label") or "").strip(),
                    "am overrun minutes": am_gate.get("time_window_overrun_minutes"),
                    "note": f"{source_label} export",
                }
            )

    if not assignment_rows:
        raise ValueError("Free optimization baseline has no route nodes to export.")

    fleet_rows = [
        {
            "bus_type": bus_type,
            "seat_count": seat_count,
            "vehicle_count": vehicle_count,
            "note": f"Generated from {source_label} result",
        }
        for (bus_type, seat_count), vehicle_count in sorted(fleet_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    notes_df = pd.DataFrame(
        {
            "section": ["Source", "Service Direction", "How to use"],
            "guidance": [
                f"Generated from the {source_label} result.",
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
    traffic_profile_name = normalize_traffic_profile_name(config.traffic_profile_name)

    planner.INPUT_STOPS = deepcopy(input_records)
    planner._BRP_ACTIVE_CONFIG = config
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
    planner.MAX_STOPS_PER_ROUTE = _effective_route_stop_limit(config)
    planner.STOP_SERVICE_SECONDS = config.stop_service_minutes * 60
    planner.SUBWAY_SEARCH_RADIUS_M = config.subway_search_radius_m
    planner.MAX_SUBWAY_WALK_DISTANCE_M = config.max_subway_walk_distance_m
    planner.NEARBY_CLUSTER_RADIUS_M = config.nearby_cluster_radius_m
    planner.COMFORT_LOAD_FACTOR = min(1.0, max(0.1, float(config.comfort_load_factor)))
    planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
    planner.TRAFFIC_PROFILE_CONTEXT = "Direct final-route provider validation"
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
            "express_threshold_km": float(config.express_threshold_km),
            "reserved_express_buses": int(config.reserved_express_buses),
            "express_skip_inner_km": float(config.express_skip_inner_km),
            "max_route_duration_minutes": int(config.max_route_duration_minutes),
            "stop_service_minutes": int(config.stop_service_minutes),
            "subway_search_radius_m": int(config.subway_search_radius_m),
            "max_subway_walk_distance_m": int(config.max_subway_walk_distance_m),
            "nearby_cluster_radius_m": int(config.nearby_cluster_radius_m),
            "comfort_load_factor": float(config.comfort_load_factor),
            "traffic_profile_name": normalize_traffic_profile_name(config.traffic_profile_name),
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
    if not original or original.get("enabled") is False:
        original = results.get("time_constrained", {}) or original
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
        "exception_preserving",
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
    traffic_profile_name = normalize_traffic_profile_name(config.traffic_profile_name)
    planner.CURRENT_CURRENCY_CODE = str(hydrated.get("currency_code", "USD"))
    planner.BUS_TYPE_CONFIGS = _build_bus_type_configs(config)
    (
        planner.VEHICLE_FIXED_COST,
        planner.MIN_LOAD_TARGET,
        planner.MIN_LOAD_PENALTY,
    ) = _build_slot_policy_maps(config)
    planner.OPERATING_COST_PER_KM = config.operating_cost_per_km
    planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
    planner.TRAFFIC_PROFILE_CONTEXT = "Direct final-route provider validation"
    planner.SERVICE_DIRECTION = normalize_service_direction(config.service_direction)
    planner.MAX_ROUTE_DURATION_SECONDS = effective_route_duration_limit_minutes(config) * 60
    planner.ANNOTATION_ROUTE_DURATION_SECONDS = config.max_route_duration_minutes * 60
    planner.MAX_STOPS_PER_ROUTE = _effective_route_stop_limit(config)
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
        "exception_preserving",
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


def _current_plan_route_count(current_plan: dict[str, Any] | None) -> int:
    normalized = _normalize_current_plan(current_plan)
    if not normalized:
        return 0
    route_ids = {
        str(item.get("route_id", "")).strip()
        for item in list(normalized.get("assignments") or [])
        if str(item.get("route_id", "")).strip()
    }
    return len(route_ids)


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


def _make_point_lookup(points: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    lookup: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for point in points:
        key = (
            str(point.get("country", "")).strip().lower(),
            str(point.get("city", "")).strip().lower(),
            str(point.get("address", "")).strip().lower(),
        )
        lookup.setdefault(key, []).append(point)
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
    matched_service_node_ids: set[int] = set()

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
            candidates = list(point_lookup.get(key) or [])
            unused_candidates = [
                point
                for point in candidates
                if bool(point.get("is_depot")) == bool(stop.get("is_depot"))
                and (
                    bool(stop.get("is_depot"))
                    or int(point.get("node_id", 0) or 0) not in matched_service_node_ids
                )
            ]
            matched_point = next(
                (
                    point
                    for point in unused_candidates
                    if int(point.get("passenger_count", 0) or 0)
                    == int(stop.get("passenger_count", 0) or 0)
                ),
                unused_candidates[0] if unused_candidates else None,
            )
            if matched_point is None:
                matched_point = next(
                    (
                        point
                        for point in candidates
                        if bool(point.get("is_depot")) == bool(stop.get("is_depot"))
                    ),
                    candidates[0] if candidates else None,
                )
            if matched_point is not None:
                matched_points.append(matched_point)
                if not bool(matched_point.get("is_depot")):
                    matched_service_node_ids.add(int(matched_point.get("node_id", 0) or 0))
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


def attach_current_plan_traffic_gate(
    planner: Any,
    current_plan_scenario: dict[str, Any] | None,
    points: list[dict[str, Any]],
    config: PlannerConfig,
    input_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if (
        not current_plan_scenario
        or current_plan_scenario.get("enabled") is False
        or not current_plan_scenario.get("routes")
    ):
        return None
    return attach_final_route_traffic_gate(
        planner,
        current_plan_scenario,
        points,
        config,
        input_records,
        "Current Plan",
    )


def calibrate_scheduled_current_plan_traffic(
    current_plan_scenario: dict[str, Any] | None,
    gate: dict[str, Any] | None,
) -> dict[str, Any]:
    gate = dict(gate or {})
    policy = dict(gate.get("traffic_policy") or {})
    provider = str(gate.get("provider") or policy.get("provider") or "none").strip().lower()
    evidence: dict[str, Any] = {
        "enabled": True,
        "provider": provider,
        "gate_status": str(gate.get("status") or "unavailable").strip().lower(),
        "api_calls": int(gate.get("api_calls", 0) or 0),
        "cache_hits": int(gate.get("cache_hits", 0) or 0),
    }
    if provider not in {"amap", "kakao_navi"}:
        return {**evidence, "status": "not_applicable", "reason": "unsupported_final_route_traffic_provider"}
    if evidence["gate_status"] not in {"passed", "failed"}:
        return {**evidence, "status": "unavailable", "reason": "current_plan_traffic_gate_unavailable"}
    if evidence["api_calls"] <= 0:
        return {**evidence, "status": "unavailable", "reason": "no_fresh_api_calls"}

    routes = list(dict(current_plan_scenario or {}).get("routes") or [])
    max_verified_total_s = 0.0
    measured_route_count = 0
    for route in routes:
        route_gate = dict(route.get("final_route_traffic_gate") or {})
        verified_total = _traffic_float(route_gate.get("verified_total_duration_s"))
        if verified_total is None:
            continue
        if verified_total <= 0:
            continue
        max_verified_total_s = max(max_verified_total_s, verified_total)
        measured_route_count += 1

    if not routes or measured_route_count != len(routes) or max_verified_total_s <= 0:
        return {
            **evidence,
            "status": "unavailable",
            "reason": "incomplete_current_plan_traffic_measurement",
            "route_count": len(routes),
            "measured_route_count": measured_route_count,
        }

    return {
        **evidence,
        "status": "ready",
        "succeeded": True,
        "route_count": len(routes),
        "measured_route_count": measured_route_count,
        "max_verified_total_duration_minutes": max_verified_total_s / 60.0,
        "solver_adjustment": "none",
    }


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
                f"The optimized improvement plan includes {len(removable_now)} package(s) that fully empty a route, creating immediate route-removal candidates."
            )
        elif removal_paths:
            recommendations.append(
                f"The optimized improvement plan includes {len(removal_paths)} package(s) that leave a route with very limited residual demand, creating a strong removal path."
            )
        elif consolidation_paths:
            recommendations.append(
                f"The optimized improvement plan includes {len(consolidation_paths)} package(s) that move a route materially closer to consolidation."
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
    baseline_gate = dict(baseline_result.get("traffic_gate") or {})
    baseline_gate_status = str(baseline_gate.get("status") or "").strip().lower()
    if baseline_gate_status in {"failed", "unavailable"}:
        gate_label = (
            "failed the 06:00-08:00 AM time-window check"
            if baseline_gate_status == "failed"
            else "did not complete AMap AM time-window verification"
        )
        recommendations.append(
            f"The optimized plan {gate_label}; do not treat the vehicle saving as adoption-ready."
        )
    if route_gap > 0:
        recommendations.append(
            f"The current plan uses {route_gap} more routes than the optimized plan."
        )
    if avg_distance_gap_pct > 10:
        recommendations.append(
            f"The current plan average route distance is {avg_distance_gap_pct:.1f}% above the optimized plan."
        )
    if avg_duration_gap_pct > 10:
        recommendations.append(
            f"The current plan average route duration is {avg_duration_gap_pct:.1f}% above the optimized plan."
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
                f"The current plan uses {current_count - baseline_count} more {bus_type} vehicles than the optimized plan."
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
    reduced_vehicle_limit: int | None = None,
    forced_vehicle_count: int | None = None,
    node_time_lower_bounds_builder: Any | None = None,
    node_time_upper_bounds_builder: Any | None = None,
    node_time_soft_upper_bounds_builder: Any | None = None,
    time_constraint_metadata: dict[str, Any] | None = None,
    final_time_impact_validator: Any | None = None,
    traffic_replan_attempt_limit: int | None = None,
    enable_vehicle_search: bool = True,
) -> dict[str, Any]:
    if len(points) <= 1:
        routes: list[dict[str, Any]] = []
        result = planner.build_scenario_result(points, routes, "")
        result["output_html"] = ""
        return result

    planner.log(f"[BACKEND] Building {scenario_label} scenario with {len(points)} total points.")
    previous_bus_type_configs = deepcopy(getattr(planner, "BUS_TYPE_CONFIGS", []))
    previous_node_time_lower_bounds = deepcopy(getattr(planner, "NODE_TIME_LOWER_BOUNDS", {}))
    previous_node_time_upper_bounds = deepcopy(getattr(planner, "NODE_TIME_UPPER_BOUNDS", {}))
    previous_node_time_soft_upper_bounds = deepcopy(getattr(planner, "NODE_TIME_SOFT_UPPER_BOUNDS", {}))
    previous_min_solver_vehicle_count = int(getattr(planner, "MIN_SOLVER_VEHICLE_COUNT", 0) or 0)
    previous_max_route_duration_seconds = float(getattr(planner, "MAX_ROUTE_DURATION_SECONDS", 0.0) or 0.0)
    previous_gate_route_duration_seconds = getattr(planner, "_BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS", None)
    if bus_type_configs is not None:
        planner.BUS_TYPE_CONFIGS = deepcopy(bus_type_configs)
    reduced_vehicle_limit_int = max(0, int(reduced_vehicle_limit or 0)) if reduced_vehicle_limit is not None else None
    forced_vehicle_count_int = max(0, int(forced_vehicle_count or 0)) if forced_vehicle_count is not None else None
    vehicle_cap_int = reduced_vehicle_limit_int
    if forced_vehicle_count_int is not None and forced_vehicle_count_int > 0:
        vehicle_cap_int = (
            min(vehicle_cap_int, forced_vehicle_count_int)
            if vehicle_cap_int is not None and vehicle_cap_int > 0
            else forced_vehicle_count_int
        )
    if vehicle_cap_int is not None and vehicle_cap_int > 0:
        reduced_vehicle_limit_int = vehicle_cap_int
        planner.BUS_TYPE_CONFIGS = _cap_bus_type_configs_for_vehicle_count(
            planner,
            list(getattr(planner, "BUS_TYPE_CONFIGS", []) or []),
            reduced_vehicle_limit_int,
        )
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
        time_bounds_builder = node_time_upper_bounds_builder or node_time_soft_upper_bounds_builder
        if time_bounds_builder is not None:
            node_time_lower_bounds = node_time_lower_bounds_builder(points) if node_time_lower_bounds_builder else {}
            node_time_upper_bounds = time_bounds_builder(points)
            is_soft_constraint = node_time_upper_bounds_builder is None
            lower_bounds_required = node_time_lower_bounds_builder is not None
            expected_service_stop_count = len(
                [point for point in points if not bool(dict(point or {}).get("is_depot"))]
            )
            planner.NODE_TIME_LOWER_BOUNDS = {} if is_soft_constraint else dict(node_time_lower_bounds)
            planner.NODE_TIME_UPPER_BOUNDS = {} if is_soft_constraint else dict(node_time_upper_bounds)
            planner.NODE_TIME_SOFT_UPPER_BOUNDS = dict(node_time_upper_bounds) if is_soft_constraint else {}
            planner.MIN_SOLVER_VEHICLE_COUNT = max(
                0,
                int(scenario_constraint_metadata.get("min_solver_vehicle_count", 0) or 0),
            )
            scenario_constraint_metadata.update(
                {
                    "enabled": bool(node_time_upper_bounds),
                    "mode": "soft" if is_soft_constraint else "hard",
                    "best_effort": bool(is_soft_constraint),
                    "bounded_solver_stop_count_lower": len(node_time_lower_bounds),
                    "bounded_solver_stop_count_upper": len(node_time_upper_bounds),
                    "bounded_solver_stop_count": len(node_time_upper_bounds),
                    "expected_solver_stop_count": expected_service_stop_count,
                    "strict_satisfied": bool(
                        not is_soft_constraint
                        and expected_service_stop_count > 0
                        and (
                            not lower_bounds_required
                            or len(node_time_lower_bounds) == expected_service_stop_count
                        )
                        and len(node_time_upper_bounds) == expected_service_stop_count
                    ),
                    "min_solver_vehicle_count": int(planner.MIN_SOLVER_VEHICLE_COUNT),
                    "reduced_vehicle_limit": reduced_vehicle_limit_int,
                }
            )
            if isinstance(time_constraint_metadata, dict):
                time_constraint_metadata.update(scenario_constraint_metadata)
            planner.log(
                f"[BACKEND] Applied {len(node_time_upper_bounds)} upper and "
                f"{len(node_time_lower_bounds)} lower {'soft' if is_soft_constraint else 'hard'} stop time-impact "
                f"constraint(s) for {scenario_label}; solver vehicle floor "
                f"{planner.MIN_SOLVER_VEHICLE_COUNT}."
            )
            if not node_time_upper_bounds:
                raise RuntimeError("No solver stops matched the current-plan time-impact constraints.")
            if not is_soft_constraint and (
                len(node_time_upper_bounds) != expected_service_stop_count
                or (
                    lower_bounds_required
                    and len(node_time_lower_bounds) != expected_service_stop_count
                )
            ):
                raise RuntimeError(
                    "Hard time-impact constraints did not cover every solver stop: "
                    f"lower {len(node_time_lower_bounds)}, upper {len(node_time_upper_bounds)}, "
                    f"expected {expected_service_stop_count}."
                )
        else:
            planner.NODE_TIME_LOWER_BOUNDS = {}
            planner.NODE_TIME_UPPER_BOUNDS = {}
            planner.NODE_TIME_SOFT_UPPER_BOUNDS = {}
            planner.MIN_SOLVER_VEHICLE_COUNT = 0
        if forced_vehicle_count_int is not None and forced_vehicle_count_int > 0:
            planner.MIN_SOLVER_VEHICLE_COUNT = forced_vehicle_count_int
            scenario_constraint_metadata["forced_vehicle_count"] = forced_vehicle_count_int
            planner.log(
                f"[BACKEND] Forcing {scenario_label} to test exactly "
                f"{forced_vehicle_count_int} active vehicle(s)."
            )
        original_scenario_bus_type_configs = deepcopy(getattr(planner, "BUS_TYPE_CONFIGS", []))
        active_config = getattr(planner, "_BRP_ACTIVE_CONFIG", None) or PlannerConfig()
        active_service_direction = normalize_service_direction(active_config.service_direction)
        is_from_school = active_service_direction == "From School"
        max_vehicle_count = _max_vehicle_count_from_bus_type_configs(original_scenario_bus_type_configs)
        if reduced_vehicle_limit_int is not None and reduced_vehicle_limit_int > 0:
            max_vehicle_count = min(max_vehicle_count, reduced_vehicle_limit_int)
        gate_name = "route-duration" if is_from_school else "AM arrival"

        def solve_with_current_settings(max_replans: int | None = None) -> dict[str, Any]:
            traffic_replan_attempts: list[dict[str, Any]] = []
            best_failed_candidate: dict[str, Any] | None = None
            result: dict[str, Any] = {}
            replan_attempt_index = 0
            while True:
                route_limit_before_s = float(getattr(planner, "MAX_ROUTE_DURATION_SECONDS", 0.0) or 0.0)
                try:
                    final_routes = planner.solve_routes(points, solve_time, solve_distance)
                except Exception as exc:
                    if replan_attempt_index > 0 and result:
                        last_attempt = traffic_replan_attempts[-1] if traffic_replan_attempts else {}
                        if traffic_replan_attempts:
                            traffic_replan_attempts[-1]["error"] = str(exc)
                        planner.log(
                            f"[WARN] {scenario_label} {gate_name} replan failed after "
                            f"action {last_attempt.get('action') or 'unknown'} at "
                            f"{route_limit_before_s / 60.0:.1f} minutes: {exc}"
                        )
                        fallback_gate = dict(result.get("traffic_gate") or {})
                        fallback_min_limit_s, fallback_max_step_minutes = _route_duration_replan_bounds(fallback_gate)
                        fallback_limit_s = _next_final_route_replan_limit_seconds(
                            float(last_attempt.get("from_route_duration_minutes", route_limit_before_s / 60.0) or 0.0)
                            * 60.0,
                            fallback_gate,
                            minimum_limit_seconds=fallback_min_limit_s,
                            max_step_minutes=fallback_max_step_minutes,
                        )
                        if (
                            str(last_attempt.get("action") or "") in {
                                "increase_active_vehicles",
                                "increase_active_vehicles_after_solver_error",
                                "tighten_route_target_and_increase_active_vehicles",
                            }
                            and fallback_limit_s is not None
                            and replan_attempt_index < (
                                max(0, int(max_replans))
                                if max_replans is not None
                                else max(
                                    FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS,
                                    FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS,
                                )
                            )
                        ):
                            fallback_min_vehicle_count = max(
                                0,
                                int(last_attempt.get("from_min_solver_vehicle_count", 0) or 0),
                            )
                            fallback_feasibility = dict(result.get("feasibility_report") or {})
                            last_target_vehicle_count = max(
                                fallback_min_vehicle_count,
                                int(last_attempt.get("to_min_solver_vehicle_count", 0) or 0),
                            )
                            retry_limit = (
                                max(0, int(max_replans))
                                if max_replans is not None
                                else max(
                                    FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS,
                                    FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS,
                                )
                            )
                            if (
                                str(last_attempt.get("action") or "") in {
                                    "increase_active_vehicles",
                                    "increase_active_vehicles_after_solver_error",
                                    "tighten_route_target_and_increase_active_vehicles",
                                }
                                and FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS > 0
                                and last_target_vehicle_count < max_vehicle_count
                                and replan_attempt_index < retry_limit
                            ):
                                next_vehicle_count = last_target_vehicle_count + 1
                                planner.MIN_SOLVER_VEHICLE_COUNT = next_vehicle_count
                                planner.MAX_ROUTE_DURATION_SECONDS = route_limit_before_s
                                traffic_replan_attempts.append(
                                    {
                                        "attempt": replan_attempt_index + 1,
                                        "action": "increase_active_vehicles_after_solver_error",
                                        "feasibility_status": str(fallback_feasibility.get("status") or ""),
                                        "failure_reasons": list(fallback_feasibility.get("failure_reasons") or []),
                                        "gate_type": fallback_gate.get("gate_type"),
                                        "bus_count": int(result.get("bus_count", 0) or 0),
                                        "from_route_duration_minutes": route_limit_before_s / 60.0,
                                        "to_route_duration_minutes": route_limit_before_s / 60.0,
                                        "from_min_solver_vehicle_count": last_target_vehicle_count,
                                        "to_min_solver_vehicle_count": next_vehicle_count,
                                        "failed_route_count": int(fallback_gate.get("failed_route_count", 0) or 0),
                                        "checked_route_count": int(fallback_gate.get("checked_route_count", 0) or 0),
                                        "unavailable_route_count": int(fallback_gate.get("unavailable_route_count", 0) or 0),
                                        "api_calls": int(fallback_gate.get("api_calls", 0) or 0),
                                        "cache_hits": int(fallback_gate.get("cache_hits", 0) or 0),
                                        "failed_route_ids": list(fallback_gate.get("failed_route_ids") or []),
                                        "max_estimated_arrival_delay_minutes": float(
                                            fallback_gate.get("max_estimated_arrival_delay_minutes", 0.0) or 0.0
                                        ),
                                    }
                                )
                                planner.log(
                                    f"[BACKEND] {scenario_label} vehicle-floor solve error; "
                                    f"trying next vehicle floor {next_vehicle_count} before tightening target."
                                )
                                replan_attempt_index += 1
                                continue
                            planner.MIN_SOLVER_VEHICLE_COUNT = fallback_min_vehicle_count
                            planner.MAX_ROUTE_DURATION_SECONDS = fallback_limit_s
                            traffic_replan_attempts.append(
                                {
                                    "attempt": replan_attempt_index + 1,
                                    "action": "tighten_route_target_after_vehicle_error",
                                    "feasibility_status": str(fallback_feasibility.get("status") or ""),
                                    "failure_reasons": list(fallback_feasibility.get("failure_reasons") or []),
                                    "gate_type": fallback_gate.get("gate_type"),
                                    "bus_count": int(result.get("bus_count", 0) or 0),
                                    "from_route_duration_minutes": float(
                                        last_attempt.get("from_route_duration_minutes", route_limit_before_s / 60.0)
                                        or 0.0
                                    ),
                                    "to_route_duration_minutes": fallback_limit_s / 60.0,
                                    "from_min_solver_vehicle_count": fallback_min_vehicle_count,
                                    "to_min_solver_vehicle_count": fallback_min_vehicle_count,
                                    "failed_route_count": int(fallback_gate.get("failed_route_count", 0) or 0),
                                    "checked_route_count": int(fallback_gate.get("checked_route_count", 0) or 0),
                                    "unavailable_route_count": int(fallback_gate.get("unavailable_route_count", 0) or 0),
                                    "api_calls": int(fallback_gate.get("api_calls", 0) or 0),
                                    "cache_hits": int(fallback_gate.get("cache_hits", 0) or 0),
                                    "failed_route_ids": list(fallback_gate.get("failed_route_ids") or []),
                                    "max_estimated_arrival_delay_minutes": float(
                                        fallback_gate.get("max_estimated_arrival_delay_minutes", 0.0) or 0.0
                                    ),
                                }
                            )
                            planner.log(
                                f"[BACKEND] {scenario_label} falling back to route-target search after "
                                f"vehicle-floor solve error; vehicle floor {fallback_min_vehicle_count}, "
                                f"route target {fallback_limit_s / 60.0:.1f} minutes."
                            )
                            replan_attempt_index += 1
                            continue
                        break
                    raise
                planner.enrich_routes_with_actual_driving(points, final_routes)
                planner.annotate_and_price_routes(points, final_routes)
                result = planner.build_scenario_result(points, final_routes, "")
                result["output_html"] = ""
                if scenario_constraint_metadata:
                    result["time_constraint"] = deepcopy(scenario_constraint_metadata)
                gate = attach_final_route_traffic_gate(
                    planner,
                    result,
                    points,
                    getattr(planner, "_BRP_ACTIVE_CONFIG", None) or PlannerConfig(),
                    list(getattr(planner, "INPUT_STOPS", []) or []),
                    scenario_label,
                )
                if (
                    dict(gate or {}).get("status") == "failed"
                    and int(result.get("bus_count", 0) or 0) < max_vehicle_count
                ):
                    result = _repair_failed_routes_with_spare_vehicles(
                        planner,
                        result,
                        points,
                        solve_time,
                        active_config,
                        list(getattr(planner, "INPUT_STOPS", []) or []),
                        scenario_label,
                        max_vehicle_count,
                    )
                    gate = dict(result.get("traffic_gate") or {})
                final_time_impact_gate = (
                    final_time_impact_validator(result, points)
                    if callable(final_time_impact_validator)
                    else {}
                )
                current_min_vehicle_count = int(getattr(planner, "MIN_SOLVER_VEHICLE_COUNT", 0) or 0)
                feasibility_report = build_route_feasibility_report(
                    result,
                    gate,
                    getattr(planner, "_BRP_ACTIVE_CONFIG", None) or PlannerConfig(),
                    current_min_active_vehicle_count=current_min_vehicle_count,
                    max_vehicle_count=max_vehicle_count,
                )
                result["feasibility_report"] = feasibility_report
                best_failed_candidate = _better_failed_candidate(
                    _failed_candidate_summary(result, max_vehicle_count),
                    best_failed_candidate,
                )
                fleet_constraint = dict(
                    dict(feasibility_report.get("hard_constraints") or {}).get("fleet") or {}
                )
                working_time = transpose_matrix(solve_time) if not is_from_school else solve_time
                direct_time_lower_bounds = {
                    node_index: int(float(working_time[0][node_index] or 0.0))
                    for node_index in range(1, len(points))
                }
                time_impact_bounds_tightened = bool(
                    not fleet_constraint.get("can_add_vehicle")
                    and _tighten_final_time_impact_bounds(
                        planner,
                        final_time_impact_gate,
                        minimum_bounds=direct_time_lower_bounds,
                    )
                )
                replan_limit = FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS if max_replans is None else max(0, int(max_replans))
                if max_replans is None:
                    replan_limit = max(
                        replan_limit,
                        FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS,
                    )
                if replan_attempt_index >= replan_limit:
                    break
                min_limit_s, max_step_minutes = _route_duration_replan_bounds(gate)
                next_limit_s = _next_final_route_replan_limit_seconds(
                    route_limit_before_s,
                    gate,
                    minimum_limit_seconds=min_limit_s,
                    max_step_minutes=max_step_minutes,
                )
                next_min_vehicle_count = _next_active_vehicle_count_from_feasibility(
                    feasibility_report,
                    current_min_vehicle_count,
                )
                gate_type = str(dict(gate or {}).get("gate_type") or "")
                prefer_route_target_search = gate_type in {"arrival_window", "route_duration"} and next_limit_s is not None
                search_action = "tighten_route_target" if next_limit_s is not None else "none"
                if prefer_route_target_search:
                    if traffic_replan_attempts and next_min_vehicle_count > current_min_vehicle_count:
                        search_action = "tighten_route_target_and_increase_active_vehicles"
                    else:
                        next_min_vehicle_count = current_min_vehicle_count
                elif time_impact_bounds_tightened:
                    search_action = (
                        "tighten_time_impact_bounds_and_increase_active_vehicles"
                        if next_min_vehicle_count > current_min_vehicle_count
                        else "tighten_time_impact_bounds"
                    )
                elif next_min_vehicle_count > current_min_vehicle_count:
                    next_limit_s = None
                    search_action = "increase_active_vehicles"
                if (
                    next_limit_s is None
                    and next_min_vehicle_count <= current_min_vehicle_count
                    and not time_impact_bounds_tightened
                ):
                    break
                traffic_replan_attempts.append(
                    {
                        "attempt": replan_attempt_index + 1,
                        "action": search_action,
                        "feasibility_status": str(feasibility_report.get("status") or ""),
                        "failure_reasons": list(feasibility_report.get("failure_reasons") or []),
                        "gate_type": dict(gate or {}).get("gate_type"),
                        "bus_count": int(result.get("bus_count", 0) or 0),
                        "from_route_duration_minutes": route_limit_before_s / 60.0,
                        "to_route_duration_minutes": (next_limit_s or route_limit_before_s) / 60.0,
                        "from_min_solver_vehicle_count": current_min_vehicle_count,
                        "to_min_solver_vehicle_count": next_min_vehicle_count,
                        "failed_route_count": int(dict(gate or {}).get("failed_route_count", 0) or 0),
                        "checked_route_count": int(dict(gate or {}).get("checked_route_count", 0) or 0),
                        "unavailable_route_count": int(dict(gate or {}).get("unavailable_route_count", 0) or 0),
                        "api_calls": int(dict(gate or {}).get("api_calls", 0) or 0),
                        "cache_hits": int(dict(gate or {}).get("cache_hits", 0) or 0),
                        "failed_route_ids": list(dict(gate or {}).get("failed_route_ids") or []),
                        "max_estimated_arrival_delay_minutes": float(
                            dict(gate or {}).get("max_estimated_arrival_delay_minutes", 0.0) or 0.0
                        ),
                        "time_impact_over_limit_stop_count": int(
                            dict(final_time_impact_gate or {}).get("over_limit_stop_count", 0) or 0
                        ),
                        "time_impact_max_adverse_minutes": float(
                            dict(final_time_impact_gate or {}).get("max_adverse_minutes", 0.0) or 0.0
                        ),
                    }
                )
                planner.log(
                    f"[BACKEND] {scenario_label} failed final hard-constraint gate; "
                    f"action {search_action}; "
                    f"route target {route_limit_before_s / 60.0:.1f} "
                    f"to {(next_limit_s or route_limit_before_s) / 60.0:.1f} minutes; "
                    f"vehicle floor {current_min_vehicle_count} -> {next_min_vehicle_count}; resolving."
                )
                if next_limit_s is not None:
                    planner.MAX_ROUTE_DURATION_SECONDS = next_limit_s
                if next_min_vehicle_count > current_min_vehicle_count:
                    planner.MIN_SOLVER_VEHICLE_COUNT = next_min_vehicle_count
                replan_attempt_index += 1
            if best_failed_candidate and not _scenario_feasibility_passed(result):
                result["traffic_best_failed_candidate"] = best_failed_candidate
                traffic_gate = dict(result.get("traffic_gate") or {})
                traffic_gate["best_failed_candidate"] = best_failed_candidate
                result["traffic_gate"] = traffic_gate
            if traffic_replan_attempts:
                result["traffic_replan_attempts"] = traffic_replan_attempts
                traffic_gate = dict(result.get("traffic_gate") or {})
                traffic_gate["replan_enabled"] = bool(FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED)
                traffic_gate["replan_attempts"] = traffic_replan_attempts
                result["traffic_gate"] = traffic_gate
            return result

        planner.BUS_TYPE_CONFIGS = deepcopy(original_scenario_bus_type_configs)
        planner.MAX_ROUTE_DURATION_SECONDS = previous_max_route_duration_seconds
        planner._BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS = previous_max_route_duration_seconds
        result = solve_with_current_settings(traffic_replan_attempt_limit)

        vehicle_search_attempts: list[dict[str, Any]] = []
        if enable_vehicle_search and forced_vehicle_count_int is None and _scenario_feasibility_passed(result):
            min_vehicle_count = _minimum_solver_vehicle_count(planner, points)
            selected_bus_count = int(result.get("bus_count", 0) or 0)
            target_bus_count = selected_bus_count - 1
            search_count = 0
            while (
                FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS > 0
                and target_bus_count >= min_vehicle_count
                and search_count < FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS
            ):
                search_count += 1
                planner.BUS_TYPE_CONFIGS = _cap_bus_type_configs_for_vehicle_count(
                    planner,
                    original_scenario_bus_type_configs,
                    target_bus_count,
                )
                planner.MAX_ROUTE_DURATION_SECONDS = previous_max_route_duration_seconds
                planner._BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS = previous_max_route_duration_seconds
                attempt: dict[str, Any] = {
                    "attempt": search_count,
                    "target_bus_count": target_bus_count,
                }
                try:
                    candidate = solve_with_current_settings(FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_REPLAN_ATTEMPTS)
                    gate = dict(candidate.get("traffic_gate") or {})
                    feasibility_report = dict(candidate.get("feasibility_report") or {})
                    candidate_bus_count = int(candidate.get("bus_count", 0) or 0)
                    attempt.update(
                        {
                            "status": str(feasibility_report.get("status") or gate.get("status") or "unknown"),
                            "feasibility_status": str(feasibility_report.get("status") or ""),
                            "failure_reasons": list(feasibility_report.get("failure_reasons") or []),
                            "bus_count": candidate_bus_count,
                            "failed_route_count": int(gate.get("failed_route_count", 0) or 0),
                            "max_estimated_arrival_delay_minutes": float(
                                gate.get("max_estimated_arrival_delay_minutes", 0.0) or 0.0
                            ),
                        }
                    )
                    vehicle_search_attempts.append(attempt)
                    if _scenario_feasibility_passed(candidate):
                        result = candidate
                        selected_bus_count = candidate_bus_count
                        target_bus_count = selected_bus_count - 1
                        continue
                    break
                except Exception as exc:
                    attempt.update({"status": "error", "error": str(exc)})
                    vehicle_search_attempts.append(attempt)
                    break
        if vehicle_search_attempts:
            result["traffic_vehicle_search_attempts"] = vehicle_search_attempts
            traffic_gate = dict(result.get("traffic_gate") or {})
            traffic_gate["vehicle_search_enabled"] = True
            traffic_gate["vehicle_search_attempts"] = vehicle_search_attempts
            result["traffic_gate"] = traffic_gate
        if scenario_constraint_metadata:
            result["time_constraint"] = scenario_constraint_metadata
        return result
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url
        planner.BUS_TYPE_CONFIGS = previous_bus_type_configs
        planner.NODE_TIME_LOWER_BOUNDS = previous_node_time_lower_bounds
        planner.NODE_TIME_UPPER_BOUNDS = previous_node_time_upper_bounds
        planner.NODE_TIME_SOFT_UPPER_BOUNDS = previous_node_time_soft_upper_bounds
        planner.MIN_SOLVER_VEHICLE_COUNT = previous_min_solver_vehicle_count
        planner.MAX_ROUTE_DURATION_SECONDS = previous_max_route_duration_seconds
        if previous_gate_route_duration_seconds is None:
            if hasattr(planner, "_BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS"):
                delattr(planner, "_BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS")
        else:
            planner._BRP_FINAL_ROUTE_TRAFFIC_GATE_DURATION_SECONDS = previous_gate_route_duration_seconds


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


def _scenario_bus_count(result: dict[str, Any] | None) -> int:
    result = dict(result or {})
    return int(result.get("bus_count") or len(list(result.get("routes") or [])) or 0)


def _apply_vehicle_saving_target(
    result: dict[str, Any],
    current_route_count: int,
    minimum_vehicle_reduction: int,
    *,
    frozen_route_count: int = 0,
) -> dict[str, Any]:
    route_count = _scenario_bus_count(result)
    current_route_count = max(0, int(current_route_count or 0))
    minimum_vehicle_reduction = max(0, int(minimum_vehicle_reduction or 0))
    result_enabled = result.get("enabled") is not False
    has_routes = route_count > 0
    saved_route_count = max(0, current_route_count - route_count) if result_enabled and has_routes else 0
    status = (
        "not_applicable"
        if not result_enabled or not has_routes
        else "passed" if saved_route_count >= minimum_vehicle_reduction else "failed"
    )
    payload = {
        "current_route_count": current_route_count,
        "route_count": route_count,
        "saved_route_count": saved_route_count,
        "minimum_vehicle_reduction": minimum_vehicle_reduction,
        "frozen_route_count": max(0, int(frozen_route_count or 0)),
        "status": status,
    }
    result["vehicle_saving_target"] = payload
    traffic_gate = dict(result.get("traffic_gate") or {})
    traffic_gate["vehicle_saving_target"] = payload
    result["traffic_gate"] = traffic_gate

    report = dict(result.get("feasibility_report") or {})
    if report:
        hard_constraints = dict(report.get("hard_constraints") or {})
        hard_constraints["vehicle_saving_target"] = {
            "status": payload["status"],
            "current_route_count": current_route_count,
            "route_count": route_count,
            "saved_route_count": saved_route_count,
            "minimum_vehicle_reduction": minimum_vehicle_reduction,
        }
        report["hard_constraints"] = hard_constraints
        report["vehicle_saving_target"] = payload
        if payload["status"] == "failed":
            failure_reasons = list(report.get("failure_reasons") or [])
            if "vehicle_savings_target" not in failure_reasons:
                failure_reasons.append("vehicle_savings_target")
            report["failure_reasons"] = failure_reasons
            report["status"] = "failed"
        result["feasibility_report"] = report
    return result


def _vehicle_ladder_attempt(result: dict[str, Any], target_vehicle_count: int) -> dict[str, Any]:
    gate = dict(result.get("traffic_gate") or {})
    time_impact_gate = dict(result.get("final_time_impact_gate") or {})
    report = dict(result.get("feasibility_report") or {})
    saving_target = dict(result.get("vehicle_saving_target") or {})
    return {
        "target_vehicle_count": max(0, int(target_vehicle_count or 0)),
        "route_count": _scenario_bus_count(result),
        "status": str(report.get("status") or gate.get("status") or "unknown"),
        "failure_reasons": list(report.get("failure_reasons") or []),
        "failed_route_count": int(gate.get("failed_route_count", 0) or 0),
        "max_overrun_minutes": _traffic_gate_overrun_minutes(gate),
        "time_impact_over_limit_stop_count": int(time_impact_gate.get("over_limit_stop_count", 0) or 0),
        "time_impact_over_limit_rider_count": int(time_impact_gate.get("over_limit_rider_count", 0) or 0),
        "time_impact_max_adverse_minutes": float(time_impact_gate.get("max_adverse_minutes", 0.0) or 0.0),
        "saved_route_count": int(saving_target.get("saved_route_count", 0) or 0),
        "saving_target_status": str(saving_target.get("status") or ""),
    }


def _attach_vehicle_ladder_metadata(
    result: dict[str, Any],
    attempts: list[dict[str, Any]],
    *,
    current_route_count: int,
    minimum_vehicle_reduction: int,
    selected_target_vehicle_count: int | None,
    frozen_route_count: int = 0,
) -> dict[str, Any]:
    metadata = {
        "enabled": True,
        "current_route_count": max(0, int(current_route_count or 0)),
        "minimum_vehicle_reduction": max(0, int(minimum_vehicle_reduction or 0)),
        "frozen_route_count": max(0, int(frozen_route_count or 0)),
        "selected_target_vehicle_count": selected_target_vehicle_count,
        "attempts": attempts,
    }
    result["vehicle_ladder_search"] = metadata
    traffic_gate = dict(result.get("traffic_gate") or {})
    traffic_gate["vehicle_ladder_search"] = metadata
    result["traffic_gate"] = traffic_gate
    return result


def _scenario_candidate_rank(result: dict[str, Any]) -> tuple[int, float, float]:
    routes = list(result.get("routes") or [])
    provider_totals = [
        float(dict(route.get("final_route_traffic_gate") or {}).get("verified_total_duration_s", 0.0) or 0.0)
        for route in routes
    ]
    provider_total = sum(provider_totals) if routes and all(value > 0 for value in provider_totals) else float("inf")
    modeled_total = sum(float(route.get("time_s", 0.0) or 0.0) for route in routes)
    return _scenario_bus_count(result), provider_total, modeled_total


def _failed_scenario_rank(result: dict[str, Any]) -> tuple[int, int, int, float, float, int]:
    gate = dict(result.get("traffic_gate") or {})
    report = dict(result.get("feasibility_report") or {})
    time_impact_gate = dict(result.get("final_time_impact_gate") or {})
    return (
        len(list(report.get("failure_reasons") or [])),
        int(gate.get("failed_route_count", 0) or 0),
        int(time_impact_gate.get("over_limit_stop_count", 0) or 0),
        float(time_impact_gate.get("max_adverse_minutes", 0.0) or 0.0),
        _traffic_gate_overrun_minutes(gate),
        _scenario_bus_count(result),
    )


def _solve_vehicle_ladder_scenario(
    planner: Any,
    points: list[dict[str, Any]],
    scenario_label: str,
    *,
    current_route_count: int,
    minimum_vehicle_reduction: int,
    bus_type_configs: list[dict[str, Any]] | None = None,
    node_time_lower_bounds_builder: Any | None = None,
    node_time_upper_bounds_builder: Any | None = None,
    node_time_soft_upper_bounds_builder: Any | None = None,
    time_constraint_metadata: dict[str, Any] | None = None,
    final_time_impact_validator: Any | None = None,
) -> dict[str, Any]:
    current_route_count = max(0, int(current_route_count or 0))
    minimum_vehicle_reduction = max(0, int(minimum_vehicle_reduction or 0))
    required_target_vehicle_count = (
        max(0, current_route_count - minimum_vehicle_reduction)
        if minimum_vehicle_reduction > 0
        else max(0, current_route_count)
    )
    required_target_vehicle_count = min(required_target_vehicle_count, current_route_count)
    active_bus_type_configs = list(bus_type_configs or getattr(planner, "BUS_TYPE_CONFIGS", []) or [])
    minimum_target_vehicle_count = _minimum_vehicle_count_for_hard_constraints(
        points,
        active_bus_type_configs,
        getattr(planner, "_BRP_ACTIVE_CONFIG", None) or PlannerConfig(),
    )
    if minimum_target_vehicle_count > required_target_vehicle_count:
        reason = (
            f"{scenario_label} requires at least {minimum_target_vehicle_count} vehicle(s) for the "
            f"capacity and stop limits, above the allowed {required_target_vehicle_count} after the "
            "minimum vehicle saving is applied."
        )
        result = _build_skipped_scenario_result(reason)
        result = _apply_vehicle_saving_target(result, current_route_count, minimum_vehicle_reduction)
        result["constraint_search_outcome"] = {
            "status": "provably_infeasible",
            "reason": "minimum_vehicle_lower_bound_exceeds_allowed_maximum",
            "allowed_max_vehicle_count": required_target_vehicle_count,
            "theoretical_min_vehicle_count": minimum_target_vehicle_count,
            "attempted_vehicle_caps": [],
            "feasible_candidate_count": 0,
        }
        return _attach_vehicle_ladder_metadata(
            result,
            [],
            current_route_count=current_route_count,
            minimum_vehicle_reduction=minimum_vehicle_reduction,
            selected_target_vehicle_count=None,
        )
    attempts: list[dict[str, Any]] = []
    best_target_result: dict[str, Any] | None = None
    best_failed_result: dict[str, Any] | None = None
    selected_target_vehicle_count: int | None = None
    for target_vehicle_count in range(required_target_vehicle_count, minimum_target_vehicle_count - 1, -1):
        try:
            candidate = _compute_scenario_without_render(
                planner,
                points,
                scenario_label,
                bus_type_configs=active_bus_type_configs,
                reduced_vehicle_limit=target_vehicle_count,
                node_time_lower_bounds_builder=node_time_lower_bounds_builder,
                node_time_upper_bounds_builder=node_time_upper_bounds_builder,
                node_time_soft_upper_bounds_builder=node_time_soft_upper_bounds_builder,
                time_constraint_metadata=deepcopy(time_constraint_metadata or {}),
                final_time_impact_validator=final_time_impact_validator,
                enable_vehicle_search=False,
            )
        except Exception as exc:
            attempts.append(
                {
                    "target_vehicle_count": target_vehicle_count,
                    "phase": "target_or_better",
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue

        hard_passed = _scenario_feasibility_passed(candidate)
        candidate = _apply_vehicle_saving_target(candidate, current_route_count, minimum_vehicle_reduction)
        all_constraints_passed = _scenario_feasibility_passed(candidate)
        attempt = _vehicle_ladder_attempt(candidate, target_vehicle_count)
        attempt["phase"] = "target_or_better"
        attempt["hard_constraints_passed"] = hard_passed
        attempt["all_constraints_passed"] = all_constraints_passed
        attempt["required_target_vehicle_count"] = required_target_vehicle_count
        attempts.append(attempt)

        if all_constraints_passed:
            if best_target_result is None or _scenario_candidate_rank(candidate) < _scenario_candidate_rank(best_target_result):
                best_target_result = candidate
                selected_target_vehicle_count = target_vehicle_count
            continue
        if best_failed_result is None or _failed_scenario_rank(candidate) < _failed_scenario_rank(best_failed_result):
            best_failed_result = candidate

    result = best_target_result or best_failed_result
    if result is None:
        result = _build_skipped_scenario_result(
            f"{scenario_label} could not produce a candidate between the hard vehicle limits "
            f"{minimum_target_vehicle_count} and {required_target_vehicle_count}."
        )
        result = _apply_vehicle_saving_target(result, current_route_count, minimum_vehicle_reduction)
    search_outcome = {
        "status": "passed" if best_target_result is not None else "exhausted_without_feasible_candidate",
        "allowed_max_vehicle_count": required_target_vehicle_count,
        "theoretical_min_vehicle_count": minimum_target_vehicle_count,
        "attempted_vehicle_caps": [int(item["target_vehicle_count"]) for item in attempts],
        "feasible_candidate_count": sum(1 for item in attempts if item.get("all_constraints_passed")),
    }
    result["constraint_search_outcome"] = search_outcome
    return _attach_vehicle_ladder_metadata(
        result,
        attempts,
        current_route_count=current_route_count,
        minimum_vehicle_reduction=minimum_vehicle_reduction,
        selected_target_vehicle_count=selected_target_vehicle_count,
    )


def _route_display_id(route: dict[str, Any], fallback_index: int) -> str:
    return str(route.get("route_id") or route.get("id") or f"Bus {fallback_index}").strip()


def _route_time_window_overrun_minutes(route: dict[str, Any]) -> float:
    gate = dict(route.get("final_route_traffic_gate") or route.get("am_arrival_gate") or {})
    reverse = dict(route.get("arrival_reverse_check") or {})
    return float(
        gate.get("time_window_overrun_minutes")
        or gate.get("estimated_arrival_delay_minutes")
        or gate.get("route_duration_overrun_minutes")
        or reverse.get("departure_window_overrun_minutes")
        or 0.0
    )


def _route_failed_final_gate(route: dict[str, Any], failed_ids: set[str], fallback_index: int) -> bool:
    route_id = _route_display_id(route, fallback_index)
    gate = dict(route.get("final_route_traffic_gate") or route.get("am_arrival_gate") or {})
    reverse = dict(route.get("arrival_reverse_check") or {})
    status = str(gate.get("status") or reverse.get("status") or "").strip().lower()
    if route_id in failed_ids:
        return True
    if gate.get("passes") is False:
        return True
    return status == "failed" or bool(reverse.get("before_earliest_departure"))


def _scenario_exception_summary(
    scenario: dict[str, Any] | None,
    *,
    include_frozen_current: bool = True,
    config: PlannerConfig | None = None,
) -> dict[str, Any]:
    scenario = dict(scenario or {})
    gate = dict(scenario.get("traffic_gate") or {})
    failed_ids = {str(item) for item in list(gate.get("failed_route_ids") or [])}
    failed_routes: list[dict[str, Any]] = []
    affected_riders = 0
    max_overrun_minutes = (
        float(gate.get("max_time_window_overrun_minutes") or 0.0)
        if include_frozen_current
        else 0.0
    )
    for route_index, route in enumerate(list(scenario.get("routes") or []), start=1):
        route = dict(route or {})
        if not include_frozen_current and str(route.get("exception_role") or "") == "frozen_current":
            continue
        route_id = _route_display_id(route, route_index)
        failures = (
            _route_hard_constraint_failures(
                route,
                config,
                route_id=route_id,
                traffic_failed_route_ids=failed_ids,
            )
            if config is not None
            else (["time_window"] if _route_failed_final_gate(route, failed_ids, route_index) else [])
        )
        if not failures:
            continue
        overrun_minutes = _route_time_window_overrun_minutes(route)
        max_overrun_minutes = max(max_overrun_minutes, overrun_minutes)
        affected_riders += int(route.get("load") or route.get("passenger_count") or route.get("passengers") or 0)
        failed_routes.append(
            {
                "route_id": route_id,
                "rider_count": int(route.get("load") or route.get("passenger_count") or route.get("passengers") or 0),
                "service_stop_count": _route_service_stop_count(route),
                "overrun_minutes": overrun_minutes,
                "failure_reasons": failures,
            }
        )
    return {
        "route_count": int(scenario.get("bus_count") or len(list(scenario.get("routes") or [])) or 0),
        "failed_route_count": len(failed_routes),
        "failed_route_ids": [str(item["route_id"]) for item in failed_routes],
        "affected_rider_count": affected_riders,
        "max_overrun_minutes": max_overrun_minutes,
        "failed_routes": failed_routes,
    }


def _build_exception_subset_points(
    original_points: list[dict[str, Any]],
    frozen_node_ids: set[int],
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    if not original_points:
        return [], {}
    subset_points = [dict(original_points[0])]
    subset_points[0]["node_id"] = 0
    subset_points[0]["_exception_original_node_id"] = 0
    subset_to_original = {0: 0}
    for original_index, point in enumerate(original_points[1:], start=1):
        if original_index in frozen_node_ids:
            continue
        subset_index = len(subset_points)
        copied = dict(point)
        copied["node_id"] = subset_index
        copied["_exception_original_node_id"] = original_index
        subset_points.append(copied)
        subset_to_original[subset_index] = original_index
    return subset_points, subset_to_original


def _remap_exception_route_nodes(
    route: dict[str, Any],
    subset_points: list[dict[str, Any]],
    route_id: str,
) -> dict[str, Any]:
    remapped = deepcopy(route)

    def original_node_id(subset_index: Any) -> int:
        try:
            index = int(subset_index)
        except (TypeError, ValueError):
            return 0
        if 0 <= index < len(subset_points):
            point = dict(subset_points[index] or {})
            return int(point.get("_exception_original_node_id", index) or 0)
        return index

    remapped["route_id"] = route_id
    remapped["exception_role"] = "optimized_remainder"
    remapped["nodes"] = [original_node_id(node_id) for node_id in list(remapped.get("nodes") or [])]
    for leg in list(remapped.get("leg_details") or []):
        if isinstance(leg, dict):
            leg["from_node"] = original_node_id(leg.get("from_node"))
            leg["to_node"] = original_node_id(leg.get("to_node"))
    return remapped


def _bus_type_configs_after_frozen_routes(
    bus_type_configs: list[dict[str, Any]],
    frozen_routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    frozen_counts = Counter(
        str(route.get("bus_type_name") or route.get("bus_type") or "").strip()
        for route in frozen_routes
        if str(route.get("bus_type_name") or route.get("bus_type") or "").strip()
    )
    adjusted: list[dict[str, Any]] = []
    for item in list(bus_type_configs or []):
        copied = deepcopy(item)
        name = str(copied.get("name") or "").strip()
        copied["max_count"] = max(0, int(copied.get("max_count", 0) or 0) - int(frozen_counts.get(name, 0)))
        adjusted.append(copied)
    return adjusted


def _route_service_node_ids(route: dict[str, Any]) -> set[int]:
    node_ids: set[int] = set()
    for node_id in list(route.get("nodes") or []):
        try:
            parsed = int(node_id)
        except (TypeError, ValueError):
            continue
        if parsed != 0:
            node_ids.add(parsed)
    return node_ids


def _exception_candidate_accepted(
    candidate_remainder_summary: dict[str, Any],
    candidate_route_count: int,
    current_route_count: int,
    target_vehicle_count: int | None = None,
) -> bool:
    vehicle_limit = int(target_vehicle_count if target_vehicle_count is not None else current_route_count - 1)
    return (
        candidate_route_count <= vehicle_limit
        and int(candidate_remainder_summary.get("failed_route_count", 0) or 0) == 0
    )


def _exception_candidate_rank(
    candidate_summary: dict[str, Any],
    candidate_route_count: int,
    time_impact_gate: dict[str, Any] | None = None,
) -> tuple[int, int, int, float, int, float, int]:
    time_impact_gate = dict(time_impact_gate or {})
    return (
        int(candidate_summary.get("failed_route_count", 0) or 0)
        + (1 if int(time_impact_gate.get("over_limit_stop_count", 0) or 0) else 0),
        int(candidate_summary.get("failed_route_count", 0) or 0),
        int(time_impact_gate.get("over_limit_stop_count", 0) or 0),
        float(time_impact_gate.get("max_adverse_minutes", 0.0) or 0.0),
        int(candidate_summary.get("affected_rider_count", 0) or 0),
        float(candidate_summary.get("max_overrun_minutes", 0.0) or 0.0),
        int(candidate_route_count or 0),
    )


def _protected_route_cost(nodes: list[int], solve_time: list[list[float]]) -> float:
    route_nodes = [*nodes, 0]
    return sum(
        float(solve_time[left][right])
        for left, right in zip(route_nodes[:-1], route_nodes[1:])
    )


def _protected_route_elapsed_by_node(
    nodes: list[int],
    solve_time: list[list[float]],
) -> dict[int, float]:
    reverse_nodes = [0, *reversed(nodes)]
    elapsed = 0.0
    values: dict[int, float] = {}
    for left, right in zip(reverse_nodes[:-1], reverse_nodes[1:]):
        elapsed += float(solve_time[right][left])
        values[right] = elapsed
    return values


def _protected_route_within_solver_bounds(
    nodes: list[int],
    solve_time: list[list[float]],
    bounds: dict[int, int],
    route_limit_s: float,
) -> bool:
    elapsed_by_node = _protected_route_elapsed_by_node(nodes, solve_time)
    return bool(elapsed_by_node) and all(
        elapsed <= float(bounds.get(node, route_limit_s))
        for node, elapsed in elapsed_by_node.items()
    ) and max(elapsed_by_node.values(), default=0.0) <= route_limit_s


def _assign_protected_route_fleet(
    routes: list[dict[str, Any]],
    frozen_route_ids: set[str],
    config: PlannerConfig,
) -> bool:
    factor = min(1.0, max(0.1, float(config.comfort_load_factor)))
    available: list[tuple[int, int, str]] = []
    for item in _build_bus_type_configs(config):
        capacity = max(1, int(item.get("capacity", 0) or 0))
        comfort_capacity = max(1, int(math.floor(capacity * factor)))
        for _ in range(max(0, int(item.get("max_count", 0) or 0))):
            available.append((comfort_capacity, capacity, str(item.get("name") or "")))

    for route in routes:
        if _route_display_id(route, 0) not in frozen_route_ids:
            continue
        bus_type = str(route.get("bus_type_name") or route.get("bus_type") or "")
        slot_index = next(
            (index for index, item in enumerate(available) if item[2] == bus_type),
            None,
        )
        if slot_index is None:
            return False
        available.pop(slot_index)

    movable_routes = [
        route for route in routes if _route_display_id(route, 0) not in frozen_route_ids
    ]
    for route in sorted(
        movable_routes,
        key=lambda item: int(item.get("load", 0) or 0),
        reverse=True,
    ):
        load = max(0, int(route.get("load", 0) or 0))
        eligible = sorted(
            (
                (item, index)
                for index, item in enumerate(available)
                if item[0] >= load
            ),
            key=lambda row: row[0],
        )
        if not eligible:
            return False
        (comfort_capacity, capacity, bus_type), slot_index = eligible[0]
        available.pop(slot_index)
        route["bus_type_name"] = bus_type
        route["bus_capacity"] = capacity
        route["comfort_capacity"] = comfort_capacity
    return True


def _build_route_preserving_protected_scenario(
    planner: Any,
    original_points: list[dict[str, Any]],
    current_plan_scenario: dict[str, Any],
    config: PlannerConfig,
    input_records: list[dict[str, Any]],
    *,
    node_time_upper_bounds_builder: Any,
    time_constraint_metadata: dict[str, Any],
    final_time_impact_validator: Any,
    solve_time: list[list[float]],
    baseline_name: str,
    scenario_label: str,
) -> dict[str, Any] | None:
    if normalize_service_direction(config.service_direction) != "To School":
        return None
    current_routes = [deepcopy(route) for route in list(current_plan_scenario.get("routes") or [])]
    current_route_count = len(current_routes)
    saving_count = max(0, int(config.minimum_vehicle_reduction or 0))
    if saving_count <= 0 or saving_count > 3:
        return None
    current_summary = _scenario_exception_summary(current_plan_scenario, config=config)
    frozen_route_ids = {str(item) for item in list(current_summary.get("failed_route_ids") or [])}
    current_by_id = {
        _route_display_id(route, index): route
        for index, route in enumerate(current_routes, start=1)
    }
    movable_ids = sorted(set(current_by_id) - frozen_route_ids)
    if len(movable_ids) <= saving_count:
        return None

    bounds = dict(node_time_upper_bounds_builder(original_points) or {})
    if not bounds:
        return None
    current_elapsed: dict[int, float] = {}
    for route in current_routes:
        nodes = [int(node) for node in list(route.get("nodes") or []) if int(node) != 0]
        current_elapsed.update(_protected_route_elapsed_by_node(nodes, solve_time))

    stop_limit = _effective_route_stop_limit(config)
    route_limit_s = effective_route_duration_limit_minutes(config) * 60.0
    factor = min(1.0, max(0.1, float(config.comfort_load_factor)))
    max_comfort_capacity = max(
        int(math.floor(int(item.get("capacity", 0) or 0) * factor))
        for item in _build_bus_type_configs(config)
    )
    plans: dict[tuple[Any, ...], dict[str, Any]] = {}
    for donor_ids in itertools.combinations(movable_ids, saving_count):
        donor_nodes = [
            int(node)
            for donor_id in donor_ids
            for node in list(current_by_id[donor_id].get("nodes") or [])
            if int(node) != 0
        ]
        recipients = [route_id for route_id in movable_ids if route_id not in donor_ids]
        base_nodes = {
            route_id: [
                int(node)
                for node in list(current_by_id[route_id].get("nodes") or [])
                if int(node) != 0
            ]
            for route_id in recipients
        }
        base_loads = {
            route_id: sum(
                int(original_points[node].get("passenger_count", 0) or 0)
                for node in nodes
            )
            for route_id, nodes in base_nodes.items()
        }
        orderings = (
            donor_nodes,
            list(reversed(donor_nodes)),
            sorted(donor_nodes, key=lambda node: float(solve_time[node][0]), reverse=True),
            sorted(donor_nodes, key=lambda node: float(solve_time[node][0])),
        )
        for ordered_nodes in orderings:
            candidate_nodes = deepcopy(base_nodes)
            candidate_loads = dict(base_loads)
            changed_route_ids: set[str] = set()
            added_cost = 0.0
            max_elapsed_increase = 0.0
            total_elapsed_increase = 0.0
            for node in ordered_nodes:
                demand = max(0, int(original_points[node].get("passenger_count", 0) or 0))
                options: list[tuple[float, float, float, str, list[int]]] = []
                for route_id in recipients:
                    existing = candidate_nodes[route_id]
                    if (
                        len(existing) >= stop_limit
                        or candidate_loads[route_id] + demand > max_comfort_capacity
                    ):
                        continue
                    before = _protected_route_cost(existing, solve_time)
                    for position in range(len(existing) + 1):
                        merged = [*existing[:position], node, *existing[position:]]
                        if not _protected_route_within_solver_bounds(
                            merged,
                            solve_time,
                            bounds,
                            route_limit_s,
                        ):
                            continue
                        elapsed_by_node = _protected_route_elapsed_by_node(merged, solve_time)
                        increases = [
                            max(0.0, elapsed - float(current_elapsed.get(item, elapsed)))
                            for item, elapsed in elapsed_by_node.items()
                        ]
                        after = _protected_route_cost(merged, solve_time)
                        options.append(
                            (
                                max(increases, default=0.0),
                                sum(increases),
                                after - before,
                                route_id,
                                merged,
                            )
                        )
                if not options:
                    break
                worst_increase, summed_increase, delta, route_id, merged = min(options)
                candidate_nodes[route_id] = merged
                candidate_loads[route_id] += demand
                changed_route_ids.add(route_id)
                added_cost += delta
                max_elapsed_increase = max(max_elapsed_increase, worst_increase)
                total_elapsed_increase += summed_increase
            else:
                signature = tuple(
                    (route_id, tuple(candidate_nodes[route_id]))
                    for route_id in sorted(changed_route_ids)
                )
                rank = (
                    max_elapsed_increase,
                    total_elapsed_increase,
                    added_cost,
                    len(changed_route_ids),
                )
                previous = plans.get(signature)
                if previous is None or rank < previous["rank"]:
                    plans[signature] = {
                        "rank": rank,
                        "donor_ids": donor_ids,
                        "nodes": candidate_nodes,
                        "loads": candidate_loads,
                        "changed_route_ids": changed_route_ids,
                    }

    target_vehicle_count = max(0, current_route_count - saving_count)
    previous_osrm_base_url = planner.OSRM_BASE_URL
    planner.OSRM_BASE_URL = planner.resolve_osrm_base_url(original_points)
    try:
        for attempt_index, plan in enumerate(
            sorted(plans.values(), key=lambda item: item["rank"])[:60],
            start=1,
        ):
            candidate_routes: list[dict[str, Any]] = []
            changed_routes: list[dict[str, Any]] = []
            for route_id, current_route in current_by_id.items():
                if route_id in plan["donor_ids"]:
                    continue
                route = deepcopy(current_route)
                if route_id in frozen_route_ids:
                    route["exception_role"] = "frozen_current"
                elif route_id in plan["changed_route_ids"]:
                    route["nodes"] = [*plan["nodes"][route_id], 0]
                    route["load"] = int(plan["loads"][route_id])
                    route["exception_role"] = "optimized_remainder"
                    changed_routes.append(route)
                else:
                    route["exception_role"] = "protected_unchanged"
                candidate_routes.append(route)
            service_nodes = [
                int(node)
                for route in candidate_routes
                for node in list(route.get("nodes") or [])
                if int(node) != 0
            ]
            if sorted(service_nodes) != list(range(1, len(original_points))):
                continue
            planner.enrich_routes_with_actual_driving(original_points, changed_routes)
            planner.annotate_and_price_routes(original_points, changed_routes)
            if not _assign_protected_route_fleet(candidate_routes, frozen_route_ids, config):
                continue
            candidate = planner.build_scenario_result(original_points, candidate_routes, "")
            candidate["output_html"] = ""
            candidate["baseline_name"] = baseline_name
            remaining_stop_count = len(
                set(range(1, len(original_points)))
                - set().union(
                    *[
                        _route_service_node_ids(current_by_id[route_id])
                        for route_id in frozen_route_ids
                    ]
                )
            )
            candidate["time_constraint"] = {
                **deepcopy(time_constraint_metadata or {}),
                "enabled": True,
                "mode": "hard",
                "best_effort": False,
                "strict_satisfied": True,
                "bounded_solver_stop_count": remaining_stop_count,
                "expected_solver_stop_count": remaining_stop_count,
                "applies_to": "exception_preserving_remainder",
                "frozen_route_count": len(frozen_route_ids),
            }
            gate = attach_final_route_traffic_gate(
                planner,
                candidate,
                original_points,
                config,
                input_records,
                scenario_label,
            )
            final_time_impact_validator(candidate, original_points)
            candidate["feasibility_report"] = build_route_feasibility_report(
                candidate,
                gate,
                config,
                current_min_active_vehicle_count=max(
                    0,
                    len(candidate_routes) - len(frozen_route_ids),
                ),
                max_vehicle_count=target_vehicle_count,
                ignored_route_ids=frozen_route_ids,
            )
            candidate = _apply_vehicle_saving_target(
                candidate,
                current_route_count,
                saving_count,
                frozen_route_count=len(frozen_route_ids),
            )
            accepted = _scenario_feasibility_passed(candidate)
            candidate["route_preserving_search"] = {
                "enabled": True,
                "status": "passed" if accepted else "candidate_failed",
                "attempt": attempt_index,
                "candidate_count": len(plans),
                "donor_route_ids": list(plan["donor_ids"]),
                "changed_route_ids": sorted(plan["changed_route_ids"]),
            }
            candidate["exception_preserving"] = {
                "enabled": True,
                "accepted": accepted,
                "strategy": "route_preserving_reallocation",
                "frozen_route_count": len(frozen_route_ids),
                "frozen_route_ids": sorted(frozen_route_ids),
                "current_failure_summary": current_summary,
                "remaining_vehicle_limit": max(0, target_vehicle_count - len(frozen_route_ids)),
                "vehicle_limit_relaxed": False,
            }
            candidate["exception_feasible"] = accepted
            if accepted:
                candidate["constraint_search_outcome"] = {
                    "status": "passed",
                    "strategy": "route_preserving_reallocation",
                    "frozen_route_count": len(frozen_route_ids),
                    "allowed_max_vehicle_count": target_vehicle_count,
                    "feasible_candidate_count": 1,
                }
                return candidate
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url
    return None


def build_exception_preserving_scenario(
    planner: Any,
    original_points: list[dict[str, Any]],
    current_plan_scenario: dict[str, Any] | None,
    config: PlannerConfig,
    input_records: list[dict[str, Any]],
    bus_type_configs: list[dict[str, Any]],
    reduced_vehicle_limit: int | None,
    *,
    standard_scenarios: list[dict[str, Any]],
    node_time_lower_bounds_builder: Any | None = None,
    node_time_upper_bounds_builder: Any | None = None,
    time_constraint_metadata: dict[str, Any] | None = None,
    final_time_impact_validator: Any | None = None,
    solve_time: list[list[float]] | None = None,
    baseline_name: str = "exception_preserving_optimization",
    scenario_label: str = "Exception preserving optimization",
) -> dict[str, Any]:
    del standard_scenarios, reduced_vehicle_limit
    if not current_plan_scenario or current_plan_scenario.get("enabled") is False:
        return _build_skipped_scenario_result("Current plan timing was not available for exception preservation.")

    if (
        callable(node_time_upper_bounds_builder)
        and callable(final_time_impact_validator)
        and solve_time is not None
    ):
        route_preserving = _build_route_preserving_protected_scenario(
            planner,
            original_points,
            current_plan_scenario,
            config,
            input_records,
            node_time_upper_bounds_builder=node_time_upper_bounds_builder,
            time_constraint_metadata=deepcopy(time_constraint_metadata or {}),
            final_time_impact_validator=final_time_impact_validator,
            solve_time=solve_time,
            baseline_name=baseline_name,
            scenario_label=scenario_label,
        )
        if route_preserving is not None:
            return route_preserving

    current_routes = [dict(route) for route in list(current_plan_scenario.get("routes") or [])]
    current_summary = _scenario_exception_summary(current_plan_scenario, config=config)
    failed_route_ids = set(current_summary.get("failed_route_ids") or [])
    failed_routes = [
        route
        for index, route in enumerate(current_routes, start=1)
        if _route_display_id(route, index) in failed_route_ids
    ]
    failed_routes.sort(key=_route_time_window_overrun_minutes, reverse=True)
    current_route_count = int(current_plan_scenario.get("bus_count") or len(current_routes) or 0)
    minimum_vehicle_reduction = max(0, int(config.minimum_vehicle_reduction or 0))
    target_vehicle_count = max(0, current_route_count - minimum_vehicle_reduction)
    attempts: list[dict[str, Any]] = []
    best_accepted_candidate: dict[str, Any] | None = None
    best_failed_candidate: dict[str, Any] | None = None
    remaining_min_vehicle_count = 0
    preflight_infeasible = False

    # Freeze the full current-plan violation set; partial freezes can hide an
    # existing violation inside the optimized remainder.
    for frozen_count in (len(failed_routes),):
        frozen_routes = [deepcopy(route) for route in failed_routes[:frozen_count]]
        frozen_node_ids = (
            set().union(*[_route_service_node_ids(route) for route in frozen_routes])
            if frozen_routes
            else set()
        )
        subset_points, _subset_to_original = _build_exception_subset_points(original_points, frozen_node_ids)
        remaining_service_count = max(0, len(subset_points) - 1)
        remaining_bus_type_configs = _bus_type_configs_after_frozen_routes(bus_type_configs, frozen_routes)
        remaining_max_vehicle_count = max(0, target_vehicle_count - frozen_count)
        remaining_min_vehicle_count = (
            _minimum_vehicle_count_for_hard_constraints(subset_points, remaining_bus_type_configs, config)
            if remaining_service_count
            else 0
        )
        if remaining_service_count:
            remaining_limits = list(
                range(remaining_max_vehicle_count, remaining_min_vehicle_count - 1, -1)
            )
        else:
            remaining_limits = [0]
        if remaining_service_count and not remaining_limits:
            preflight_infeasible = True
            attempts.append(
                {
                    "frozen_route_count": frozen_count,
                    "frozen_route_ids": [
                        _route_display_id(route, index)
                        for index, route in enumerate(frozen_routes, start=1)
                    ],
                    "remaining_stop_count": remaining_service_count,
                    "remaining_vehicle_limit": remaining_max_vehicle_count,
                    "accepted": False,
                    "error": (
                        "Hard minimum vehicle requirement for the unfrozen remainder "
                        f"is {remaining_min_vehicle_count}, above the allowed {remaining_max_vehicle_count}."
                    ),
                }
            )

        for remaining_limit_candidate in remaining_limits:
            try:
                if remaining_service_count:
                    optimized = _compute_scenario_without_render(
                        planner,
                        subset_points,
                        f"Protected remainder ({frozen_count} frozen)",
                        bus_type_configs=remaining_bus_type_configs,
                        reduced_vehicle_limit=remaining_limit_candidate,
                        node_time_lower_bounds_builder=node_time_lower_bounds_builder,
                        node_time_upper_bounds_builder=node_time_upper_bounds_builder,
                        time_constraint_metadata=deepcopy(time_constraint_metadata or {}),
                        final_time_impact_validator=final_time_impact_validator,
                        enable_vehicle_search=False,
                    )
                    optimized_points = list(optimized.get("points") or subset_points)
                    optimized_routes = [
                        _remap_exception_route_nodes(route, optimized_points, f"Opt Bus {index}")
                        for index, route in enumerate(list(optimized.get("routes") or []), start=1)
                    ]
                else:
                    optimized = {}
                    optimized_routes = []
                candidate_frozen_routes = [deepcopy(route) for route in frozen_routes]
                for route in candidate_frozen_routes:
                    route["exception_role"] = "frozen_current"
                combined_routes = candidate_frozen_routes + optimized_routes
                candidate = planner.build_scenario_result(original_points, combined_routes, "")
                candidate["output_html"] = ""
                candidate["baseline_name"] = baseline_name
                if node_time_upper_bounds_builder is not None and remaining_service_count:
                    candidate["time_constraint"] = {
                        **deepcopy(time_constraint_metadata or {}),
                        **dict(optimized.get("time_constraint") or {}),
                        "applies_to": "exception_preserving_remainder",
                        "frozen_route_count": frozen_count,
                    }
                attach_final_route_traffic_gate(
                    planner,
                    candidate,
                    original_points,
                    config,
                    input_records,
                    scenario_label,
                )
                if callable(final_time_impact_validator):
                    final_time_impact_validator(candidate, original_points)
                candidate_route_count = int(candidate.get("bus_count") or len(combined_routes) or 0)
                frozen_route_ids = {
                    _route_display_id(route, index)
                    for index, route in enumerate(candidate_frozen_routes, start=1)
                }
                if solve_time is not None and candidate_route_count < target_vehicle_count:
                    candidate = _repair_failed_routes_with_spare_vehicles(
                        planner,
                        candidate,
                        original_points,
                        solve_time,
                        config,
                        input_records,
                        scenario_label,
                        target_vehicle_count,
                        ignored_failed_route_ids=frozen_route_ids,
                        node_time_lower_bounds=(
                            node_time_lower_bounds_builder(original_points)
                            if node_time_lower_bounds_builder is not None
                            else None
                        ),
                        node_time_upper_bounds=(
                            node_time_upper_bounds_builder(original_points)
                            if node_time_upper_bounds_builder is not None
                            else None
                        ),
                        available_bus_type_configs=bus_type_configs,
                    )
                    combined_routes = list(candidate.get("routes") or [])
                    candidate_route_count = int(candidate.get("bus_count") or len(combined_routes) or 0)
                candidate_summary = _scenario_exception_summary(candidate, config=config)
                candidate_remainder_summary = _scenario_exception_summary(
                    candidate,
                    include_frozen_current=False,
                    config=config,
                )
                remainder_gate = deepcopy(dict(candidate.get("traffic_gate") or {}))
                remainder_gate["failed_route_count"] = int(candidate_remainder_summary["failed_route_count"])
                remainder_gate["failed_route_ids"] = list(candidate_remainder_summary["failed_route_ids"])
                remainder_gate["max_time_window_overrun_minutes"] = float(
                    candidate_remainder_summary["max_overrun_minutes"]
                )
                if remainder_gate["failed_route_count"] == 0 and remainder_gate.get("status") == "failed":
                    remainder_gate["status"] = "passed"
                attempt = {
                    "frozen_route_count": frozen_count,
                    "frozen_route_ids": [
                        _route_display_id(route, index)
                        for index, route in enumerate(candidate_frozen_routes, start=1)
                    ],
                    "remaining_stop_count": remaining_service_count,
                    "remaining_vehicle_limit": remaining_limit_candidate,
                    "route_count": candidate_route_count,
                    "candidate_failure_summary": candidate_summary,
                    "candidate_remainder_failure_summary": candidate_remainder_summary,
                    "vehicle_limit_relaxed": False,
                }
                attempts.append(attempt)
                candidate["feasibility_report"] = build_route_feasibility_report(
                    candidate,
                    remainder_gate,
                    config,
                    current_min_active_vehicle_count=remaining_limit_candidate,
                    max_vehicle_count=target_vehicle_count,
                    ignored_route_ids=frozen_route_ids,
                )
                candidate = _apply_vehicle_saving_target(
                    candidate,
                    current_route_count,
                    minimum_vehicle_reduction,
                    frozen_route_count=frozen_count,
                )
                attempt["vehicle_saving_target"] = dict(candidate.get("vehicle_saving_target") or {})
                accepted = (
                    _exception_candidate_accepted(
                        candidate_remainder_summary,
                        candidate_route_count,
                        current_route_count,
                        target_vehicle_count,
                    )
                    and _scenario_feasibility_passed(candidate)
                    and (
                        not remaining_service_count
                        or node_time_upper_bounds_builder is None
                        or bool(dict(candidate.get("time_constraint") or {}).get("strict_satisfied"))
                    )
                )
                attempt["accepted"] = accepted
                candidate_time_impact_gate = dict(candidate.get("final_time_impact_gate") or {})
                attempt["time_impact_over_limit_stop_count"] = int(
                    candidate_time_impact_gate.get("over_limit_stop_count", 0) or 0
                )
                attempt["time_impact_over_limit_rider_count"] = int(
                    candidate_time_impact_gate.get("over_limit_rider_count", 0) or 0
                )
                attempt["time_impact_max_adverse_minutes"] = float(
                    candidate_time_impact_gate.get("max_adverse_minutes", 0.0) or 0.0
                )
                candidate["exception_preserving"] = {
                    "enabled": True,
                    "accepted": accepted,
                    "frozen_route_count": frozen_count,
                    "frozen_route_ids": list(attempt["frozen_route_ids"]),
                    "current_failure_summary": current_summary,
                    "candidate_failure_summary": candidate_summary,
                    "candidate_remainder_failure_summary": candidate_remainder_summary,
                    "attempts": attempts,
                    "remaining_vehicle_limit": remaining_limit_candidate,
                    "vehicle_limit_relaxed": False,
                }
                candidate["exception_feasible"] = accepted
                if accepted:
                    if (
                        best_accepted_candidate is None
                        or _scenario_candidate_rank(candidate) < _scenario_candidate_rank(best_accepted_candidate)
                    ):
                        best_accepted_candidate = candidate
                elif best_failed_candidate is None or _exception_candidate_rank(
                    candidate_remainder_summary,
                    candidate_route_count,
                    candidate_time_impact_gate,
                ) < _exception_candidate_rank(
                    _scenario_exception_summary(
                        best_failed_candidate,
                        include_frozen_current=False,
                        config=config,
                    ),
                    int(
                        best_failed_candidate.get("bus_count")
                        or len(list(best_failed_candidate.get("routes") or []))
                        or 0
                    ),
                    dict(best_failed_candidate.get("final_time_impact_gate") or {}),
                ):
                    best_failed_candidate = candidate
            except Exception as exc:
                attempts.append(
                    {
                        "frozen_route_count": frozen_count,
                        "frozen_route_ids": [_route_display_id(route, index) for index, route in enumerate(frozen_routes, start=1)],
                        "remaining_stop_count": remaining_service_count,
                        "remaining_vehicle_limit": remaining_limit_candidate,
                        "accepted": False,
                        "vehicle_limit_relaxed": False,
                        "error": str(exc),
                    }
                )
                planner.log(
                    f"[WARN] Protected attempt with {frozen_count} frozen route(s) "
                    f"and remaining vehicle limit {remaining_limit_candidate} failed: {exc}"
                )
                continue

    best_candidate = best_accepted_candidate or best_failed_candidate
    if best_candidate is not None:
        accepted = best_accepted_candidate is not None
        best_candidate["exception_preserving"] = {
            **dict(best_candidate.get("exception_preserving") or {}),
            "enabled": True,
            "accepted": accepted,
            "current_failure_summary": current_summary,
            "attempts": attempts,
        }
        best_candidate["exception_feasible"] = accepted
        best_candidate["constraint_search_outcome"] = {
            "status": "passed" if accepted else "exhausted_without_feasible_candidate",
            "frozen_route_count": len(failed_routes),
            "allowed_remainder_max_vehicle_count": max(0, target_vehicle_count - len(failed_routes)),
            "theoretical_remainder_min_vehicle_count": remaining_min_vehicle_count,
            "attempted_remainder_vehicle_caps": [
                int(item["remaining_vehicle_limit"])
                for item in attempts
                if item.get("remaining_vehicle_limit") is not None
            ],
            "feasible_candidate_count": sum(1 for item in attempts if item.get("accepted")),
        }
        return best_candidate
    error_attempts = [dict(attempt) for attempt in attempts if attempt.get("error")]
    tried_limits = [
        str(attempt.get("remaining_vehicle_limit"))
        for attempt in error_attempts
        if attempt.get("remaining_vehicle_limit") is not None
    ]
    skip_reason = "Exception-preserving optimization could not produce a candidate."
    if error_attempts:
        skip_reason = (
            "Exception-preserving optimization could not solve the unfrozen remainder"
            + (f" after trying remaining vehicle limit(s): {', '.join(tried_limits)}." if tried_limits else ".")
            + f" Last error: {error_attempts[-1].get('error')}"
        )
    skipped_extra: dict[str, Any] = {
        "constraint_search_outcome": {
            "status": "provably_infeasible" if preflight_infeasible else "exhausted_without_feasible_candidate",
            "reason": (
                "minimum_vehicle_lower_bound_exceeds_allowed_maximum"
                if preflight_infeasible
                else "search_exhausted"
            ),
            "frozen_route_count": len(failed_routes),
            "allowed_remainder_max_vehicle_count": max(0, target_vehicle_count - len(failed_routes)),
            "theoretical_remainder_min_vehicle_count": remaining_min_vehicle_count,
            "attempted_remainder_vehicle_caps": [
                int(item["remaining_vehicle_limit"])
                for item in attempts
                if item.get("remaining_vehicle_limit") is not None
            ],
            "feasible_candidate_count": 0,
        },
        "exception_preserving": {
            "enabled": True,
            "accepted": False,
            "current_failure_summary": current_summary,
            "attempts": attempts,
        }
    }
    if node_time_upper_bounds_builder is not None:
        skipped_extra["time_constraint"] = {
            **deepcopy(time_constraint_metadata or {}),
            "enabled": True,
            "mode": "hard",
            "best_effort": False,
        }
    return _build_skipped_scenario_result(skip_reason, skipped_extra)


def _normalize_time_constraint_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _point_time_constraint_keys(point: dict[str, Any], fallback_index: int) -> list[str]:
    keys: list[str] = []
    node_id_value = point.get("_exception_original_node_id")
    if node_id_value is None:
        node_id_value = point.get("node_id", fallback_index)
    try:
        node_id = int(node_id_value)
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


def _scenario_time_impact_rows(
    scenario: dict[str, Any],
    points: list[dict[str, Any]],
    config: PlannerConfig,
) -> tuple[list[dict[str, Any]], int]:
    service_direction = normalize_service_direction(config.service_direction)
    dwell_seconds = max(0.0, float(config.stop_service_minutes or 1) * 60.0)
    rows: list[dict[str, Any]] = []
    unavailable_route_count = 0
    for route_index, route in enumerate(list(scenario.get("routes") or []), start=1):
        nodes = list(route.get("nodes") or [])
        legs = list(route.get("leg_details") or [])
        route_id = _route_display_id(route, route_index)
        gate = dict(route.get("final_route_traffic_gate") or {})
        verified_drive_s = float(gate.get("verified_drive_duration_s", 0.0) or 0.0)
        raw_duration_s = float(route.get("time_s", 0.0) or 0.0)
        max_cumulative_s = sum(float(dict(leg or {}).get("duration_s", 0.0) or 0.0) for leg in legs)
        modeled_route_drive_s = float(route.get("raw_osrm_time_s", 0.0) or 0.0) or sum(
            float(
                dict(leg or {}).get(
                    "raw_osrm_duration_s",
                    dict(leg or {}).get("duration_s", 0.0),
                )
                or 0.0
            )
            for leg in legs
        )
        base_duration_s = raw_duration_s if raw_duration_s > 0 else max_cumulative_s
        if verified_drive_s <= 0 or base_duration_s <= 0 or modeled_route_drive_s <= 0:
            unavailable_route_count += 1
            continue
        scale = verified_drive_s / base_duration_s
        route_duration_s = max(verified_drive_s, max_cumulative_s * scale)
        service_orders = [order for order, node in enumerate(nodes) if int(node) != 0]
        cumulative_s = 0.0
        modeled_cumulative_s = 0.0
        for order, node in enumerate(nodes):
            if order > 0 and order - 1 < len(legs):
                leg = dict(legs[order - 1] or {})
                cumulative_s += float(leg.get("duration_s", 0.0) or 0.0)
                modeled_cumulative_s += float(
                    leg.get("raw_osrm_duration_s", leg.get("duration_s", 0.0)) or 0.0
                )
            node_index = int(node)
            if node_index == 0 or node_index < 0 or node_index >= len(points):
                continue
            if service_direction == "To School":
                downstream_dwell_count = sum(1 for item in service_orders if item >= order)
                remaining_drive_s = max(0.0, route_duration_s - cumulative_s * scale)
                offset_s = -(remaining_drive_s + downstream_dwell_count * dwell_seconds)
                modeled_elapsed_s = max(0.0, modeled_route_drive_s - modeled_cumulative_s)
            else:
                prior_dwell_count = sum(1 for item in service_orders if item < order)
                offset_s = cumulative_s * scale + prior_dwell_count * dwell_seconds
                modeled_elapsed_s = modeled_cumulative_s
            point = dict(points[node_index] or {})
            rows.append(
                {
                    "route_id": route_id,
                    "node_index": node_index,
                    "keys": _point_time_constraint_keys(point, node_index),
                    "offset_s": offset_s,
                    "modeled_elapsed_s": modeled_elapsed_s,
                    "affected_rider_count": max(0, int(point.get("passenger_count", 0) or 0)),
                }
            )
    return rows, unavailable_route_count


def _build_final_time_impact_validator(
    current_plan_scenario: dict[str, Any],
    original_points: list[dict[str, Any]],
    config: PlannerConfig,
    threshold_minutes: float,
) -> Any:
    current_rows, current_unavailable_route_count = _scenario_time_impact_rows(
        current_plan_scenario,
        original_points,
        config,
    )
    current_lookup: dict[str, dict[str, Any]] = {}
    for row in current_rows:
        for key in list(row.get("keys") or []):
            current_lookup.setdefault(str(key), row)
    threshold_minutes = max(0.0, float(threshold_minutes))
    service_direction = normalize_service_direction(config.service_direction)

    def validate(scenario: dict[str, Any], scenario_points: list[dict[str, Any]]) -> dict[str, Any]:
        candidate_rows, unavailable_route_count = _scenario_time_impact_rows(
            scenario,
            scenario_points,
            config,
        )
        violations: list[dict[str, Any]] = []
        unavailable_stop_count = 0
        compared_stop_count = 0
        compared_rider_count = 0
        over_limit_rider_count = 0
        max_adverse_minutes = 0.0
        for row in candidate_rows:
            current_row = next(
                (
                    current_lookup[str(key)]
                    for key in list(row.get("keys") or [])
                    if str(key) in current_lookup
                ),
                None,
            )
            if current_row is None:
                unavailable_stop_count += 1
                continue
            compared_stop_count += 1
            affected_rider_count = max(
                int(row.get("affected_rider_count", 0) or 0),
                int(current_row.get("affected_rider_count", 0) or 0),
            )
            compared_rider_count += affected_rider_count
            delta_s = float(row.get("offset_s", 0.0) or 0.0) - float(
                current_row.get("offset_s", 0.0) or 0.0
            )
            adverse_minutes = max(0.0, -delta_s if service_direction == "To School" else delta_s) / 60.0
            max_adverse_minutes = max(max_adverse_minutes, adverse_minutes)
            if adverse_minutes <= threshold_minutes + 1e-9:
                continue
            over_limit_rider_count += affected_rider_count
            violations.append(
                {
                    "route_id": str(row.get("route_id") or ""),
                    "node_index": int(row.get("node_index", 0) or 0),
                    "adverse_minutes": adverse_minutes,
                    "over_limit_minutes": adverse_minutes - threshold_minutes,
                    "modeled_elapsed_s": float(row.get("modeled_elapsed_s", 0.0) or 0.0),
                    "affected_rider_count": affected_rider_count,
                }
            )
        unavailable_stop_count += max(0, len(scenario_points) - 1 - len(candidate_rows))
        status = "passed"
        if current_unavailable_route_count or unavailable_route_count or unavailable_stop_count:
            status = "unavailable"
        elif violations:
            status = "failed"
        gate = {
            "status": status,
            "threshold_minutes": threshold_minutes,
            "expected_stop_count": max(0, len(scenario_points) - 1),
            "compared_stop_count": compared_stop_count,
            "compared_rider_count": compared_rider_count,
            "unavailable_stop_count": unavailable_stop_count,
            "unavailable_route_count": unavailable_route_count + current_unavailable_route_count,
            "over_limit_stop_count": len(violations),
            "over_limit_rider_count": over_limit_rider_count,
            "max_adverse_minutes": max_adverse_minutes,
            "max_over_limit_minutes": max(
                [float(item["over_limit_minutes"]) for item in violations],
                default=0.0,
            ),
            "violations": violations,
        }
        scenario["final_time_impact_gate"] = gate
        time_constraint = dict(scenario.get("time_constraint") or {})
        time_constraint["final_validation_required"] = True
        time_constraint["final_validation_status"] = status
        time_constraint["final_satisfied"] = status == "passed"
        scenario["time_constraint"] = time_constraint
        return gate

    return validate


def _tighten_final_time_impact_bounds(
    planner: Any,
    gate: dict[str, Any] | None,
    *,
    limit: int = 1,
    minimum_bounds: dict[int, int] | None = None,
) -> bool:
    gate = dict(gate or {})
    if str(gate.get("status") or "") != "failed":
        return False
    bounds = dict(getattr(planner, "NODE_TIME_UPPER_BOUNDS", {}) or {})
    tightened: list[dict[str, int]] = []
    violations = sorted(
        list(gate.get("violations") or []),
        key=lambda item: float(dict(item).get("over_limit_minutes", 0.0) or 0.0),
        reverse=True,
    )
    for violation in violations[: max(1, int(limit or 1))]:
        node_index = int(dict(violation).get("node_index", 0) or 0)
        current_bound = bounds.get(node_index)
        modeled_elapsed_s = float(dict(violation).get("modeled_elapsed_s", 0.0) or 0.0)
        over_limit_s = float(dict(violation).get("over_limit_minutes", 0.0) or 0.0) * 60.0
        if current_bound is None or modeled_elapsed_s <= 0 or over_limit_s <= 0:
            continue
        target_bound = max(
            int(dict(minimum_bounds or {}).get(node_index, 0) or 0),
            int(math.floor(modeled_elapsed_s - over_limit_s - 30.0)),
        )
        next_bound = min(int(current_bound) - 1, target_bound)
        if next_bound < 0 or next_bound >= int(current_bound):
            continue
        bounds[node_index] = next_bound
        tightened.append({"node_index": node_index, "from_s": int(current_bound), "to_s": next_bound})
    if not tightened:
        return False
    planner.NODE_TIME_UPPER_BOUNDS = bounds
    gate["bound_tightening_count"] = len(tightened)
    gate["bound_tightenings"] = tightened
    return True


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
) -> tuple[Any | None, Any | None, dict[str, Any]]:
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
        return None, None, metadata
    if not assessment_time:
        metadata["skipped_reason"] = "No current-plan timing matrix was available."
        return None, None, metadata
    if len(original_points) <= 1:
        metadata["skipped_reason"] = "No service stops were available for time-impact constraints."
        return None, None, metadata

    working_time = (
        transpose_matrix(assessment_time)
        if normalize_service_direction(service_direction) == "To School"
        else assessment_time
    )
    upper_bound_by_key: dict[str, int] = {}
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
                existing = upper_bound_by_key.get(key)
                upper_bound_by_key[key] = upper_bound_s if existing is None else min(existing, upper_bound_s)

    if not upper_bound_by_key:
        metadata["skipped_reason"] = "No matched current-plan stops could be converted into time constraints."
        return None, None, metadata

    metadata["enabled"] = True
    metadata["impact_direction"] = "adverse_only"
    metadata["bounded_current_stop_count"] = len(bounded_nodes)

    def matched_upper_bounds(point: dict[str, Any], index: int) -> list[int]:
        return [
            upper_bound_by_key[key]
            for key in _point_time_constraint_keys(point, index)
            if key in upper_bound_by_key
        ]

    def build_node_time_upper_bounds(solver_points: list[dict[str, Any]]) -> dict[int, int]:
        bounds: dict[int, int] = {}
        for index, point in enumerate(list(solver_points or [])):
            if index == 0 or bool(dict(point or {}).get("is_depot")):
                continue
            upper_bounds = matched_upper_bounds(dict(point or {}), index)
            if upper_bounds:
                bounds[index] = min(upper_bounds)
        return bounds

    return None, build_node_time_upper_bounds, metadata


def _format_time_impact_limit_minutes(value: Any) -> str:
    try:
        numeric = max(0.0, float(value))
    except (TypeError, ValueError):
        numeric = float(TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.1f}".rstrip("0").rstrip(".")


def run_backend_planner_with_prepared_data(
    prepared_payload: dict[str, Any],
    config: PlannerConfig | None = None,
    progress_callback: Any | None = None,
    require_fresh_final_traffic: bool = False,
) -> dict[str, Any]:
    config = create_request_scoped_config(config or PlannerConfig())
    if require_fresh_final_traffic:
        setattr(config, "_fresh_final_route_traffic_required", True)
    input_records = _normalize_input_records(prepared_payload.get("input_records") or [])
    if not input_records:
        raise ValueError("Prepared payload does not contain any input records.")

    planner = load_legacy_planner()
    _apply_config(planner, config, input_records)
    planner.CURRENT_CURRENCY_CODE = str(prepared_payload.get("currency_code") or planner.determine_currency_code(input_records))
    traffic_profile_name = normalize_traffic_profile_name(config.traffic_profile_name)
    traffic_profile_context = "Unscaled OSRM candidate search with direct final-route provider validation"

    original_points = deepcopy(prepared_payload.get("original_points") or [])
    subway_points = deepcopy(prepared_payload.get("subway_points") or [])
    nearby_points = deepcopy(prepared_payload.get("nearby_points") or [])
    current_plan = deepcopy(prepared_payload.get("current_plan") or {})

    log_stream = _StreamingLogCapture(callback=progress_callback)
    started_at = time.perf_counter()
    scheduled_run_traffic_refresh: dict[str, Any] = {
        "enabled": bool(require_fresh_final_traffic),
        "status": "pending" if require_fresh_final_traffic else "not_requested",
    }
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        planner.log(
            f"[BACKEND] Starting route optimization for service direction `{normalize_service_direction(config.service_direction)}`."
        )
        planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
        planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
        planner.log(
            "[BACKEND] Solver uses unscaled OSRM travel times; final AMap/Kakao route calls "
            "are the only traffic acceptance evidence."
        )
        planner.log(
            f"[BACKEND] Using traffic profile `{traffic_profile_name}` "
            "with unscaled OSRM candidate times "
            f"({traffic_profile_context})."
        )
        solver_bus_type_configs = _build_bus_type_configs(config)
        current_plan_route_count_for_reduction = _current_plan_route_count(current_plan)
        minimum_vehicle_reduction = max(0, int(config.minimum_vehicle_reduction or 0))
        assessment_time = None
        assessment_distance = None
        current_plan_assessment = None
        current_plan_scenario = None
        if require_fresh_final_traffic:
            if not current_plan:
                raise RuntimeError("Scheduled run requires a current plan for fresh traffic validation.")
            assessment_time, assessment_distance = _build_assessment_metric_matrices(planner, original_points)
            current_plan_assessment = assess_current_plan(
                planner,
                current_plan,
                original_points,
                config,
                solve_time=assessment_time,
                solve_distance=assessment_distance,
            )
            current_plan_scenario = build_current_plan_map_scenario(
                planner,
                current_plan_assessment,
                original_points,
            )
            current_plan_gate = attach_current_plan_traffic_gate(
                planner,
                current_plan_scenario,
                original_points,
                config,
                input_records,
            )
            scheduled_run_traffic_refresh = calibrate_scheduled_current_plan_traffic(
                current_plan_scenario,
                current_plan_gate,
            )
            refresh_status = str(scheduled_run_traffic_refresh.get("status") or "").strip().lower()
            if refresh_status == "ready":
                traffic_profile_name = f"{str(scheduled_run_traffic_refresh.get('provider') or '').upper()} Current (Scheduled)"
                traffic_profile_context = (
                    "Fresh current-plan direct-provider validation for this scheduled run"
                )
                planner.TRAFFIC_PROFILE_NAME = traffic_profile_name
                planner.TRAFFIC_PROFILE_CONTEXT = traffic_profile_context
                planner.log(
                    "[BACKEND] Scheduled traffic refresh completed direct-provider evidence; "
                    "the user time window remains unchanged."
                )
            elif refresh_status != "not_applicable":
                reason = str(scheduled_run_traffic_refresh.get("reason") or "traffic_refresh_unavailable")
                raise RuntimeError(f"Scheduled fresh traffic validation failed: {reason}")
        planner.log("[BACKEND] Free optimization baseline solve paused; X-minute constrained is the primary baseline.")
        free_optimization_baseline = _build_skipped_scenario_result(
            "Free optimization baseline solve is retired; use Strict Plan or Protected Plan.",
            {
                "baseline_name": "free_optimization_baseline",
                "display_name": "Free Optimization Baseline",
                "scenario_label": "Free Optimization Baseline",
            },
        )
        subway_result = _build_skipped_scenario_result(
            "Subway aggregation is retired from the two-plan solver."
        )
        nearby_result = _build_skipped_scenario_result(
            "Nearby aggregation is retired from the two-plan solver."
        )
        if current_plan_scenario is None:
            if current_plan:
                assessment_time, assessment_distance = _build_assessment_metric_matrices(planner, original_points)
            current_plan_assessment = assess_current_plan(
                planner,
                current_plan,
                original_points,
                config,
                solve_time=assessment_time,
                solve_distance=assessment_distance,
            )
            current_plan_scenario = build_current_plan_map_scenario(
                planner,
                current_plan_assessment,
                original_points,
            )
            attach_current_plan_traffic_gate(
                planner,
                current_plan_scenario,
                original_points,
                config,
                input_records,
            )
        time_impact_limit_minutes = max(
            0.0,
            float(
                config.time_impact_limit_minutes
                if config.time_impact_limit_minutes is not None
                else TIME_IMPACT_ACCEPTANCE_THRESHOLD_MINUTES
            ),
        )
        time_impact_limit_label = _format_time_impact_limit_minutes(time_impact_limit_minutes)
        time_constrained_display_label = "Strict Plan"
        time_constrained_solver_label = (
            f"Strict plan ({time_impact_limit_label}-minute time-impact limit)"
        )
        (
            time_constraint_lower_builder,
            time_constraint_builder,
            time_constraint_metadata,
        ) = _build_time_acceptance_constraint_builder(
            current_plan_assessment,
            original_points,
            assessment_time,
            config.service_direction,
            time_impact_limit_minutes,
        )
        time_constraint_metadata.update(
            {
                "display_name": time_constrained_display_label,
                "time_impact_limit_minutes": time_impact_limit_minutes,
                "final_validation_required": True,
            }
        )
        final_time_impact_validator = _build_final_time_impact_validator(
            current_plan_scenario,
            original_points,
            config,
            time_impact_limit_minutes,
        )
        if time_constraint_builder is not None:
            try:
                hard_time_constraint_metadata = deepcopy(time_constraint_metadata)
                time_constrained_result = _solve_vehicle_ladder_scenario(
                    planner,
                    original_points,
                    time_constrained_solver_label,
                    current_route_count=current_plan_route_count_for_reduction,
                    minimum_vehicle_reduction=minimum_vehicle_reduction,
                    bus_type_configs=solver_bus_type_configs,
                    node_time_lower_bounds_builder=time_constraint_lower_builder,
                    node_time_upper_bounds_builder=time_constraint_builder,
                    time_constraint_metadata=hard_time_constraint_metadata,
                    final_time_impact_validator=final_time_impact_validator,
                )
                time_constrained_result["baseline_name"] = "time_constrained_optimization"
                time_constrained_result["display_name"] = time_constrained_display_label
                time_constrained_result["scenario_label"] = time_constrained_display_label
                time_constrained_result["time_impact_limit_minutes"] = time_impact_limit_minutes
                time_constrained_result["time_constraint"] = {
                    **hard_time_constraint_metadata,
                    **dict(time_constrained_result.get("time_constraint") or {}),
                    "enabled": True,
                    "bounded_solver_stop_count": int(
                        dict(time_constrained_result.get("time_constraint") or {}).get(
                            "bounded_solver_stop_count",
                            hard_time_constraint_metadata.get("bounded_solver_stop_count", 0),
                        )
                        or 0
                    ),
                }
            except Exception as exc:
                planner.log(f"[WARN] {time_constrained_solver_label} skipped: {exc}")
                time_constrained_result = _build_skipped_scenario_result(
                    f"{time_constrained_solver_label} was infeasible: {exc}",
                    {
                        "baseline_name": "time_constrained_optimization",
                        "display_name": time_constrained_display_label,
                        "scenario_label": time_constrained_display_label,
                        "time_impact_limit_minutes": time_impact_limit_minutes,
                        "time_constraint": {
                            **time_constraint_metadata,
                            "enabled": False,
                            "strict_error": str(exc),
                        }
                    },
                )
        else:
            time_constrained_result = _build_skipped_scenario_result(
                str(time_constraint_metadata.get("skipped_reason") or "Time-impact constraints were not available."),
                {
                    "baseline_name": "time_constrained_optimization",
                    "display_name": time_constrained_display_label,
                    "scenario_label": time_constrained_display_label,
                    "time_impact_limit_minutes": time_impact_limit_minutes,
                    "time_constraint": time_constraint_metadata,
                },
            )
        original_result = free_optimization_baseline
        if time_constraint_builder is not None:
            protected_time_constraint_metadata = {
                **deepcopy(time_constraint_metadata),
                "source": "exception_preserving_remainder",
                "display_name": "Protected Plan",
            }
            exception_preserving_result = build_exception_preserving_scenario(
                planner,
                original_points,
                current_plan_scenario,
                config,
                input_records,
                solver_bus_type_configs,
                None,
                standard_scenarios=[time_constrained_result],
                node_time_lower_bounds_builder=time_constraint_lower_builder,
                node_time_upper_bounds_builder=time_constraint_builder,
                time_constraint_metadata=protected_time_constraint_metadata,
                final_time_impact_validator=final_time_impact_validator,
                solve_time=assessment_time,
            )
            exception_preserving_result["display_name"] = "Protected Plan"
            exception_preserving_result["scenario_label"] = "Protected Plan"
            exception_preserving_result["time_impact_limit_minutes"] = time_impact_limit_minutes
        else:
            exception_preserving_result = _build_skipped_scenario_result(
                str(time_constraint_metadata.get("skipped_reason") or "Time-impact constraints were not available."),
                {
                    "baseline_name": "exception_preserving_optimization",
                    "display_name": "Protected Plan",
                    "scenario_label": "Protected Plan",
                    "time_impact_limit_minutes": time_impact_limit_minutes,
                    "time_constraint": time_constraint_metadata,
                },
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
        current_plan_comparison = compare_current_plan_to_baseline(current_plan_assessment, time_constrained_result)
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
        "exception_preserving": exception_preserving_result,
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
        "exception_preserving_optimization": exception_preserving_result,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "input_address_review": input_address_review,
        "traffic_profile_name": traffic_profile_name,
        "traffic_profile_context": traffic_profile_context,
        "scheduled_run_traffic_refresh": scheduled_run_traffic_refresh,
        "service_direction": normalize_service_direction(config.service_direction),
        "planner_config": asdict(config),
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
        "exception_preserving_optimization": exception_preserving_result,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "input_address_review": input_address_review,
        "traffic_profile_name": traffic_profile_name,
        "traffic_profile_context": traffic_profile_context,
        "scheduled_run_traffic_refresh": scheduled_run_traffic_refresh,
        "service_direction": normalize_service_direction(config.service_direction),
        "planner_config": asdict(config),
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
