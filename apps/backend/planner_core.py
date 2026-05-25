from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import asdict, dataclass
import importlib.util
import io
import itertools
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urljoin
import uuid

import pandas as pd
try:
    from .BusingProblem import transpose_matrix
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from BusingProblem import transpose_matrix
import requests


BASE_DIR = Path(__file__).resolve().parent
LEGACY_PLANNER_PATH = BASE_DIR / "BusingProblem.py"
OUTPUT_DIR = BASE_DIR / "outputs"
CACHE_DIR = BASE_DIR / "cache"
PLANNER_RESULT_CACHE_PATH = CACHE_DIR / "planner_result_cache.json"
ROUTE_METRICS_CACHE_PATH = CACHE_DIR / "route_metrics_cache.json"
ROUTE_GEOMETRY_CACHE_PATH = CACHE_DIR / "route_geometry_cache.json"
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
        "Off-Peak": 1.4,
        "AM Peak": 1.6,
        "PM Peak": 1.75,
    },
    ("CHINA", "BEIJING"): {
        "Off-Peak": 1.0,
        "AM Peak": 1.42,
        "PM Peak": 1.72,
    },
    ("CHINA", "SUZHOU"): {
        "Off-Peak": 1.0,
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
    traffic_profile_name: str = "Off-Peak"
    service_direction: str = "From School"
    matrix_nearest_neighbors: int = 10
    matrix_candidate_radius_km: float = 15.0
    operating_cost_per_km: float = 0.0
    revenue_rules: list[dict[str, float | None]] | None = None
    original_output_name: str = "school_bus_routes.html"
    current_plan_output_name: str = "current_plan_routes.html"
    subway_output_name: str = "school_bus_routes_subway_aggregated.html"
    nearby_output_name: str = "school_bus_routes_nearby_aggregated.html"
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

    original_non_depot = max(0, original_valid_stops - 1)
    subway_non_depot = max(0, subway_valid_stops - 1)
    nearby_non_depot = max(0, nearby_valid_stops - 1)

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

    for scenario_key in ("original", "subway", "nearby", "further_most", "further_most_nearby"):
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
    planner.CURRENT_CURRENCY_CODE = str(hydrated.get("currency_code", "USD"))
    planner.BUS_TYPE_CONFIGS = _build_bus_type_configs(config)
    (
        planner.VEHICLE_FIXED_COST,
        planner.MIN_LOAD_TARGET,
        planner.MIN_LOAD_PENALTY,
    ) = _build_slot_policy_maps(config)
    planner.OPERATING_COST_PER_KM = config.operating_cost_per_km
    planner.MATRIX_NEAREST_NEIGHBORS = config.matrix_nearest_neighbors
    planner.MATRIX_MAX_CANDIDATE_DISTANCE_KM = config.matrix_candidate_radius_km
    if config.revenue_rules is not None:
        planner.REVENUE_RULES = config.revenue_rules

    for scenario_key in ("original", "subway", "nearby", "further_most", "further_most_nearby"):
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

        passenger_count = sum(
            int(stop.get("passenger_count", 0))
            for stop in ordered_stops
            if not bool(stop.get("is_depot"))
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
        if len(ordered_stops) > 12:
            recommendation_set.append(
                f"Route {route_id} has {len(ordered_stops)} scheduled stops. Review whether some nearby stops can be consolidated."
            )
        route_summaries.append(
            {
                "route_id": route_id,
                "bus_type": bus_type,
                "capacity": capacity,
                "stop_count": len(ordered_stops),
                "passenger_count": passenger_count,
                "distance_m": route_distance_m,
                "duration_s": route_duration_s,
                "load_factor": load_factor,
                "stop_ids": [str(stop.get("stop_id", "")).strip() for stop in ordered_stops],
                "addresses": [str(stop.get("address", "")).strip() for stop in ordered_stops],
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
        )

    route_count = len(route_summaries)
    average_load_factor = (total_load_factor / route_count) if route_count else 0.0
    avg_route_distance_m = (total_distance_m / route_count) if route_count else 0.0
    avg_route_duration_s = (total_duration_s / route_count) if route_count else 0.0
    if route_count >= 2 and average_load_factor < 0.6:
        recommendation_set.append(
            "The imported plan has a low overall average load factor. Review whether some routes can be merged or downsized."
        )

    return {
        "route_count": route_count,
        "stop_count": len(stop_rows),
        "assignment_count": len(assignments),
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
        "stop_count": len(points),
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

    return {
        "stops": stops,
        "assignments": assignments,
        "fleet": [dict(item) for item in list(fleet or [])],
        "service_direction": normalized_direction,
        "summary": {
            "stop_count": len(stops),
            "assignment_count": len(assignments),
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
        package_transfer_distance_m = min(transfer_distances_m) if transfer_distances_m else float("inf")

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
            transfer_to_route_min_distance_m=package_transfer_distance_m,
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
    scenario["stop_count"] = int(scenario.get("stop_count", len(points)))
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
) -> dict[str, Any]:
    if len(points) <= 1:
        routes: list[dict[str, Any]] = []
        result = planner.build_scenario_result(points, routes, "")
        result["output_html"] = ""
        return result

    planner.log(f"[BACKEND] Building {scenario_label} scenario with {len(points)} total points.")
    scenario_osrm_base_url = planner.resolve_osrm_base_url(points)
    previous_osrm_base_url = planner.OSRM_BASE_URL
    previous_bus_type_configs = deepcopy(getattr(planner, "BUS_TYPE_CONFIGS", []))
    planner.OSRM_BASE_URL = scenario_osrm_base_url
    if bus_type_configs is not None:
        planner.BUS_TYPE_CONFIGS = deepcopy(bus_type_configs)
    planner.log(f"[BACKEND] Using OSRM backend {scenario_osrm_base_url} for {scenario_label} scenario.")
    try:
        try:
            planner.log(f"[BACKEND] Building full OSRM matrix for {scenario_label} scenario.")
            solve_time, solve_distance = planner.build_osrm_full_matrix(points)
        except Exception as exc:
            planner.log(f"[WARN] OSRM full matrix failed for {scenario_label}; falling back to seed matrix: {exc}")
            solve_time, solve_distance = planner.seed_edge_metrics(points)
        final_routes = planner.solve_routes(points, solve_time, solve_distance)
        planner.enrich_routes_with_actual_driving(points, final_routes)
        planner.annotate_and_price_routes(points, final_routes)
        result = planner.build_scenario_result(points, final_routes, "")
        result["output_html"] = ""
        return result
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url
        planner.BUS_TYPE_CONFIGS = previous_bus_type_configs


def _build_skipped_scenario_result(reason: str) -> dict[str, Any]:
    return {
        "points": None,
        "routes": None,
        "output_html": "",
        "bus_count": 0,
        "stop_count": 0,
        "bus_mix": {},
        "enabled": False,
        "skipped_reason": reason,
    }


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

    original_points = deepcopy(prepared_payload.get("original_points") or [])
    subway_points = deepcopy(prepared_payload.get("subway_points") or [])
    nearby_points = deepcopy(prepared_payload.get("nearby_points") or [])
    current_plan = deepcopy(prepared_payload.get("current_plan") or {})

    log_stream = _StreamingLogCapture(callback=progress_callback)
    started_at = time.perf_counter()
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        planner.log(
            f"[BACKEND] Starting route optimization for service direction `{normalize_service_direction(config.service_direction)}`."
        )
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
        )
        free_optimization_baseline = _attach_free_baseline_metadata(
            original_result,
            config,
            free_baseline_bus_type_configs,
        )
        if config.include_subway_aggregation_scenario:
            subway_result = _compute_scenario_without_render(planner, subway_points, "Subway aggregated")
        else:
            planner.log("[BACKEND] Skipping subway aggregation scenario for this run.")
            subway_result = _build_skipped_scenario_result(
                "Subway alternative baseline was disabled for this run."
            )
        if config.include_nearby_aggregation_scenario:
            nearby_result = _compute_scenario_without_render(planner, nearby_points, "Nearby-address aggregated")
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
        )
        current_plan_scenario = build_current_plan_map_scenario(
            planner,
            current_plan_assessment,
            original_points,
        )
        like_for_like_baseline = assess_current_plan(
            planner,
            current_plan,
            original_points,
            config,
            solve_time=assessment_time,
            solve_distance=assessment_distance,
            optimize_stop_order=True,
        )
        current_plan_like_for_like_comparison = compare_current_plan_to_like_for_like_baseline(
            current_plan_assessment,
            like_for_like_baseline,
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
        constrained_improvement_plan, constrained_selected_moves, constrained_package_summaries = build_constrained_improvement_current_plan(
            current_plan,
            route_reallocation_analysis,
            config,
        )
        constrained_improvement_baseline = assess_current_plan(
            planner,
            constrained_improvement_plan,
            original_points,
            config,
            solve_time=assessment_time,
            solve_distance=assessment_distance,
            optimize_stop_order=True,
        )
        constrained_package_summaries = _enrich_constrained_package_summaries(
            constrained_package_summaries,
            current_plan_assessment,
            constrained_improvement_baseline,
        )
        current_plan_constrained_comparison = compare_current_plan_to_assessment_baseline(
            current_plan_assessment,
            constrained_improvement_baseline,
            baseline_name="constrained-improvement baseline",
            optimization_phrase="limited route-to-route reallocations",
            constrained_package_summaries=constrained_package_summaries,
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
    log_stream.flush()

    structured_results = {
        "original": original_result,
        "current_plan": current_plan_scenario,
        "subway": subway_result,
        "nearby": nearby_result,
        "further_most": further_most_result,
        "further_most_nearby": further_most_nearby_result,
        "input_address_count": len(input_records),
        "valid_stop_count": len(original_points),
        "currency_code": planner.CURRENT_CURRENCY_CODE,
        "job_id": config.output_directory_name,
        "current_plan_assessment": current_plan_assessment,
        "current_plan_scenario": current_plan_scenario,
        "like_for_like_baseline": like_for_like_baseline,
        "current_plan_like_for_like_comparison": current_plan_like_for_like_comparison,
        "constrained_improvement_baseline": constrained_improvement_baseline,
        "current_plan_constrained_comparison": current_plan_constrained_comparison,
        "constrained_selected_moves": constrained_selected_moves,
        "constrained_package_summaries": constrained_package_summaries,
        "free_optimization_baseline": free_optimization_baseline,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "further_most_private_access_analysis": further_most_private_access_analysis,
        "further_most_nearby_private_access_analysis": further_most_nearby_private_access_analysis,
        "traffic_profile_name": traffic_profile_name,
        "traffic_time_multiplier": traffic_time_multiplier,
        "traffic_profile_context": traffic_profile_context,
        "service_direction": normalize_service_direction(config.service_direction),
    }
    structured_results = attach_output_paths_to_structured_results(structured_results, config)
    return {
        "structured_results": structured_results,
        "summary": summarize_structured_results(structured_results, len(input_records)),
        "logs": log_stream.getvalue(),
        "elapsed_seconds": time.perf_counter() - started_at,
        "current_plan_assessment": current_plan_assessment,
        "current_plan_scenario": current_plan_scenario,
        "like_for_like_baseline": like_for_like_baseline,
        "current_plan_like_for_like_comparison": current_plan_like_for_like_comparison,
        "constrained_improvement_baseline": constrained_improvement_baseline,
        "current_plan_constrained_comparison": current_plan_constrained_comparison,
        "constrained_selected_moves": constrained_selected_moves,
        "constrained_package_summaries": constrained_package_summaries,
        "free_optimization_baseline": free_optimization_baseline,
        "current_plan_comparison": current_plan_comparison,
        "route_reallocation_analysis": route_reallocation_analysis,
        "nearby_private_access_analysis": nearby_private_access_analysis,
        "further_most_private_access_analysis": further_most_private_access_analysis,
        "further_most_nearby_private_access_analysis": further_most_nearby_private_access_analysis,
        "traffic_profile_name": traffic_profile_name,
        "traffic_time_multiplier": traffic_time_multiplier,
        "traffic_profile_context": traffic_profile_context,
        "service_direction": normalize_service_direction(config.service_direction),
    }


def attach_output_paths_to_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    hydrated = deepcopy(results)
    existing_path_map = hydrated.get("output_paths")
    expected_keys = ("original", "current_plan", "subway", "nearby", "further_most", "further_most_nearby")
    if isinstance(existing_path_map, dict) and all(key in existing_path_map for key in expected_keys):
        path_map = {key: str(existing_path_map[key]) for key in expected_keys}
    else:
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
    if structured_results:
        summary = summarize_structured_results(structured_results, len(normalized_records))
    else:
        summary = summarize_logs(logs, len(normalized_records))

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
