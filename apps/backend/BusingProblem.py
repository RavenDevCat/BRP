from __future__ import annotations

import colorsys
import html
import json
import math
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import folium
import requests
from folium import Element
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from api_rate_limit import CrossProcessRateLimiter


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("BRP_BACKEND_CACHE_DIR", str(BASE_DIR / "cache"))).expanduser()
GEOCODE_CACHE_PATH = CACHE_DIR / "geocode_cache.json"
SUBWAY_CACHE_PATH = CACHE_DIR / "subway_search_cache.json"

AMAP_KEY = "67552867e1fe0125b04b7437cf0c392d"
GOOGLE_KEY = ""

AMAP_GEOCODE_MAX_QPS = 2.8
AMAP_PLACES_MAX_QPS = 2.8
AMAP_ROUTING_MAX_QPS = 2.8
AMAP_MATRIX_MAX_QPS = 2.8
REQUEST_TIMEOUT = 20
OSRM_USE_BUILTIN_DEFAULTS = os.environ.get("OSRM_USE_BUILTIN_DEFAULTS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
OSRM_BASE_URL = os.environ.get(
    "OSRM_BASE_URL",
    "http://127.0.0.1:5002" if OSRM_USE_BUILTIN_DEFAULTS else "",
).strip()
OSRM_LOCATION_DEFAULTS: dict[tuple[str, str], str] = {
    ("CHINA", "SHANGHAI"): "http://127.0.0.1:5002",
    ("CHINA", "BEIJING"): "http://127.0.0.1:5003",
    ("CHINA", "SUZHOU"): "http://127.0.0.1:5004",
    ("CHINA", "XIAN"): "http://127.0.0.1:5005",
    ("SOUTH KOREA", "SEOUL"): "http://127.0.0.1:5006",
    ("SOUTH KOREA", ""): "http://127.0.0.1:5006",
}

INPUT_STOPS: list[dict[str, Any]] = []
ADDRESSES: list[str] = []
BUS_TYPE_CONFIGS = [
    {"name": "Large Bus", "capacity": 42, "max_count": 20},
    {"name": "Mid Bus", "capacity": 35, "max_count": 15},
    {"name": "Small Bus", "capacity": 19, "max_count": 10},
]
EXPRESS_THRESHOLD_KM = 15.0
RESERVED_EXPRESS_BUSES = 4
EXPRESS_SKIP_INNER_KM = 8.0
MAX_ROUTE_DURATION_SECONDS = 90 * 60
ANNOTATION_ROUTE_DURATION_SECONDS = 90 * 60
ROUTE_DURATION_GRACE_SECONDS = 10 * 60
STOP_SERVICE_SECONDS = 60
MAX_STOPS_PER_ROUTE = int(os.environ.get("BRP_MAX_STOPS_PER_ROUTE", "10") or 10)
COMFORT_LOAD_FACTOR = float(os.environ.get("BRP_COMFORT_LOAD_FACTOR", "0.85") or 0.85)
DEMAND_SPLIT_TARGET_LOAD_RATIO = float(os.environ.get("BRP_DEMAND_SPLIT_TARGET_LOAD_RATIO", "0.70") or 0.70)
DEMAND_SPLIT_MIN_BATCH_SIZE = int(os.environ.get("BRP_DEMAND_SPLIT_MIN_BATCH_SIZE", "8") or 8)
DEMAND_SPLIT_MAX_EXTRA_BATCHES = int(os.environ.get("BRP_DEMAND_SPLIT_MAX_EXTRA_BATCHES", "2") or 2)
SUBWAY_SEARCH_RADIUS_M = 1500
MAX_SUBWAY_WALK_DISTANCE_M = 800
NEARBY_CLUSTER_RADIUS_M = 500
TRAFFIC_PROFILE_NAME = "Off-Peak"
TRAFFIC_TIME_MULTIPLIER = 1.0
TRAFFIC_PROFILE_CONTEXT = "Global default"
SERVICE_DIRECTION = "From School"
MATRIX_NEAREST_NEIGHBORS = 10
MATRIX_MAX_CANDIDATE_DISTANCE_KM = 15.0
OPERATING_COST_PER_KM = 0.0
REVENUE_RULES = [
    {"min_km": 0.0, "max_km": 10.0, "fee_per_person": 0.0},
    {"min_km": 10.0, "max_km": 20.0, "fee_per_person": 0.0},
    {"min_km": 20.0, "max_km": None, "fee_per_person": 0.0},
]
OUTPUT_HTML = str(BASE_DIR / "outputs" / "school_bus_routes.html")
SUBWAY_OUTPUT_HTML = str(BASE_DIR / "outputs" / "school_bus_routes_subway_aggregated.html")
NEARBY_OUTPUT_HTML = str(BASE_DIR / "outputs" / "school_bus_routes_nearby_aggregated.html")

CURRENT_CURRENCY_CODE = "USD"
LAST_RUN_RESULTS: dict[str, Any] = {}

HUGE_TIME_SECONDS = 6 * 3600
HUGE_DISTANCE_METERS = 300_000
SEED_SECONDS_PER_KM = 150
VEHICLE_FIXED_COST = {"Large Bus": 0, "Mid Bus": 1200, "Small Bus": 2600}
# Soft load targets are intentionally moderate: roughly 50% occupancy is acceptable
# before we start discouraging underfilled buses.
MIN_LOAD_TARGET = {"Large Bus": 21, "Mid Bus": 18, "Small Bus": 10}
MIN_LOAD_PENALTY = {"Large Bus": 140, "Mid Bus": 100, "Small Bus": 40}
ROUTE_DURATION_SOFT_PENALTY_PER_SECOND = 80


class RateLimiter:
    def __init__(self, name: str, max_qps: float) -> None:
        self.shared_limiter = CrossProcessRateLimiter(name, max_qps)

    def wait(self) -> None:
        self.shared_limiter.wait()


AMAP_GEOCODE_LIMITER = RateLimiter("amap-geocode", AMAP_GEOCODE_MAX_QPS)
AMAP_PLACES_LIMITER = RateLimiter("amap-places", AMAP_PLACES_MAX_QPS)
AMAP_ROUTING_LIMITER = RateLimiter("amap-routing", AMAP_ROUTING_MAX_QPS)
AMAP_MATRIX_LIMITER = RateLimiter("amap-matrix", AMAP_MATRIX_MAX_QPS)
CACHE_FILE_LOCKS: dict[Path, threading.Lock] = {}


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def apply_traffic_time_multiplier(duration_s: int | float) -> int:
    adjusted = int(round(float(duration_s) * max(0.1, float(TRAFFIC_TIME_MULTIPLIER))))
    return max(1, adjusted)


def traffic_buffer_factor() -> float:
    return max(0.1, float(TRAFFIC_TIME_MULTIPLIER))


def stop_rider_label(point: dict[str, Any]) -> str:
    if bool(point.get("is_depot")):
        return "School"
    passenger_count = int(point.get("passenger_count", 0) or 0)
    rider_action = "Boarding" if is_to_school_direction() else "Drop-off"
    if int(point.get("demand_batch_count", 0) or 0) > 1:
        batch_index = int(point.get("demand_batch_index", 0) or 0)
        batch_count = int(point.get("demand_batch_count", 0) or 0)
        total = int(point.get("original_passenger_count", passenger_count) or passenger_count)
        return f"{rider_action}: {passenger_count} of {total}, batch {batch_index}/{batch_count}"
    return f"{rider_action}: {passenger_count}"


def stop_display_text(point: dict[str, Any]) -> str:
    address = str(point.get("address", "")).strip()
    rider_label = stop_rider_label(point)
    return f"{address} ({rider_label})" if address else rider_label


def load_json_cache(path: Path) -> dict[str, Any]:
    ensure_cache_dir()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json_cache(path: Path, payload: dict[str, Any]) -> None:
    ensure_cache_dir()
    lock = CACHE_FILE_LOCKS.setdefault(path, threading.Lock())
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with lock:
        temp_path.write_text(body, encoding="utf-8")
        temp_path.replace(path)


GEOCODE_CACHE = load_json_cache(GEOCODE_CACHE_PATH)
SUBWAY_CACHE = load_json_cache(SUBWAY_CACHE_PATH)


def log(message: str) -> None:
    print(message, flush=True)


def determine_currency_code(input_records: list[dict[str, Any]]) -> str:
    countries = " ".join(str(item.get("country", "")).strip().lower() for item in input_records)
    if any(token in countries for token in ("korea", "south korea", "대한민국", "한국")):
        return "KRW"
    if any(token in countries for token in ("china", "中国", "中华人民共和国")):
        return "CNY"
    return "USD"


def _normalize_osrm_key_part(value: str) -> str:
    normalized = []
    for char in value.strip().upper():
        normalized.append(char if char.isalnum() else "_")
    return "".join(normalized).strip("_")


def _country_aliases(country: str) -> list[str]:
    normalized = country.strip().lower()
    aliases = [country]
    if normalized in {"south korea", "republic of korea", "korea", "대한민국", "한국", "남한"}:
        aliases.extend(["South Korea", "Korea"])
    if normalized in {"china", "中国", "中华人民共和国"}:
        aliases.extend(["China"])
    deduped: list[str] = []
    for item in aliases:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _canonical_country(country: str) -> str:
    normalized = country.strip().lower()
    if normalized in {"south korea", "republic of korea", "korea", "대한민국", "한국", "남한"}:
        return "SOUTH KOREA"
    if normalized in {"china", "中国", "中华人民共和国"}:
        return "CHINA"
    return country.strip().upper()


def is_china_country(country: str) -> bool:
    return country.strip().lower() in {"china", "中国", "中华人民共和国"}


CHINA_CITY_CONFIGS: dict[str, dict[str, Any]] = {
    "shanghai": {
        "canonical": "SHANGHAI",
        "amap_city": "310000",
        "aliases": ["shanghai", "shanghai city", "上海", "上海市"],
        "adcode_prefixes": ["31"],
        "bbox": (31.90, 122.25, 30.65, 120.85),
    },
    "beijing": {
        "canonical": "BEIJING",
        "amap_city": "110000",
        "aliases": ["beijing", "beijing city", "北京", "北京市"],
        "adcode_prefixes": ["11"],
        "bbox": (41.10, 117.60, 39.40, 115.40),
    },
    "suzhou": {
        "canonical": "SUZHOU",
        "amap_city": "320500",
        "aliases": ["suzhou", "suzhou city", "苏州", "苏州市"],
        "adcode_prefixes": ["3205"],
        "bbox": (32.15, 121.35, 30.75, 119.85),
    },
    "xian": {
        "canonical": "XIAN",
        "amap_city": "610100",
        "aliases": ["xian", "xi'an", "xi an", "西安", "西安市"],
        "adcode_prefixes": ["6101"],
        "bbox": (34.85, 109.85, 33.40, 107.50),
    },
}


CHINA_CITY_ALIAS_TO_KEY = {
    alias.lower(): city_key
    for city_key, config in CHINA_CITY_CONFIGS.items()
    for alias in config["aliases"]
}


def _normalize_china_city_key(city: str) -> str:
    normalized = " ".join(city.strip().lower().replace("’", "'").split())
    return CHINA_CITY_ALIAS_TO_KEY.get(normalized, normalized)


def _china_city_config(city: str) -> dict[str, Any] | None:
    return CHINA_CITY_CONFIGS.get(_normalize_china_city_key(city))


def _amap_city_param(country: str, city: str) -> str:
    if not is_china_country(country):
        return city.strip()
    config = _china_city_config(city)
    if config:
        return str(config["amap_city"])
    return city.strip()


def _canonical_city(city: str) -> str:
    normalized = city.strip().lower()
    china_config = _china_city_config(city)
    if china_config:
        return str(china_config["canonical"])
    aliases = {
        "seoul": "SEOUL",
        "서울": "SEOUL",
        "seongnam": "SEOUL",
        "seongnam-si": "SEOUL",
        "seongnam si": "SEOUL",
        "성남": "SEOUL",
        "성남시": "SEOUL",
    }
    return aliases.get(normalized, city.strip().upper())


def _infer_location_from_address(address: str) -> tuple[str, str] | None:
    normalized = address.strip().lower()
    patterns = [
        (tuple(CHINA_CITY_CONFIGS["shanghai"]["aliases"]), ("CHINA", "SHANGHAI")),
        (tuple(CHINA_CITY_CONFIGS["beijing"]["aliases"]), ("CHINA", "BEIJING")),
        (tuple(CHINA_CITY_CONFIGS["suzhou"]["aliases"]), ("CHINA", "SUZHOU")),
        (tuple(CHINA_CITY_CONFIGS["xian"]["aliases"]), ("CHINA", "XIAN")),
        (("서울", "seoul"), ("SOUTH KOREA", "SEOUL")),
        (("성남", "seongnam"), ("SOUTH KOREA", "SEOUL")),
    ]
    for tokens, location in patterns:
        if any(token in normalized for token in tokens):
            return location
    return None


def _resolve_point_location(point: dict[str, Any]) -> tuple[str, str] | None:
    country = str(point.get("country", "")).strip()
    city = str(point.get("city", "")).strip()
    if country and city:
        return _canonical_country(country), _canonical_city(city)
    inferred = _infer_location_from_address(str(point.get("address", "")))
    if inferred is not None:
        return inferred
    if country:
        return _canonical_country(country), ""
    return None


def resolve_osrm_base_url(points: list[dict[str, Any]]) -> str:
    non_depot_points = [point for point in points if not point.get("is_depot")]
    if not non_depot_points:
        return OSRM_BASE_URL

    resolved_locations = {
        location
        for point in non_depot_points
        for location in [_resolve_point_location(point)]
        if location is not None
    }
    if len(resolved_locations) > 1:
        return OSRM_BASE_URL

    if resolved_locations:
        country, city = next(iter(resolved_locations))
    else:
        country = ""
        city = ""

    candidate_env_keys: list[str] = []
    if country and city:
        for country_alias in _country_aliases(country):
            country_key = _normalize_osrm_key_part(country_alias)
            city_key = _normalize_osrm_key_part(city)
            candidate_env_keys.append(f"OSRM_BASE_URL_{country_key}_{city_key}")
    if country:
        for country_alias in _country_aliases(country):
            country_key = _normalize_osrm_key_part(country_alias)
            candidate_env_keys.append(f"OSRM_BASE_URL_{country_key}")
    if city:
        candidate_env_keys.append(f"OSRM_BASE_URL_{_normalize_osrm_key_part(city)}")

    for env_key in candidate_env_keys:
        env_value = os.environ.get(env_key, "").strip()
        if env_value:
            return env_value

    if country and city and OSRM_USE_BUILTIN_DEFAULTS:
        builtin_url = OSRM_LOCATION_DEFAULTS.get((_canonical_country(country), _canonical_city(city)))
        if builtin_url:
            return builtin_url
    if OSRM_BASE_URL:
        return OSRM_BASE_URL
    location_label = "/".join(part for part in (country, city) if part) or "unknown location"
    raise RuntimeError(
        f"No OSRM endpoint is configured for {location_label}. "
        "Set OSRM_BASE_URL_<COUNTRY>_<CITY>, OSRM_BASE_URL_<COUNTRY>, or OSRM_BASE_URL "
        "in this server's local environment."
    )


def format_currency(amount: float) -> str:
    return f"{CURRENT_CURRENCY_CODE} {amount:.2f}"


def haversine_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def out_of_china(lat: float, lng: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    if out_of_china(lat, lng):
        return lat, lng
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = math.radians(lat)
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrt_magic * math.cos(radlat) * math.pi)
    mg_lat = lat + dlat
    mg_lng = lng + dlng
    return lat * 2 - mg_lat, lng * 2 - mg_lng


def decode_polyline(polyline: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for chunk in polyline.split(";"):
        if not chunk.strip():
            continue
        lng_str, lat_str = chunk.split(",")
        points.append((float(lat_str), float(lng_str)))
    return points


def point_osrm_lat_lng(point: dict[str, Any]) -> tuple[float, float]:
    lat = point.get("plot_lat", point.get("lat"))
    lng = point.get("plot_lng", point.get("lng"))
    if lat is None or lng is None:
        raise RuntimeError(f"Point has no usable OSRM coordinates: {point.get('address', 'UNKNOWN')}")
    return float(lat), float(lng)


def amap_request_json(endpoint: str, params: dict[str, Any], limiter: RateLimiter) -> dict[str, Any]:
    limiter.wait()
    response = requests.get(
        f"https://restapi.amap.com{endpoint}",
        params={**params, "key": AMAP_KEY},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status")) != "1":
        info = payload.get("info", "UNKNOWN_ERROR")
        infocode = payload.get("infocode", "")
        raise RuntimeError(f"AMap request failed: {endpoint} -> {info} ({infocode})")
    return payload


def osrm_request_json(service: str, coordinates: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{OSRM_BASE_URL}/{service}/v1/driving/{coordinates}",
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM request failed: {service} -> {payload}")
    return payload


def geocode_cache_key(country: str, city: str, address: str) -> str:
    return f"{country.strip()}|{city.strip()}|{address.strip()}"


def _is_within_china_city_bbox(city: str, lat: float, lng: float) -> bool:
    config = _china_city_config(city)
    if config is None:
        return True
    north, east, south, west = config["bbox"]
    return south <= lat <= north and west <= lng <= east


def is_plausible_china_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
    adcode: str = "",
) -> bool:
    if not is_china_country(country):
        return True
    config = _china_city_config(city)
    if config is None:
        return True

    adcode_text = str(adcode).strip()
    if adcode_text:
        return any(adcode_text.startswith(prefix) for prefix in config["adcode_prefixes"])

    address_text = formatted_address.strip().lower()
    target_city_key = _normalize_china_city_key(city)
    for city_key, candidate_config in CHINA_CITY_CONFIGS.items():
        if city_key == target_city_key:
            continue
        if any(alias.lower() in address_text for alias in candidate_config["aliases"]):
            return False

    if any(alias.lower() in address_text for alias in config["aliases"]):
        return True

    return _is_within_china_city_bbox(city, lat, lng)


def is_plausible_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
    adcode: str = "",
) -> bool:
    if is_china_country(country):
        return is_plausible_china_geocode_result(country, city, lat, lng, formatted_address, adcode)
    return True


def amap_geocode_query(country: str, city: str, address: str) -> dict[str, Any]:
    amap_city = _amap_city_param(country, city)
    queries = [address.strip()]
    if city.strip():
        queries.append(f"{city.strip()} {address.strip()}")
    if country.strip() and city.strip():
        queries.append(f"{country.strip()} {city.strip()} {address.strip()}")
    if country.strip():
        queries.append(f"{country.strip()} {address.strip()}")

    last_error = None
    for query in queries:
        try:
            params = {"address": query}
            if amap_city:
                params["city"] = amap_city
            payload = amap_request_json("/v3/geocode/geo", params, AMAP_GEOCODE_LIMITER)
            geocodes = payload.get("geocodes") or []
            for candidate in geocodes:
                lng_str, lat_str = str(candidate["location"]).split(",")
                lat = float(lat_str)
                lng = float(lng_str)
                formatted_address = str(candidate.get("formatted_address") or address.strip()).strip()
                adcode = str(candidate.get("adcode", "") or "").strip()
                if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, adcode):
                    last_error = RuntimeError(
                        f"AMap geocode returned result outside {city.strip()}: {formatted_address}"
                    )
                    continue
                plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
                return {
                    "provider": "amap",
                    "address": address.strip(),
                    "city": city.strip(),
                    "country": country.strip(),
                    "lat": lat,
                    "lng": lng,
                    "plot_lat": plot_lat,
                    "plot_lng": plot_lng,
                    "formatted_address": formatted_address,
                    "adcode": adcode,
                }
        except Exception as exc:
            last_error = exc

    try:
        payload = amap_request_json(
            "/v3/place/text",
            {
                "keywords": address.strip(),
                "city": amap_city,
                "citylimit": "true" if amap_city else "false",
                "offset": 10,
                "page": 1,
            },
            AMAP_PLACES_LIMITER,
        )
        pois = payload.get("pois") or []
        for candidate in pois:
            lng_str, lat_str = str(candidate["location"]).split(",")
            lat = float(lat_str)
            lng = float(lng_str)
            formatted_parts = [
                str(candidate.get("pname", "") or "").strip(),
                str(candidate.get("cityname", "") or "").strip(),
                str(candidate.get("adname", "") or "").strip(),
                str(candidate.get("address", "") or candidate.get("name", "") or address.strip()).strip(),
            ]
            formatted_address = "".join(part for index, part in enumerate(formatted_parts) if part and part not in formatted_parts[:index])
            adcode = str(candidate.get("adcode", "") or "").strip()
            if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, adcode):
                last_error = RuntimeError(f"AMap place search returned result outside {city.strip()}: {formatted_address}")
                continue
            plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
            return {
                "provider": "amap",
                "address": address.strip(),
                "city": city.strip(),
                "country": country.strip(),
                "lat": lat,
                "lng": lng,
                "plot_lat": plot_lat,
                "plot_lng": plot_lng,
                "formatted_address": formatted_address,
                "adcode": adcode,
            }
    except Exception as exc:
        last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"AMap geocode failed for {address.strip()}")


def geocode_records(input_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    points: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    changed = False
    for index, item in enumerate(input_records):
        country = str(item.get("country", "")).strip()
        city = str(item.get("city", "")).strip()
        address = str(item.get("address", "")).strip()
        passenger_count = int(item.get("passenger_count", 0 if index == 0 else 1))
        cache_key = geocode_cache_key(country, city, address)
        cached = GEOCODE_CACHE.get(cache_key)
        point = None
        if cached:
            try:
                cached_lat = float(cached.get("lat", 0.0) or 0.0)
                cached_lng = float(cached.get("lng", 0.0) or 0.0)
                cached_formatted_address = str(cached.get("formatted_address", "") or address).strip()
                cached_adcode = str(cached.get("adcode", "") or "").strip()
                if is_plausible_geocode_result(
                    country,
                    city,
                    cached_lat,
                    cached_lng,
                    cached_formatted_address,
                    cached_adcode,
                ):
                    point = dict(cached)
            except Exception:
                point = None
        if point is None:
            try:
                point = amap_geocode_query(country, city, address)
                GEOCODE_CACHE[cache_key] = point
                changed = True
            except Exception as exc:
                log(f"[WARN] geocode failed: {address} -> {exc}")
                warnings.append(
                    {
                        "address": address,
                        "warning": "This address could not be resolved to coordinates by the map API.",
                        "suggestion": (
                            "Use a more complete postal-style address, including district, road name, "
                            "building number, and city. Avoid abbreviations, landmarks only, or informal descriptions."
                        ),
                    }
                )
                continue
        point = dict(point)
        point["node_id"] = len(points)
        point["passenger_count"] = passenger_count
        point["original_members"] = [address]
        point["display_address"] = address
        point["is_depot"] = len(points) == 0
        points.append(point)
    if changed:
        save_json_cache(GEOCODE_CACHE_PATH, GEOCODE_CACHE)
    if not points:
        raise RuntimeError("No valid stops were accepted as valid input stops.")
    return points, warnings


def subway_cache_key(point: dict[str, Any]) -> str:
    return f"{point['lat']:.6f},{point['lng']:.6f}|{SUBWAY_SEARCH_RADIUS_M}|{MAX_SUBWAY_WALK_DISTANCE_M}"


def find_nearby_subway_station(point: dict[str, Any]) -> dict[str, Any] | None:
    cache_key = subway_cache_key(point)
    if cache_key in SUBWAY_CACHE:
        return SUBWAY_CACHE[cache_key]
    try:
        payload = amap_request_json(
            "/v3/place/around",
            {
                "location": f"{point['lng']},{point['lat']}",
                "keywords": "地铁站",
                "radius": SUBWAY_SEARCH_RADIUS_M,
                "sortrule": "distance",
                "offset": 10,
                "page": 1,
            },
            AMAP_PLACES_LIMITER,
        )
        for poi in payload.get("pois") or []:
            distance = int(float(poi.get("distance", 0) or 0))
            if distance > MAX_SUBWAY_WALK_DISTANCE_M:
                continue
            lng_str, lat_str = str(poi["location"]).split(",")
            lat = float(lat_str)
            lng = float(lng_str)
            plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
            result = {
                "name": poi.get("name") or "Nearby Subway Station",
                "station_id": poi.get("id") or poi.get("name") or f"{lat:.6f},{lng:.6f}",
                "lat": lat,
                "lng": lng,
                "plot_lat": plot_lat,
                "plot_lng": plot_lng,
                "walking_distance_m": distance,
            }
            SUBWAY_CACHE[cache_key] = result
            save_json_cache(SUBWAY_CACHE_PATH, SUBWAY_CACHE)
            return result
    except Exception as exc:
        log(f"[WARN] subway search failed: {point['address']} -> {exc}")

    SUBWAY_CACHE[cache_key] = None
    save_json_cache(SUBWAY_CACHE_PATH, SUBWAY_CACHE)
    return None


def build_subway_aggregated_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    depot = dict(points[0])
    depot["node_id"] = 0
    aggregated: list[dict[str, Any]] = [depot]
    groups: dict[str, dict[str, Any]] = {}
    for point in points[1:]:
        station = find_nearby_subway_station(point)
        if station is None:
            standalone = dict(point)
            standalone["node_id"] = len(aggregated)
            aggregated.append(standalone)
            continue
        key = str(station["station_id"])
        if key not in groups:
            groups[key] = {
                "provider": "amap",
                "address": station["name"],
                "display_address": station["name"],
                "country": point.get("country", ""),
                "city": point.get("city", ""),
                "lat": station["lat"],
                "lng": station["lng"],
                "plot_lat": station["plot_lat"],
                "plot_lng": station["plot_lng"],
                "passenger_count": 0,
                "original_members": [],
                "is_depot": False,
                "aggregated_type": "subway",
                "covered_stop_count": 0,
            }
        group = groups[key]
        group["passenger_count"] += int(point.get("passenger_count", 1))
        group["original_members"].extend(point.get("original_members", [point["address"]]))
        group["covered_stop_count"] += 1

    for key in sorted(groups):
        group = groups[key]
        group["node_id"] = len(aggregated)
        aggregated.append(group)
    log(f"[INFO] Subway aggregation reduced stops from {max(0, len(points) - 1)} to {max(0, len(aggregated) - 1)}.")
    return aggregated


def build_nearby_aggregated_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    depot = dict(points[0])
    depot["node_id"] = 0
    aggregated: list[dict[str, Any]] = [depot]
    remaining = points[1:]
    used = [False] * len(remaining)
    radius_km = NEARBY_CLUSTER_RADIUS_M / 1000.0
    for i, point in enumerate(remaining):
        if used[i]:
            continue
        cluster = [point]
        used[i] = True
        for j in range(i + 1, len(remaining)):
            if used[j]:
                continue
            candidate = remaining[j]
            distance_km = haversine_distance_km(point["lat"], point["lng"], candidate["lat"], candidate["lng"])
            if distance_km <= radius_km:
                cluster.append(candidate)
                used[j] = True
        if len(cluster) == 1:
            standalone = dict(cluster[0])
            standalone["node_id"] = len(aggregated)
            aggregated.append(standalone)
            continue
        avg_lat = sum(item["lat"] for item in cluster) / len(cluster)
        avg_lng = sum(item["lng"] for item in cluster) / len(cluster)
        representative = min(
            cluster,
            key=lambda item: haversine_distance_km(avg_lat, avg_lng, item["lat"], item["lng"]),
        )
        merged = dict(representative)
        merged["node_id"] = len(aggregated)
        merged["passenger_count"] = sum(int(item.get("passenger_count", 1)) for item in cluster)
        merged["original_members"] = [
            member
            for item in cluster
            for member in item.get("original_members", [item["address"]])
        ]
        merged["covered_stop_count"] = len(cluster)
        merged["aggregated_type"] = "nearby"
        aggregated.append(merged)
    log(f"[INFO] Nearby-address aggregation reduced stops from {max(0, len(points) - 1)} to {max(0, len(aggregated) - 1)}.")
    return aggregated


def seed_edge_metrics(points: list[dict[str, Any]]) -> tuple[list[list[int]], list[list[int]]]:
    n = len(points)
    time_matrix = [[0] * n for _ in range(n)]
    distance_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            distance_km = haversine_distance_km(points[i]["lat"], points[i]["lng"], points[j]["lat"], points[j]["lng"])
            distance_m = max(100, int(distance_km * 1000))
            time_s = apply_traffic_time_multiplier(max(120, int(distance_km * SEED_SECONDS_PER_KM)))
            if j != 0:
                time_s += STOP_SERVICE_SECONDS
            time_matrix[i][j] = time_s
            distance_matrix[i][j] = distance_m
    return time_matrix, distance_matrix


def top_k_neighbors(points: list[dict[str, Any]], k: int, radius_km: float) -> dict[int, set[int]]:
    neighbors: dict[int, set[int]] = {i: set() for i in range(len(points))}
    for i in range(len(points)):
        if i == 0:
            neighbors[i] = set(range(1, len(points)))
            continue
        ranked: list[tuple[float, int]] = []
        for j in range(1, len(points)):
            if i == j:
                continue
            distance_km = haversine_distance_km(points[i]["lat"], points[i]["lng"], points[j]["lat"], points[j]["lng"])
            if distance_km <= radius_km:
                ranked.append((distance_km, j))
        ranked.sort(key=lambda item: item[0])
        neighbors[i].add(0)
        for _, node in ranked[:k]:
            neighbors[i].add(node)
    return neighbors


def build_vehicle_fleet() -> list[dict[str, Any]]:
    fleet: list[dict[str, Any]] = []
    for bus_type in BUS_TYPE_CONFIGS:
        for _ in range(int(bus_type.get("max_count", 0))):
            capacity = int(bus_type["capacity"])
            fleet.append(
                {
                    "name": str(bus_type["name"]),
                    "capacity": capacity,
                    "comfort_capacity": comfort_capacity_for_vehicle(capacity),
                }
            )
    return fleet


def _minimum_vehicle_count_for_demand(total_demand: int, fleet: list[dict[str, Any]]) -> int:
    if total_demand <= 0 or not fleet:
        return 0
    sorted_capacities = sorted((solver_capacity_for_vehicle(item) for item in fleet), reverse=True)
    running = 0
    for count, capacity in enumerate(sorted_capacities, start=1):
        running += capacity
        if running >= total_demand:
            return count
    return len(sorted_capacities)


def comfort_capacity_for_vehicle(capacity: int) -> int:
    if capacity <= 0:
        return 0
    bounded_factor = min(1.0, max(0.1, float(COMFORT_LOAD_FACTOR)))
    return max(1, min(capacity, int(math.floor(capacity * bounded_factor))))


def solver_capacity_for_vehicle(vehicle: dict[str, Any]) -> int:
    capacity = int(vehicle.get("capacity", 0) or 0)
    comfort_capacity = int(vehicle.get("comfort_capacity", 0) or 0)
    if comfort_capacity <= 0:
        comfort_capacity = comfort_capacity_for_vehicle(capacity)
    return min(capacity, comfort_capacity)


def route_stop_limit() -> int:
    return max(1, int(MAX_STOPS_PER_ROUTE))


def balanced_demand_batch_sizes(passenger_count: int, max_batch_size: int) -> list[int]:
    passenger_count = max(0, int(passenger_count or 0))
    max_batch_size = max(1, int(max_batch_size or 1))
    if passenger_count <= max_batch_size:
        return [passenger_count] if passenger_count else []

    minimum_batch_count = int(math.ceil(passenger_count / max_batch_size))
    target_load_ratio = min(1.0, max(0.1, float(DEMAND_SPLIT_TARGET_LOAD_RATIO)))
    configured_min_size = max(1, int(DEMAND_SPLIT_MIN_BATCH_SIZE or 1))
    target_batch_size = min(
        max_batch_size,
        max(configured_min_size, int(math.ceil(max_batch_size * target_load_ratio))),
    )
    target_batch_count = int(math.ceil(passenger_count / target_batch_size))
    max_extra_batches = max(0, int(DEMAND_SPLIT_MAX_EXTRA_BATCHES or 0))
    max_batch_count = minimum_batch_count + max_extra_batches
    batch_count = max(minimum_batch_count, min(target_batch_count, max_batch_count))
    batch_count = min(batch_count, passenger_count)

    base_size = passenger_count // batch_count
    remainder = passenger_count % batch_count
    return [base_size + (1 if index < remainder else 0) for index in range(batch_count)]


def split_oversized_demand_points(points: list[dict[str, Any]], max_batch_size: int) -> list[dict[str, Any]]:
    if max_batch_size <= 0 or len(points) <= 1:
        return points

    expanded: list[dict[str, Any]] = []
    for point in points:
        if bool(point.get("is_depot")):
            cloned_depot = dict(point)
            cloned_depot["node_id"] = len(expanded)
            expanded.append(cloned_depot)
            continue

        passenger_count = int(point.get("passenger_count", 0) or 0)
        batch_sizes = balanced_demand_batch_sizes(passenger_count, max_batch_size)
        if len(batch_sizes) <= 1:
            cloned = dict(point)
            cloned["node_id"] = len(expanded)
            expanded.append(cloned)
            continue

        batch_count = len(batch_sizes)
        for batch_index, batch_size in enumerate(batch_sizes, start=1):
            cloned = dict(point)
            cloned["node_id"] = len(expanded)
            cloned["passenger_count"] = batch_size
            cloned["original_passenger_count"] = passenger_count
            cloned["demand_batch_index"] = batch_index
            cloned["demand_batch_count"] = batch_count
            cloned["demand_batch_key"] = str(point.get("address", "")).strip() or f"node-{point.get('node_id', len(expanded))}"
            cloned["demand_batch_strategy"] = "balanced_target_load"
            expanded.append(cloned)
    return expanded

def trim_fleet_for_demand(points: list[dict[str, Any]], fleet: list[dict[str, Any]], extra_buffer: int = 2) -> list[dict[str, Any]]:
    if not points or not fleet:
        return []

    total_demand = sum(int(point.get("passenger_count", 0)) for point in points[1:])
    largest_stop_demand = max((int(point.get("passenger_count", 0)) for point in points[1:]), default=0)
    if total_demand <= 0:
        return fleet[:1]

    sorted_fleet = sort_regular_preference(fleet)
    stop_based_count = math.ceil(max(0, len(points) - 1) / route_stop_limit())
    min_vehicle_count = max(
        _minimum_vehicle_count_for_demand(total_demand, sorted_fleet),
        stop_based_count,
    )
    operational_buffer = max(extra_buffer, int(math.ceil(stop_based_count * 0.75)))
    target_vehicle_count = min(len(sorted_fleet), max(1, min_vehicle_count + operational_buffer))

    trimmed = sorted_fleet[:target_vehicle_count]
    if largest_stop_demand > 0 and all(solver_capacity_for_vehicle(item) < largest_stop_demand for item in trimmed):
        for candidate in sorted_fleet[target_vehicle_count:]:
            trimmed.append(candidate)
            if solver_capacity_for_vehicle(candidate) >= largest_stop_demand:
                break
    return trimmed


def build_trivial_routes(
    points: list[dict[str, Any]],
    time_matrix: list[list[int]],
    distance_matrix: list[list[int]],
    fleet: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(points) <= 1 or not fleet:
        return []

    demand = int(points[1].get("passenger_count", 0))
    chosen_vehicle = next((item for item in sort_regular_preference(fleet) if solver_capacity_for_vehicle(item) >= demand), None)
    if chosen_vehicle is None:
        raise RuntimeError("No feasible vehicle is large enough for the requested stop demand.")

    route = {
            "vehicle_id": 1,
            "bus_type_name": chosen_vehicle["name"],
            "bus_capacity": chosen_vehicle["capacity"],
            "comfort_capacity": solver_capacity_for_vehicle(chosen_vehicle),
            "max_stops": route_stop_limit(),
            "nodes": [0, 1],
            "time_s": int(time_matrix[0][1]),
            "distance_m": int(distance_matrix[0][1]),
            "load": demand,
        }
    return [reverse_route_for_to_school(route)]


def sort_express_preference(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        fleet,
        key=lambda item: (
            int(item.get("capacity", 0) or 0),
            str(item.get("name", "")).lower(),
        ),
    )


def sort_regular_preference(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        fleet,
        key=lambda item: (
            -int(item.get("capacity", 0) or 0),
            str(item.get("name", "")).lower(),
        ),
    )


def is_to_school_direction() -> bool:
    return str(SERVICE_DIRECTION).strip() == "To School"


def transpose_matrix(matrix: list[list[int]]) -> list[list[int]]:
    return [list(row) for row in zip(*matrix)] if matrix else []


def reverse_route_for_to_school(route: dict[str, Any]) -> dict[str, Any]:
    if not is_to_school_direction():
        return route
    cloned = dict(route)
    cloned["nodes"] = list(reversed(list(route.get("nodes") or [])))
    return cloned


def compute_depot_distances(points: list[dict[str, Any]]) -> dict[int, float]:
    if not points:
        return {}
    depot = points[0]
    return {
        idx: haversine_distance_km(depot["lat"], depot["lng"], point["lat"], point["lng"])
        for idx, point in enumerate(points)
    }


def subset_matrix(matrix: list[list[int]], nodes: list[int]) -> list[list[int]]:
    return [[matrix[i][j] for j in nodes] for i in nodes]


def remap_subset_routes(subset_routes: list[dict[str, Any]], subset_nodes: list[int]) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for route in subset_routes:
        cloned = dict(route)
        cloned["nodes"] = [subset_nodes[node] for node in route["nodes"]]
        remapped.append(cloned)
    return remapped


def solve_routes_for_fleet(
    points: list[dict[str, Any]],
    time_matrix: list[list[int]],
    distance_matrix: list[list[int]],
    fleet: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not points:
        return []
    if not fleet:
        return []

    working_time_matrix = transpose_matrix(time_matrix) if is_to_school_direction() else time_matrix
    working_distance_matrix = transpose_matrix(distance_matrix) if is_to_school_direction() else distance_matrix

    largest_capacity = max(solver_capacity_for_vehicle(item) for item in fleet)
    oversized = [point["address"] for point in points[1:] if int(point.get("passenger_count", 1)) > largest_capacity]
    if oversized:
        raise RuntimeError(
            "One or more stops exceed the largest comfort capacity. "
            f"Current comfort load factor is {COMFORT_LOAD_FACTOR:.0%}; oversized stops above the largest comfort capacity: {', '.join(oversized[:10])}"
        )

    total_demand = sum(int(point.get("passenger_count", 0)) for point in points[1:])
    if total_demand > sum(solver_capacity_for_vehicle(item) for item in fleet):
        raise RuntimeError("No feasible fleet composition exists under the configured Large / Mid / Small bus max-count limits.")

    vehicle_count = len(fleet)
    manager = pywrapcp.RoutingIndexManager(len(points), vehicle_count, [0] * vehicle_count, [0] * vehicle_count)
    routing = pywrapcp.RoutingModel(manager)

    def transit_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        if to_node == 0:
            return 0
        return int(working_time_matrix[from_node][to_node])

    transit_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)
    target_route_duration_seconds = max(60, int(MAX_ROUTE_DURATION_SECONDS))
    soft_route_duration_seconds = target_route_duration_seconds + ROUTE_DURATION_GRACE_SECONDS
    hard_route_duration_seconds = max(
        soft_route_duration_seconds + 10 * 60,
        int(round(soft_route_duration_seconds * 1.20)),
    )
    routing.AddDimension(
        transit_index,
        0,
        hard_route_duration_seconds,
        True,
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    def demand_callback(index: int) -> int:
        node = manager.IndexToNode(index)
        return int(points[node].get("passenger_count", 0))

    demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_index,
        0,
        [solver_capacity_for_vehicle(item) for item in fleet],
        True,
        "Load",
    )
    load_dimension = routing.GetDimensionOrDie("Load")

    def stop_count_callback(from_index: int, to_index: int) -> int:
        del from_index
        to_node = manager.IndexToNode(to_index)
        return 0 if to_node == 0 else 1

    stop_count_index = routing.RegisterTransitCallback(stop_count_callback)
    routing.AddDimension(
        stop_count_index,
        0,
        route_stop_limit(),
        True,
        "Stops",
    )

    for vehicle_id, vehicle in enumerate(fleet):
        routing.SetFixedCostOfVehicle(VEHICLE_FIXED_COST.get(vehicle["name"], 0), vehicle_id)
        target = MIN_LOAD_TARGET.get(vehicle["name"], 0)
        penalty = MIN_LOAD_PENALTY.get(vehicle["name"], 0)
        if target > 0 and penalty > 0:
            load_dimension.SetCumulVarSoftLowerBound(routing.End(vehicle_id), target, penalty)
        time_dimension.SetCumulVarSoftUpperBound(
            routing.End(vehicle_id),
            soft_route_duration_seconds,
            ROUTE_DURATION_SOFT_PENALTY_PER_SECOND,
        )

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.seconds = 30
    solution = routing.SolveWithParameters(search)
    if solution is None:
        raise RuntimeError(
            "OR-Tools could not find a feasible routing solution. "
            f"Current constraints include the configured Large / Mid / Small bus capacities. "
            f"Last attempted feasible vehicle count: {vehicle_count}. Configured fleet maximum: {vehicle_count} vehicles. "
            "Stops unreachable from depot: 0. Stops that appear to require dedicated vehicles: 0 "
            f"out of {max(0, len(points) - 1)} non-depot stops."
        )

    routes: list[dict[str, Any]] = []
    for vehicle_id, vehicle in enumerate(fleet):
        index = routing.Start(vehicle_id)
        if solution.Value(routing.NextVar(index)) == routing.End(vehicle_id):
            continue
        nodes = [0]
        route_time_s = 0
        route_distance_m = 0
        while not routing.IsEnd(index):
            next_index = solution.Value(routing.NextVar(index))
            if routing.IsEnd(next_index):
                break
            from_node = manager.IndexToNode(index)
            to_node = manager.IndexToNode(next_index)
            nodes.append(to_node)
            route_time_s += int(working_time_matrix[from_node][to_node])
            route_distance_m += int(working_distance_matrix[from_node][to_node])
            index = next_index
        route_load = sum(int(points[node].get("passenger_count", 0) or 0) for node in nodes if node != 0)
        route_stop_count = len([node for node in nodes if node != 0])
        routes.append(
            reverse_route_for_to_school(
                {
                "vehicle_id": vehicle_id + 1,
                "bus_type_name": vehicle["name"],
                "bus_capacity": vehicle["capacity"],
                "comfort_capacity": solver_capacity_for_vehicle(vehicle),
                "max_stops": route_stop_limit(),
                "nodes": nodes,
                "time_s": route_time_s,
                "distance_m": route_distance_m,
                "load": route_load,
                "stop_count": route_stop_count,
                }
            )
        )
    served_nodes = {node for route in routes for node in route.get("nodes", []) if node != 0}
    expected_nodes = set(range(1, len(points)))
    missing_nodes = sorted(expected_nodes - served_nodes)
    if missing_nodes:
        missing_addresses = [str(points[node].get("address", f"node {node}")) for node in missing_nodes[:10]]
        raise RuntimeError(
            "Routing solution did not serve every stop. "
            f"Missing stops: {', '.join(missing_addresses)}"
        )
    routes.sort(key=lambda item: item["vehicle_id"])
    return routes


def solve_routes(points: list[dict[str, Any]], time_matrix: list[list[int]], distance_matrix: list[list[int]]) -> list[dict[str, Any]]:
    if not points:
        return []
    full_fleet = build_vehicle_fleet()
    if not full_fleet:
        raise RuntimeError("No feasible fleet composition exists under the configured Large / Mid / Small bus max-count limits.")

    # Small cases do not need the full VRP search; avoiding it keeps the backend responsive.
    if len(points) == 2:
        return build_trivial_routes(points, time_matrix, distance_matrix, full_fleet)

    depot_distances = compute_depot_distances(points)
    remote_nodes = [
        idx for idx in range(1, len(points))
        if depot_distances.get(idx, 0.0) >= EXPRESS_THRESHOLD_KM
    ]
    inner_nodes = [
        idx for idx in range(1, len(points))
        if depot_distances.get(idx, 0.0) < EXPRESS_THRESHOLD_KM
    ]

    express_fleet: list[dict[str, Any]] = []
    regular_fleet = trim_fleet_for_demand(points, full_fleet)
    if remote_nodes and RESERVED_EXPRESS_BUSES > 0:
        sorted_for_express = sort_express_preference(full_fleet)
        express_fleet = sorted_for_express[: min(RESERVED_EXPRESS_BUSES, len(sorted_for_express))]
        express_ids = {id(item) for item in express_fleet}
        regular_fleet = [item for item in full_fleet if id(item) not in express_ids]
        regular_fleet = trim_fleet_for_demand(points, regular_fleet)

    combined_routes: list[dict[str, Any]] = []

    if remote_nodes and express_fleet:
        eligible_remote_nodes = [
            idx for idx in remote_nodes
            if depot_distances.get(idx, 0.0) >= EXPRESS_SKIP_INNER_KM
        ] or remote_nodes
        try:
            subset_nodes = [0] + eligible_remote_nodes
            subset_points = [points[idx] for idx in subset_nodes]
            subset_time = subset_matrix(time_matrix, subset_nodes)
            subset_distance = subset_matrix(distance_matrix, subset_nodes)
            express_routes = solve_routes_for_fleet(subset_points, subset_time, subset_distance, express_fleet)
            combined_routes.extend(remap_subset_routes(express_routes, subset_nodes))
            served_remote_nodes = {
                node
                for route in combined_routes
                for node in route["nodes"]
                if node != 0
            }
            regular_nodes = [idx for idx in range(1, len(points)) if idx not in served_remote_nodes]
        except Exception as exc:
            log(f"[WARN] Express-route pool fallback to regular pool: {exc}")
            regular_nodes = list(range(1, len(points)))
    else:
        regular_nodes = list(range(1, len(points)))

    if regular_nodes:
        subset_nodes = [0] + regular_nodes
        subset_points = [points[idx] for idx in subset_nodes]
        subset_time = subset_matrix(time_matrix, subset_nodes)
        subset_distance = subset_matrix(distance_matrix, subset_nodes)
        regular_routes = solve_routes_for_fleet(subset_points, subset_time, subset_distance, regular_fleet or full_fleet)
        combined_routes.extend(remap_subset_routes(regular_routes, subset_nodes))

    combined_routes.sort(key=lambda item: (item["bus_type_name"], item["vehicle_id"]))
    for route_id, route in enumerate(combined_routes, start=1):
        route["vehicle_id"] = route_id
    return combined_routes


def osrm_distance_matrix_batch(origin_points: list[dict[str, Any]], destination_point: dict[str, Any]) -> list[tuple[int, int]]:
    destination_lat, destination_lng = point_osrm_lat_lng(destination_point)
    coordinates = []
    for point in origin_points:
        origin_lat, origin_lng = point_osrm_lat_lng(point)
        coordinates.append(f"{origin_lng},{origin_lat}")
    coordinates.append(f"{destination_lng},{destination_lat}")
    payload = osrm_request_json(
        "table",
        ";".join(coordinates),
        {
            "annotations": "distance,duration",
            "sources": ";".join(str(index) for index in range(len(origin_points))),
            "destinations": str(len(origin_points)),
        },
    )
    durations = payload.get("durations") or []
    distances = payload.get("distances") or []
    if len(durations) != len(origin_points) or len(distances) != len(origin_points):
        raise RuntimeError("OSRM table returned an unexpected result length.")
    parsed = []
    for distance_row, duration_row in zip(distances, durations):
        distance_m = distance_row[0] if distance_row else None
        duration_s = duration_row[0] if duration_row else None
        parsed.append(
            (
                int(round(float(distance_m))) if distance_m is not None else HUGE_DISTANCE_METERS,
                apply_traffic_time_multiplier(float(duration_s)) if duration_s is not None else HUGE_TIME_SECONDS,
            )
        )
    return parsed


def build_osrm_full_matrix(points: list[dict[str, Any]]) -> tuple[list[list[int]], list[list[int]]]:
    if not points:
        return [], []

    coordinates = []
    for point in points:
        point_lat, point_lng = point_osrm_lat_lng(point)
        coordinates.append(f"{point_lng},{point_lat}")

    payload = osrm_request_json(
        "table",
        ";".join(coordinates),
        {
            "annotations": "distance,duration",
        },
    )
    durations = payload.get("durations") or []
    distances = payload.get("distances") or []
    if len(durations) != len(points) or len(distances) != len(points):
        raise RuntimeError("OSRM full table returned an unexpected matrix shape.")

    time_matrix = [[0] * len(points) for _ in range(len(points))]
    distance_matrix = [[0] * len(points) for _ in range(len(points))]
    for i in range(len(points)):
        if len(durations[i]) != len(points) or len(distances[i]) != len(points):
            raise RuntimeError("OSRM full table returned inconsistent row lengths.")
        for j in range(len(points)):
            if i == j:
                continue
            distance_m_raw = distances[i][j]
            duration_s_raw = durations[i][j]
            if distance_m_raw is None or duration_s_raw is None:
                distance_matrix[i][j] = HUGE_DISTANCE_METERS
                time_matrix[i][j] = HUGE_TIME_SECONDS
                continue
            distance_matrix[i][j] = int(round(float(distance_m_raw)))
            duration_s = apply_traffic_time_multiplier(float(duration_s_raw))
            time_matrix[i][j] = duration_s + (STOP_SERVICE_SECONDS if j != 0 else 0)
    return time_matrix, distance_matrix


def osrm_driving_direction(origin: dict[str, Any], destination: dict[str, Any]) -> tuple[int, int, list[tuple[float, float]]]:
    origin_lat, origin_lng = point_osrm_lat_lng(origin)
    destination_lat, destination_lng = point_osrm_lat_lng(destination)
    payload = osrm_request_json(
        "route",
        f"{origin_lng},{origin_lat};{destination_lng},{destination_lat}",
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        },
    )
    routes = payload.get("routes") or []
    if not routes:
        raise RuntimeError(f"OSRM direction has no path: {origin['address']} -> {destination['address']}")
    best = routes[0]
    coordinates = ((best.get("geometry") or {}).get("coordinates")) or []
    geometry = [(float(lat), float(lng)) for lng, lat in coordinates]
    if not geometry:
        geometry = [(origin_lat, origin_lng), (destination_lat, destination_lng)]
    return int(round(float(best.get("distance", 0) or 0))), int(round(float(best.get("duration", 0) or 0))), geometry


def seed_candidate_edges(points: list[dict[str, Any]], seed_routes: list[dict[str, Any]]) -> dict[int, set[int]]:
    candidates = top_k_neighbors(points, MATRIX_NEAREST_NEIGHBORS, MATRIX_MAX_CANDIDATE_DISTANCE_KM)
    for route in seed_routes:
        for from_node, to_node in zip(route["nodes"][:-1], route["nodes"][1:]):
            candidates[from_node].add(to_node)
            if from_node != 0:
                candidates[from_node].add(0)
    return candidates


def refine_with_real_road(
    points: list[dict[str, Any]],
    base_time: list[list[int]],
    base_distance: list[list[int]],
    candidate_edges: dict[int, set[int]],
) -> tuple[list[list[int]], list[list[int]]]:
    time_matrix = [row[:] for row in base_time]
    distance_matrix = [row[:] for row in base_distance]
    for destination_node in range(1, len(points)):
        origins = sorted(i for i in range(len(points)) if destination_node in candidate_edges.get(i, set()) and i != destination_node)
        for offset in range(0, len(origins), 20):
            batch_nodes = origins[offset : offset + 20]
            batch_points = [points[node] for node in batch_nodes]
            try:
                results = osrm_distance_matrix_batch(batch_points, points[destination_node])
                for origin_node, (distance_m, duration_s) in zip(batch_nodes, results):
                    distance_matrix[origin_node][destination_node] = distance_m
                    time_matrix[origin_node][destination_node] = duration_s + STOP_SERVICE_SECONDS
            except Exception as exc:
                log(f"[WARN] OSRM matrix failed for destination {points[destination_node]['address']}: {exc}")
                for origin_node in batch_nodes:
                    try:
                        distance_m, duration_s, _ = osrm_driving_direction(points[origin_node], points[destination_node])
                        distance_matrix[origin_node][destination_node] = distance_m
                        time_matrix[origin_node][destination_node] = apply_traffic_time_multiplier(duration_s) + STOP_SERVICE_SECONDS
                    except Exception as inner_exc:
                        log(f"[WARN] OSRM direction fallback failed: {points[origin_node]['address']} -> {points[destination_node]['address']} -> {inner_exc}")
    return time_matrix, distance_matrix


def choose_revenue_rule(distance_km: float) -> dict[str, Any] | None:
    rules = sorted(REVENUE_RULES, key=lambda item: float(item.get("min_km", 0.0) or 0.0))
    for rule in rules:
        min_km = float(rule.get("min_km", 0.0) or 0.0)
        max_km_raw = rule.get("max_km")
        max_km = None if max_km_raw in (None, "", "None") else float(max_km_raw)
        if distance_km >= min_km and (max_km is None or distance_km < max_km):
            return rule
    return None


def enrich_routes_with_actual_driving(points: list[dict[str, Any]], routes: list[dict[str, Any]]) -> None:
    for route in routes:
        leg_details: list[dict[str, Any]] = []
        actual_time_s = 0
        actual_distance_m = 0
        raw_osrm_time_s = 0
        stop_service_time_s = 0
        for from_node, to_node in zip(route["nodes"][:-1], route["nodes"][1:]):
            try:
                distance_m, duration_s, geometry_wgs = osrm_driving_direction(points[from_node], points[to_node])
                if geometry_wgs:
                    geometry_wgs[0] = (points[from_node]["plot_lat"], points[from_node]["plot_lng"])
                    geometry_wgs[-1] = (points[to_node]["plot_lat"], points[to_node]["plot_lng"])
                else:
                    geometry_wgs = [
                        (points[from_node]["plot_lat"], points[from_node]["plot_lng"]),
                        (points[to_node]["plot_lat"], points[to_node]["plot_lng"]),
                    ]
            except Exception as exc:
                log(f"[WARN] Route geometry fallback to straight line: {points[from_node]['address']} -> {points[to_node]['address']} -> {exc}")
                distance_m = max(100, int(haversine_distance_km(points[from_node]["lat"], points[from_node]["lng"], points[to_node]["lat"], points[to_node]["lng"]) * 1000))
                duration_s = max(120, int((distance_m / 1000.0) * SEED_SECONDS_PER_KM))
                geometry_wgs = [
                    (points[from_node]["plot_lat"], points[from_node]["plot_lng"]),
                    (points[to_node]["plot_lat"], points[to_node]["plot_lng"]),
                ]
            adjusted_duration_s = apply_traffic_time_multiplier(duration_s)
            stop_service_s = STOP_SERVICE_SECONDS if to_node != 0 else 0
            actual_distance_m += distance_m
            raw_osrm_time_s += int(duration_s)
            stop_service_time_s += stop_service_s
            actual_time_s += adjusted_duration_s + stop_service_s
            leg_details.append(
                {
                    "from_node": from_node,
                    "to_node": to_node,
                    "distance_m": distance_m,
                    "duration_s": adjusted_duration_s + stop_service_s,
                    "raw_osrm_duration_s": int(duration_s),
                    "traffic_adjusted_duration_s": adjusted_duration_s,
                    "stop_service_s": stop_service_s,
                    "geometry": geometry_wgs,
                }
            )
        route["leg_details"] = leg_details
        route["time_s"] = actual_time_s
        route["distance_m"] = actual_distance_m
        route["raw_osrm_time_s"] = raw_osrm_time_s
        try:
            route_factor = float(route.get("traffic_buffer_factor"))
        except (TypeError, ValueError):
            route_factor = traffic_buffer_factor()
        route_factor = max(0.1, route_factor)
        route["traffic_buffer_factor"] = route_factor
        route["traffic_adjusted_drive_time_s"] = int(round(raw_osrm_time_s * route_factor)) if raw_osrm_time_s else 0
        route["stop_service_time_s"] = stop_service_time_s


def annotate_and_price_routes(
    points: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> None:
    # Financial fields are intentionally disabled in this OSRM-focused variant.
    for route in routes:
        cumulative_time = 0
        cumulative_distance_m = 0
        limit_stop_node = None
        limit_stop_order = None
        limit_stop_elapsed_s = None
        for order, leg in enumerate(route.get("leg_details", []), start=1):
            to_node = int(leg["to_node"])
            cumulative_time += int(leg["duration_s"])
            cumulative_distance_m += int(leg["distance_m"])
            if cumulative_time <= ANNOTATION_ROUTE_DURATION_SECONDS:
                limit_stop_node = to_node
                limit_stop_order = order
                limit_stop_elapsed_s = cumulative_time

        route["limit_stop_node"] = limit_stop_node
        route["limit_stop_order"] = limit_stop_order
        route["limit_stop_elapsed_s"] = limit_stop_elapsed_s
        route["stop_revenue_basis"] = []
        route["operating_cost"] = 0.0
        route["chargeable_revenue"] = 0.0
        route["profit_loss"] = 0.0
        route["revenue_details"] = []


def route_bus_mix(routes: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(route["bus_type_name"] for route in routes))


def scenario_totals(routes: list[dict[str, Any]]) -> tuple[float, float, float]:
    total_operating_cost = sum(float(route.get("operating_cost", 0.0)) for route in routes)
    total_chargeable_revenue = sum(float(route.get("chargeable_revenue", 0.0)) for route in routes)
    return total_operating_cost, total_chargeable_revenue, total_chargeable_revenue - total_operating_cost


def seconds_to_human(seconds: int | None) -> str:
    if seconds is None:
        return "N/A"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def route_colors(count: int) -> list[str]:
    colors = []
    for index in range(max(1, count)):
        hue = index / max(1, count)
        r, g, b = colorsys.hsv_to_rgb(hue, 0.72, 0.96)
        colors.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return colors


def build_map_summary_html(
    points: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    outlying_private_access_rows: list[dict[str, Any]] | None = None,
) -> str:
    traffic_note = (
        f"{TRAFFIC_PROFILE_NAME} traffic assumption "
        f"({TRAFFIC_TIME_MULTIPLIER:.2f}x travel-time multiplier; distance unchanged)"
    )
    if str(TRAFFIC_PROFILE_CONTEXT).strip():
        traffic_note = f"{traffic_note}, {TRAFFIC_PROFILE_CONTEXT}"
    service_label = "To School" if is_to_school_direction() else "From School"
    endpoint_note = (
        "The final address in each route is the school / destination."
        if is_to_school_direction()
        else "The first address is used as the departure point."
    )
    lines = [
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.45;'>",
        "<h3 style='margin:0 0 8px 0;'>Bus Route Summary</h3>",
        f"<p style='margin:0 0 6px 0;'><b>Service direction:</b> {service_label}</p>"
        f"<p style='margin:0 0 12px 0;'>{endpoint_note} "
        f"Routes are estimated under the {traffic_note}, one-way only, and the map marks the farthest stop "
        f"reachable within {ANNOTATION_ROUTE_DURATION_SECONDS // 60} minutes.</p>",
    ]
    outlying_private_access_rows = list(outlying_private_access_rows or [])
    private_access_mode = (
        str(outlying_private_access_rows[0].get("private_access_type", "clustered_rider")).strip()
        if outlying_private_access_rows else "clustered_rider"
    ) or "clustered_rider"
    private_access_by_route: dict[str, list[dict[str, Any]]] = {}
    private_access_by_pickup: dict[str, list[dict[str, Any]]] = {}
    for item in outlying_private_access_rows:
        access_type = str(item.get("private_access_type", "clustered_rider")).strip() or "clustered_rider"
        if access_type == "private_drive_stop":
            route_id = str(item.get("pickup_route_id", "")).strip()
            if route_id:
                private_access_by_route.setdefault(route_id, []).append(item)
        else:
            pickup_address = str(item.get("pickup_address", "")).strip()
            if pickup_address:
                private_access_by_pickup.setdefault(pickup_address, []).append(item)
    for route in routes:
        route_id = f"Bus {route['vehicle_id']}"
        display_time_s = (
            route.get("traffic_api_duration_s")
            if bool(route.get("traffic_coverage_complete", True))
            else None
        )
        display_time_source = str(route.get("traffic_time_source", "")).strip()
        if display_time_s is None:
            display_time_s = route.get("traffic_adjusted_drive_time_s")
            display_time_source = "OSRM drive time * traffic buffer"
        if display_time_s is None:
            display_time_s = route.get("time_s")
        raw_osrm_time_s = route.get("raw_osrm_time_s") or route.get("traffic_osrm_duration_s")
        route_buffer_factor = route.get("traffic_buffer_factor")
        try:
            route_buffer_factor_float = float(route_buffer_factor)
        except (TypeError, ValueError):
            route_buffer_factor_float = None
        comfort_capacity = int(route.get("comfort_capacity", route.get("bus_capacity", 0)) or 0)
        bus_capacity = int(route.get("bus_capacity", 0) or 0)
        stop_count = int(route.get("stop_count", max(0, len(route.get("nodes", [])) - 1)) or 0)
        max_stops = int(route.get("max_stops", route_stop_limit()) or route_stop_limit())
        passenger_line = (
            f"<div>Passengers: {route['load']} / {comfort_capacity} comfort seats ({bus_capacity} physical)</div>"
            if comfort_capacity and comfort_capacity != bus_capacity
            else f"<div>Passengers: {route['load']} / {bus_capacity} seats</div>"
        )
        lines.extend(
            [
                f"<h4 style='margin:12px 0 4px 0;'>Bus {route['vehicle_id']}</h4>",
                f"<div>Vehicle type: {route['bus_type_name']}</div>",
                passenger_line,
                f"<div>Stops: {stop_count} / {max_stops}</div>",
                f"<div>Estimated time: {seconds_to_human(display_time_s)}</div>",
                f"<div>Estimated distance: {route['distance_m']/1000.0:.1f} km</div>",
            ]
        )
        if display_time_source:
            lines.append(f"<div>Time source: {html.escape(display_time_source)}</div>")
        if raw_osrm_time_s:
            lines.append(f"<div>OSRM drive time: {seconds_to_human(raw_osrm_time_s)}</div>")
        if route_buffer_factor_float is not None:
            lines.append(f"<div>Traffic buffer: {route_buffer_factor_float:.2f}x</div>")
        if route.get("traffic_sampled_leg_count") is not None and route.get("traffic_total_leg_count") is not None:
            lines.append(
                f"<div>AMap sampled legs: {int(route.get('traffic_sampled_leg_count') or 0)} / "
                f"{int(route.get('traffic_total_leg_count') or 0)}</div>"
            )
        if route.get("limit_stop_order") is not None:
            lines.append(
                f"<div><b>{ANNOTATION_ROUTE_DURATION_SECONDS // 60}-minute mark:</b> Stop {route['limit_stop_order']} "
                f"({seconds_to_human(route['limit_stop_elapsed_s'])})</div>"
            )
        lines.append("<ol style='padding-left:18px;'>")
        final_index = max(0, len(route["nodes"]) - 1)
        for order, node in enumerate(route["nodes"]):
            if order == 0:
                prefix = "Start"
            elif is_to_school_direction() and order == final_index:
                prefix = "School"
            else:
                prefix = f"Stop {order}"
            lines.append(f"<li>{prefix}: {html.escape(stop_display_text(points[node]))}</li>")
        lines.append("</ol>")
        route_private_drive_stops = list(private_access_by_route.get(route_id) or [])
        if route_private_drive_stops:
            route_private_drive_stops.sort(
                key=lambda item: (
                    -float(item.get("private_drive_time_s", 0.0) or 0.0),
                    str(item.get("address", "")).strip().lower(),
                ),
            )
            lines.append("<div style='margin-top:6px;'><b>Private drive stops</b></div>")
            lines.append("<ul style='margin:4px 0 0 18px;padding-left:12px;'>")
            for item in route_private_drive_stops:
                member_address = html.escape(str(item.get("address", "")).strip())
                drive_minutes = float(item.get("private_drive_time_s", 0.0) or 0.0) / 60.0
                drive_km = float(item.get("private_drive_distance_m", 0.0) or 0.0) / 1000.0
                lines.append(f"<li>{member_address} ({drive_minutes:.1f} min, {drive_km:.2f} km)</li>")
            lines.append("</ul>")
    if private_access_by_pickup and private_access_mode != "private_drive_stop":
        lines.append("<h4 style='margin:12px 0 6px 0;'>Nearby Cluster Centers</h4>")
        for pickup_address, members in sorted(private_access_by_pickup.items(), key=lambda item: item[0].lower()):
            sorted_members = sorted(
                members,
                key=lambda item: (
                    -float(item.get("private_drive_time_s", 0.0) or 0.0),
                    str(item.get("address", "")).strip().lower(),
                ),
            )
            lines.append(
                f"<div style='margin-top:8px;'><b>{html.escape(pickup_address)}</b> "
                f"({len(sorted_members)} clustered rider(s))</div>"
            )
            lines.append("<ul style='margin:4px 0 0 18px;padding-left:12px;'>")
            for item in sorted_members:
                member_address = html.escape(str(item.get("address", "")).strip())
                drive_minutes = float(item.get("private_drive_time_s", 0.0) or 0.0) / 60.0
                drive_km = float(item.get("private_drive_distance_m", 0.0) or 0.0) / 1000.0
                lines.append(f"<li>{member_address} ({drive_minutes:.1f} min, {drive_km:.2f} km)</li>")
            lines.append("</ul>")
    lines.append("</div>")
    return "".join(lines)


def render_map(
    points: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    output_html: str,
    outlying_private_access_rows: list[dict[str, Any]] | None = None,
) -> None:
    output_path = Path(output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not points:
        output_path.write_text("<html><body><p>No valid points to render.</p></body></html>", encoding="utf-8")
        return
    avg_lat = sum(point["plot_lat"] for point in points) / len(points)
    avg_lng = sum(point["plot_lng"] for point in points) / len(points)
    fmap = folium.Map(
        location=[avg_lat, avg_lng],
        zoom_start=11,
        tiles="https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    )
    colors = route_colors(len(routes))
    outlying_private_access_rows = list(outlying_private_access_rows or [])
    private_access_by_pickup: dict[str, list[dict[str, Any]]] = {}
    for item in outlying_private_access_rows:
        pickup_address = str(item.get("pickup_address", "")).strip()
        if pickup_address:
            private_access_by_pickup.setdefault(pickup_address, []).append(item)
    private_access_mode = (
        str(outlying_private_access_rows[0].get("private_access_type", "clustered_rider")).strip()
        if outlying_private_access_rows else "clustered_rider"
    )

    def build_cluster_popup(point: dict[str, Any]) -> str:
        address = str(point.get("address", "")).strip()
        base_popup = f"{html.escape(stop_display_text(point))}"
        members = list(private_access_by_pickup.get(address) or [])
        if not members:
            return base_popup
        access_type = str(members[0].get("private_access_type", "clustered_rider")).strip() or "clustered_rider"
        members.sort(
            key=lambda item: (
                -float(item.get("private_drive_time_s", 0.0) or 0.0),
                str(item.get("address", "")).strip().lower(),
            )
        )
        member_rows = "".join(
            (
                "<li>"
                f"{html.escape(str(item.get('address', '')).strip())}"
                f" ({float(item.get('private_drive_time_s', 0.0) or 0.0) / 60.0:.1f} min, "
                f"{float(item.get('private_drive_distance_m', 0.0) or 0.0) / 1000.0:.2f} km)"
                "</li>"
            )
            for item in members
        )
        member_title = "Private drive stops" if access_type == "private_drive_stop" else "Clustered addresses"
        center_title = "xx-minute-mark pickup" if access_type == "private_drive_stop" else "Nearby cluster center"
        return (
            f"{base_popup}<br><b>{center_title}</b><br>"
            f"Connected riders: {len(members)}"
            f"<br><div style='margin-top:6px;'><b>{member_title}</b></div>"
            f"<ol style='margin:4px 0 0 16px;padding-left:12px;'>{member_rows}</ol>"
        )

    for route_index, route in enumerate(routes):
        color = colors[route_index % len(colors)]
        geometry: list[tuple[float, float]] = []
        for leg_detail in route.get("leg_details", []):
            leg = leg_detail["geometry"]
            geometry.extend(leg if not geometry else leg[1:])
        if len(geometry) >= 2:
            folium.PolyLine(geometry, color="#ffffff", weight=9, opacity=0.9).add_to(fmap)
            folium.PolyLine(
                geometry,
                color=color,
                weight=6,
                opacity=0.9,
                tooltip=(
                    f"Bus {route['vehicle_id']} | {route['bus_type_name']} | "
                    f"{route['load']}/{route.get('comfort_capacity', route['bus_capacity'])} comfort | "
                    f"{route.get('stop_count', max(0, len(route.get('nodes', [])) - 1))}/"
                    f"{route.get('max_stops', route_stop_limit())} stops"
                ),
            ).add_to(fmap)
        for order, node in enumerate(route["nodes"]):
            point = points[node]
            final_index = max(0, len(route["nodes"]) - 1)
            point_label = (
                "Start"
                if order == 0
                else "School" if is_to_school_direction() and order == final_index
                else f"Stop {order}"
            )
            folium.CircleMarker(
                location=[point["plot_lat"], point["plot_lng"]],
                radius=10 if order == 0 else 9,
                color="#111827" if point.get("is_depot") else color,
                fill=True,
                fill_color=color,
                fill_opacity=0.92,
                popup=f"{point_label}<br>{build_cluster_popup(point)}",
            ).add_to(fmap)
            folium.Marker(
                location=[point["plot_lat"], point["plot_lng"]],
                icon=folium.DivIcon(
                    html=f"<div style='font-size:11px;font-weight:700;color:white;text-align:center;line-height:18px;'>{order}</div>"
                ),
            ).add_to(fmap)
        limit_node = route.get("limit_stop_node")
        if limit_node is not None:
            point = points[limit_node]
            folium.CircleMarker(
                location=[point["plot_lat"], point["plot_lng"]],
                radius=14,
                color="#111827",
                weight=3,
                fill=False,
                tooltip=f"{ANNOTATION_ROUTE_DURATION_SECONDS // 60}-minute mark for Bus {route['vehicle_id']}: {point['address']}",
            ).add_to(fmap)
    if outlying_private_access_rows:
        address_to_point = {
            str(point.get("address", "")).strip(): point
            for point in points
            if str(point.get("address", "")).strip()
        }
        outlying_group = folium.FeatureGroup(name="Private Drive Stops", show=True)
        pickup_group = folium.FeatureGroup(name="Private Drive Pickups", show=True)
        cluster_group_name = "Further Most Pickups" if private_access_mode == "private_drive_stop" else "Nearby Cluster Centers"
        cluster_group = folium.FeatureGroup(name=cluster_group_name, show=True)
        access_group = folium.FeatureGroup(name="Private Drive Connections", show=True)
        for item in outlying_private_access_rows:
            stop_address = str(item.get("address", "")).strip()
            pickup_address = str(item.get("pickup_address", "")).strip()
            stop_point = address_to_point.get(stop_address)
            pickup_point = address_to_point.get(pickup_address)
            stop_coords = None
            pickup_coords = None
            if stop_point:
                stop_coords = (stop_point["plot_lat"], stop_point["plot_lng"])
            else:
                stop_lat = float(item.get("plot_lat", 0.0) or 0.0)
                stop_lng = float(item.get("plot_lng", 0.0) or 0.0)
                if stop_lat and stop_lng:
                    stop_coords = (stop_lat, stop_lng)
            if pickup_point:
                pickup_coords = (pickup_point["plot_lat"], pickup_point["plot_lng"])
            else:
                pickup_lat = float(item.get("pickup_plot_lat", 0.0) or 0.0)
                pickup_lng = float(item.get("pickup_plot_lng", 0.0) or 0.0)
                if pickup_lat and pickup_lng:
                    pickup_coords = (pickup_lat, pickup_lng)
            if stop_coords:
                folium.CircleMarker(
                    location=[stop_coords[0], stop_coords[1]],
                    radius=8,
                    color="#6b7280",
                    weight=2,
                    fill=True,
                    fill_color="#9ca3af",
                    fill_opacity=0.92,
                    tooltip=(
                        f"Clustered rider<br>{stop_address}<br>"
                        f"Drive to center: {float(item.get('private_drive_time_s', 0.0) or 0.0) / 60.0:.1f} min"
                    ),
                ).add_to(outlying_group)
            if pickup_coords:
                folium.CircleMarker(
                    location=[pickup_coords[0], pickup_coords[1]],
                    radius=9,
                    color="#92400e",
                    weight=3,
                    fill=True,
                    fill_color="#f59e0b",
                    fill_opacity=0.9,
                    popup=build_cluster_popup(pickup_point),
                    tooltip=(
                        f"Feasible pickup on {str(item.get('pickup_route_id', '')).strip()}<br>{pickup_address}"
                    ),
                ).add_to(pickup_group)
            if stop_coords and pickup_coords:
                connection_geometry = [
                    (float(lat), float(lng))
                    for lat, lng in list(item.get("private_drive_geometry") or [])
                    if lat is not None and lng is not None
                ]
                if len(connection_geometry) < 2:
                    connection_geometry = [
                        stop_coords,
                        pickup_coords,
                    ]
                access_type = str(item.get("private_access_type", "clustered_rider")).strip() or "clustered_rider"
                access_label = "Private drive stop" if access_type == "private_drive_stop" else "Clustered rider to center"
                folium.PolyLine(
                    connection_geometry,
                    color="#6b7280",
                    weight=3,
                    opacity=0.85,
                    dash_array="6, 8",
                    tooltip=(
                        f"{access_label}: {float(item.get('private_drive_time_s', 0.0) or 0.0) / 60.0:.1f} min | "
                        f"{float(item.get('private_drive_distance_m', 0.0) or 0.0) / 1000.0:.1f} km"
                    ),
                ).add_to(access_group)
        for pickup_address, members in private_access_by_pickup.items():
            pickup_point = address_to_point.get(pickup_address)
            if not pickup_point:
                continue
            folium.Marker(
                location=[pickup_point["plot_lat"], pickup_point["plot_lng"]],
                icon=folium.DivIcon(
                    html=(
                        "<div style='transform: translate(-50%, -50%);'>"
                        "<div style='width:18px;height:18px;border-radius:50%;"
                        "background:#f59e0b;border:3px solid #111827;"
                        "box-shadow:0 0 0 2px rgba(255,255,255,0.95);'></div>"
                        "</div>"
                    )
                ),
                tooltip=(
                    f"{'xx-minute-mark pickup' if private_access_mode == 'private_drive_stop' else 'Nearby cluster center'}"
                    f"<br>{pickup_address}<br>{len(members)} connected rider(s)"
                ),
                popup=build_cluster_popup(pickup_point),
            ).add_to(cluster_group)
        outlying_group.add_to(fmap)
        pickup_group.add_to(fmap)
        cluster_group.add_to(fmap)
        access_group.add_to(fmap)
    panel = (
        "<div style='position:fixed;top:12px;right:12px;z-index:9999;width:340px;max-height:78vh;overflow:auto;"
        "background:rgba(255,255,255,0.94);padding:12px 14px;border-radius:10px;box-shadow:0 6px 18px rgba(0,0,0,0.15);'>"
        f"{build_map_summary_html(points, routes, outlying_private_access_rows=outlying_private_access_rows)}</div>"
    )
    fmap.get_root().html.add_child(Element(panel))
    folium.LayerControl().add_to(fmap)
    fmap.save(str(output_path))


def build_scenario_result(points: list[dict[str, Any]], routes: list[dict[str, Any]], output_html: str) -> dict[str, Any]:
    service_points = [point for point in points if not bool(point.get("is_depot"))]
    physical_service_points = [
        point
        for point in service_points
        if int(point.get("demand_batch_index", 1) or 1) <= 1
    ]
    total_distance_m = sum(float(route.get("distance_m", 0.0)) for route in routes)
    total_duration_s = sum(float(route.get("time_s", 0.0)) for route in routes)
    avg_route_distance_m = (total_distance_m / len(routes)) if routes else 0.0
    avg_route_duration_s = (total_duration_s / len(routes)) if routes else 0.0
    avg_route_load_factor = (
        sum(
            (float(route.get("load", 0.0)) / float(route.get("bus_capacity", 1) or 1))
            for route in routes
        ) / len(routes)
        if routes else 0.0
    )
    avg_route_comfort_load_factor = (
        sum(
            (
                float(route.get("load", 0.0))
                / float(route.get("comfort_capacity", route.get("bus_capacity", 1)) or 1)
            )
            for route in routes
        ) / len(routes)
        if routes else 0.0
    )
    max_route_stop_count = max(
        (int(route.get("stop_count", max(0, len(route.get("nodes", [])) - 1)) or 0) for route in routes),
        default=0,
    )
    return {
        "points": points,
        "routes": routes,
        "output_html": output_html,
        "bus_count": len(routes),
        "stop_count": len(physical_service_points),
        "service_stop_count": len(physical_service_points),
        "solver_stop_count": len(service_points),
        "map_point_count": len(points),
        "bus_mix": route_bus_mix(routes),
        "total_distance_m": total_distance_m,
        "total_duration_s": total_duration_s,
        "avg_route_distance_m": avg_route_distance_m,
        "avg_route_duration_s": avg_route_duration_s,
        "avg_route_load_factor": avg_route_load_factor,
        "avg_route_comfort_load_factor": avg_route_comfort_load_factor,
        "max_route_stop_count": max_route_stop_count,
        "max_stops_per_route": route_stop_limit(),
        "comfort_load_factor_limit": min(1.0, max(0.1, float(COMFORT_LOAD_FACTOR))),
        "total_operating_cost": 0.0,
        "total_chargeable_revenue": 0.0,
        "total_profit_loss": 0.0,
    }


def build_scenario(points: list[dict[str, Any]], output_html: str, scenario_label: str) -> dict[str, Any]:
    if len(points) <= 1:
        routes: list[dict[str, Any]] = []
        render_map(points, routes, output_html)
        return build_scenario_result(points, routes, output_html)
    full_fleet = build_vehicle_fleet()
    if full_fleet:
        max_comfort_capacity = max(solver_capacity_for_vehicle(item) for item in full_fleet)
        points = split_oversized_demand_points(points, max_comfort_capacity)
    log(f"[INFO] Building {scenario_label} scenario with {len(points)} total points.")
    global OSRM_BASE_URL
    scenario_osrm_base_url = resolve_osrm_base_url(points)
    previous_osrm_base_url = OSRM_BASE_URL
    OSRM_BASE_URL = scenario_osrm_base_url
    log(f"[INFO] Using OSRM backend {scenario_osrm_base_url} for {scenario_label} scenario.")
    try:
        log(f"[INFO] Building full OSRM matrix for {scenario_label} scenario.")
        solve_time, solve_distance = build_osrm_full_matrix(points)
    except Exception as exc:
        log(f"[WARN] OSRM full matrix failed for {scenario_label}; falling back to seed matrix: {exc}")
        solve_time, solve_distance = seed_edge_metrics(points)
    try:
        final_routes = solve_routes(points, solve_time, solve_distance)
        enrich_routes_with_actual_driving(points, final_routes)
        annotate_and_price_routes(points, final_routes)
        render_map(points, final_routes, output_html)
        return build_scenario_result(points, final_routes, output_html)
    finally:
        OSRM_BASE_URL = previous_osrm_base_url


def normalized_input_stops() -> list[dict[str, Any]]:
    if INPUT_STOPS:
        return [
            {
                "country": str(item.get("country", "")).strip(),
                "city": str(item.get("city", "")).strip(),
                "address": str(item["address"]).strip(),
                "passenger_count": int(item.get("passenger_count", 0 if index == 0 else 1)),
            }
            for index, item in enumerate(INPUT_STOPS)
            if str(item.get("address", "")).strip()
        ]
    if ADDRESSES:
        return [
            {
                "country": "",
                "city": "",
                "address": str(address).strip(),
                "passenger_count": 0 if index == 0 else 1,
            }
            for index, address in enumerate(ADDRESSES)
            if str(address).strip()
        ]
    raise RuntimeError("No input addresses were supplied.")


def main() -> dict[str, Any]:
    global CURRENT_CURRENCY_CODE, LAST_RUN_RESULTS

    input_records = normalized_input_stops()
    CURRENT_CURRENCY_CODE = determine_currency_code(input_records)
    log(f"Geocoding addresses with AMap, then routing against OSRM at {OSRM_BASE_URL} ...")
    points, geocode_warnings = geocode_records(input_records)
    if geocode_warnings:
        log("[WARN] The following addresses were not accepted as valid stops:")
        for item in geocode_warnings:
            log(f"  - {item['address']}")
    log(f"Valid stops: {len(points)} / {len(input_records)}")
    log("Building OSRM road-network matrices first, then solving with OR-Tools, then fetching final OSRM route geometry for the chosen legs ...")

    original_points = [dict(point) for point in points]
    for idx, point in enumerate(original_points):
        point["node_id"] = idx
    subway_points = build_subway_aggregated_points(original_points)
    nearby_points = build_nearby_aggregated_points(original_points)

    original_result = build_scenario(original_points, OUTPUT_HTML, "Original stops")
    subway_result = build_scenario(subway_points, SUBWAY_OUTPUT_HTML, "Subway aggregated")
    nearby_result = build_scenario(nearby_points, NEARBY_OUTPUT_HTML, "Nearby-address aggregated")

    service_original_points = [point for point in original_points if not bool(point.get("is_depot"))]
    LAST_RUN_RESULTS = {
        "original": original_result,
        "subway": subway_result,
        "nearby": nearby_result,
        "input_address_count": len([item for item in input_records if int(item.get("passenger_count", 0) or 0) > 0]),
        "input_point_count": len(input_records),
        "valid_stop_count": len(service_original_points),
        "valid_point_count": len(original_points),
        "currency_code": CURRENT_CURRENCY_CODE,
    }
    return LAST_RUN_RESULTS


if __name__ == "__main__":
    main()
