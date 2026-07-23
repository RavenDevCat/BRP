from __future__ import annotations

import colorsys
from datetime import datetime
import html
import math
import os
import re
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import folium
import requests
from folium import Element

from api_rate_limit import CrossProcessRateLimiter
from json_cache_store import load_json_object, save_json_object
from quota_store_sqlite import SqliteQuotaStore


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("BRP_CLIENT_CACHE_DIR", str(BASE_DIR / "cache"))).expanduser()
GEOCODE_CACHE_PATH = CACHE_DIR / "geocode_cache.json"
GOOGLE_GEOCODE_USAGE_PATH = CACHE_DIR / "google_geocode_usage.json"
GOOGLE_GEOCODE_USAGE_PROVIDER = "google_geocode"
GOOGLE_GEOCODE_USAGE_COUNTER = "geocode"
GOOGLE_GEOCODE_USAGE_PROVIDER_LABEL = "Google Geocoding"
GOOGLE_GEOCODE_USAGE_SKU_ESTIMATE = "geocoding"

AMAP_KEY = os.environ.get("AMAP_API_KEY", "").strip()
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
GOOGLE_GEOCODE_API_KEY = os.environ.get("GOOGLE_GEOCODE_API_KEY", "").strip()
AMAP_GEOCODE_MAX_QPS = 2.8
AMAP_PLACES_MAX_QPS = 2.8
KAKAO_GEOCODE_MAX_QPS = 2.8
GOOGLE_GEOCODE_MAX_QPS = 2.8
GOOGLE_GEOCODE_MONTHLY_LIMIT = 10_000
GOOGLE_GEOCODE_BASE_URL = os.environ.get(
    "GOOGLE_GEOCODE_BASE_URL",
    "https://maps.googleapis.com/maps/api/geocode/json",
).strip()
GOOGLE_GEOCODE_TIMEOUT_SECONDS = float(os.environ.get("GOOGLE_GEOCODE_TIMEOUT_SECONDS", "8") or 8)
BK_GEOCODE_MODE = os.environ.get("BRP_BK_GEOCODE_MODE", "").strip().lower()
GOOGLE_GEOCODE_RELAY_URL = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_URL", "").strip()
GOOGLE_GEOCODE_RELAY_FALLBACK_URL = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_FALLBACK_URL", "").strip()
GOOGLE_GEOCODE_RELAY_TOKEN = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TOKEN", "").strip()
GOOGLE_GEOCODE_RELAY_TIMEOUT_SECONDS = float(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TIMEOUT_SECONDS", "8") or 8)
REQUEST_TIMEOUT = 20
KAKAO_REQUEST_MAX_RETRIES = 3

ANNOTATION_ROUTE_DURATION_SECONDS = 60 * 60
GEOCODE_SCHOOL_DISTANCE_REVIEW_KM = float(os.environ.get("BRP_GEOCODE_SCHOOL_DISTANCE_REVIEW_KM", "55") or 0)


class RateLimiter:
    def __init__(self, name: str, max_qps: float) -> None:
        self.shared_limiter = CrossProcessRateLimiter(name, max_qps)

    def wait(self) -> None:
        self.shared_limiter.wait()


AMAP_GEOCODE_LIMITER = RateLimiter("amap-geocode", AMAP_GEOCODE_MAX_QPS)
AMAP_PLACES_LIMITER = RateLimiter("amap-places", AMAP_PLACES_MAX_QPS)
KAKAO_GEOCODE_LIMITER = RateLimiter("kakao-geocode", KAKAO_GEOCODE_MAX_QPS)
GOOGLE_GEOCODE_LIMITER = RateLimiter("google-geocode", GOOGLE_GEOCODE_MAX_QPS)


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_json_cache(path: Path) -> dict[str, Any]:
    ensure_cache_dir()
    return load_json_object(path)


def save_json_cache(path: Path, payload: dict[str, Any]) -> None:
    ensure_cache_dir()
    save_json_object(path, payload)


def _load_google_geocode_usage_payload() -> dict[str, Any]:
    payload = load_json_cache(GOOGLE_GEOCODE_USAGE_PATH)
    return payload if isinstance(payload, dict) else {}


def _refresh_google_geocode_usage(payload: dict[str, Any]) -> None:
    GOOGLE_GEOCODE_USAGE.clear()
    GOOGLE_GEOCODE_USAGE.update(payload)


def _coerce_google_geocode_usage_value(payload: dict[str, Any], month_key: str) -> int:
    raw_value = payload.get(month_key, 0)
    try:
        return max(0, int(raw_value))
    except Exception:
        return 0


GEOCODE_CACHE = load_json_cache(GEOCODE_CACHE_PATH)
GOOGLE_GEOCODE_USAGE = _load_google_geocode_usage_payload()


def _google_geocode_quota_store() -> SqliteQuotaStore:
    store = SqliteQuotaStore()
    store.migrate_flat_month_usage(
        source_path=GOOGLE_GEOCODE_USAGE_PATH,
        provider=GOOGLE_GEOCODE_USAGE_PROVIDER,
        counter=GOOGLE_GEOCODE_USAGE_COUNTER,
        provider_label=GOOGLE_GEOCODE_USAGE_PROVIDER_LABEL,
        sku_estimate=GOOGLE_GEOCODE_USAGE_SKU_ESTIMATE,
    )
    return store


def _sync_google_geocode_usage_cache(month_key: str, used: int) -> None:
    GOOGLE_GEOCODE_USAGE.clear()
    GOOGLE_GEOCODE_USAGE[month_key] = max(0, int(used or 0))


def log(message: str) -> None:
    print(message, flush=True)


def require_api_key(env_name: str, value: str, provider_label: str) -> str:
    if value:
        return value
    raise RuntimeError(
        f"{provider_label} API key is not configured. "
        f"Set environment variable `{env_name}` before running this workflow."
    )


def use_google_geocode_relay(country: str) -> bool:
    return is_bangkok_market_country(country) and BK_GEOCODE_MODE in {"google_relay", "relay"}


def current_google_usage_month_key() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")


def read_google_geocode_monthly_usage(month_key: str | None = None) -> int:
    usage_month_key = month_key or current_google_usage_month_key()
    usage = _google_geocode_quota_store().get_usage(
        GOOGLE_GEOCODE_USAGE_PROVIDER,
        GOOGLE_GEOCODE_USAGE_COUNTER,
        "month",
        usage_month_key,
    )
    used = _coerce_google_geocode_usage_value(usage, "attempted")
    _sync_google_geocode_usage_cache(usage_month_key, used)
    return used


def assert_google_geocode_monthly_limit_not_exceeded() -> str:
    month_key = current_google_usage_month_key()
    if read_google_geocode_monthly_usage(month_key) >= GOOGLE_GEOCODE_MONTHLY_LIMIT:
        raise RuntimeError(
            "Google geocoding monthly usage limit reached. "
            f"Usage is capped at {GOOGLE_GEOCODE_MONTHLY_LIMIT:,} requests per month."
        )
    return month_key


def reserve_google_geocode_monthly_usage() -> str:
    month_key = current_google_usage_month_key()
    try:
        result = _google_geocode_quota_store().reserve_usage(
            GOOGLE_GEOCODE_USAGE_PROVIDER,
            GOOGLE_GEOCODE_USAGE_COUNTER,
            [("month", month_key, GOOGLE_GEOCODE_MONTHLY_LIMIT)],
            count=1,
            provider_label=GOOGLE_GEOCODE_USAGE_PROVIDER_LABEL,
            sku_estimate=GOOGLE_GEOCODE_USAGE_SKU_ESTIMATE,
        )
    except RuntimeError as exc:
        if "usage cap would be exceeded" in str(exc):
            raise RuntimeError(
                "Google geocoding monthly usage limit reached. "
                f"Usage is capped at {GOOGLE_GEOCODE_MONTHLY_LIMIT:,} requests per month."
            ) from exc
        raise
    _sync_google_geocode_usage_cache(month_key, int(result.get("month", {}).get("attempted", 0) or 0))
    return month_key


def increment_google_geocode_monthly_usage(month_key: str) -> None:
    result = _google_geocode_quota_store().reserve_usage(
        GOOGLE_GEOCODE_USAGE_PROVIDER,
        GOOGLE_GEOCODE_USAGE_COUNTER,
        [("month", month_key, GOOGLE_GEOCODE_MONTHLY_LIMIT)],
        count=1,
        provider_label=GOOGLE_GEOCODE_USAGE_PROVIDER_LABEL,
        sku_estimate=GOOGLE_GEOCODE_USAGE_SKU_ESTIMATE,
    )
    _sync_google_geocode_usage_cache(month_key, int(result.get("month", {}).get("attempted", 0) or 0))


def determine_currency_code(input_records: list[dict[str, Any]]) -> str:
    countries = " ".join(str(item.get("country", "")).strip().lower() for item in input_records)
    if any(token in countries for token in ("korea", "south korea", "대한민국", "한국")):
        return "KRW"
    if any(token in countries for token in ("bangkok", "bk", "thailand", "thai", "ประเทศไทย", "ไทย")):
        return "THB"
    if any(token in countries for token in ("china", "中国", "中华人民共和国")):
        return "CNY"
    return "USD"


def is_korea_country(country: str) -> bool:
    normalized = country.strip().lower()
    return normalized in {
        "korea",
        "south korea",
        "republic of korea",
        "대한민국",
        "한국",
        "남한",
    }


def is_china_country(country: str) -> bool:
    return country.strip().lower() in {"china", "中国", "中华人民共和国"}


def is_bangkok_market_country(country: str) -> bool:
    return country.strip().lower() in {"bangkok", "bk", "bangkok market", "thailand", "thai", "th", "ประเทศไทย", "ไทย"}


CHINA_CITY_CONFIGS: dict[str, dict[str, Any]] = {
    "shanghai": {
        "canonical": "Shanghai",
        "amap_city": "310000",
        "aliases": ["shanghai", "shanghai city", "上海", "上海市"],
        "adcode_prefixes": ["31"],
        "bbox": (31.90, 122.25, 30.65, 120.85),
    },
    "beijing": {
        "canonical": "Beijing",
        "amap_city": "110000",
        "aliases": ["beijing", "beijing city", "北京", "北京市"],
        "adcode_prefixes": ["11"],
        "bbox": (41.10, 117.60, 39.40, 115.40),
    },
    "suzhou": {
        "canonical": "Suzhou",
        "amap_city": "320500",
        "aliases": ["suzhou", "suzhou city", "苏州", "苏州市"],
        "adcode_prefixes": ["3205"],
        "bbox": (32.15, 121.35, 30.75, 119.85),
    },
    "xian": {
        "canonical": "Xian",
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


BANGKOK_MARKET_CITY_CONFIGS: dict[str, dict[str, Any]] = {
    "bangkok": {
        "canonical": "Bangkok",
        "aliases": [
            "bangkok",
            "bangkok metropolis",
            "krung thep",
            "krung thep maha nakhon",
            "กรุงเทพ",
            "กรุงเทพฯ",
            "กรุงเทพมหานคร",
        ],
    },
}


BANGKOK_MARKET_CITY_ALIAS_TO_KEY = {
    alias.lower(): city_key
    for city_key, config in BANGKOK_MARKET_CITY_CONFIGS.items()
    for alias in config["aliases"]
}


def _normalize_china_city_key(city: str) -> str:
    normalized = " ".join(city.strip().lower().replace("’", "'").split())
    return CHINA_CITY_ALIAS_TO_KEY.get(normalized, normalized)


def _china_city_config(city: str) -> dict[str, Any] | None:
    return CHINA_CITY_CONFIGS.get(_normalize_china_city_key(city))


def _normalize_bangkok_market_city_key(city: str) -> str:
    normalized = " ".join(city.strip().lower().split())
    return BANGKOK_MARKET_CITY_ALIAS_TO_KEY.get(normalized, normalized)


def _bangkok_market_city_config(city: str) -> dict[str, Any] | None:
    return BANGKOK_MARKET_CITY_CONFIGS.get(_normalize_bangkok_market_city_key(city))


def _amap_city_param(country: str, city: str) -> str:
    if not is_china_country(country):
        return city.strip()
    config = _china_city_config(city)
    if config:
        return str(config["amap_city"])
    return city.strip()


def _metro_query_cities(country: str, city: str) -> list[str]:
    raw_city = city.strip()
    if not raw_city:
        return []
    cities = [raw_city]
    if is_korea_country(country) and raw_city.strip().lower() in {
        "seoul",
        "서울",
        "seongnam",
        "seongnam-si",
        "seongnam si",
        "성남",
        "성남시",
    }:
        for candidate in ("Seoul", "서울", "Seongnam", "Seongnam-si", "성남시"):
            if candidate not in cities:
                cities.append(candidate)
    if is_bangkok_market_country(country) and _normalize_bangkok_market_city_key(raw_city) == "bangkok":
        for candidate in ("Bangkok", "Bangkok Metropolis", "Krung Thep", "กรุงเทพมหานคร", "กรุงเทพ"):
            if candidate not in cities:
                cities.append(candidate)
    return cities


def build_geocode_queries(country: str, city: str, address: str) -> list[str]:
    base_address = address.strip()
    queries: list[str] = []
    query_candidates = [base_address]
    metro_cities = _metro_query_cities(country, city)
    for metro_city in metro_cities:
        query_candidates.append(f"{metro_city} {base_address}")
        if country.strip():
            query_candidates.append(f"{country.strip()} {metro_city} {base_address}")
    if country.strip():
        query_candidates.append(f"{country.strip()} {base_address}")

    for query in query_candidates:
        query = query.strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def address_contains_hangul(text: str) -> bool:
    return bool(re.search(r"[\uac00-\ud7a3]", text))


def is_likely_english_korean_address(country: str, address: str) -> bool:
    if not is_korea_country(country):
        return False
    stripped = address.strip()
    if not stripped or address_contains_hangul(stripped):
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def expected_geocode_provider(country: str, address: str) -> str:
    return geocode_provider_order(country, address)[0]


def geocode_provider_order(country: str, address: str) -> list[str]:
    if is_china_country(country):
        return ["amap"]
    if is_korea_country(country):
        primary = "google" if is_likely_english_korean_address(country, address) else "kakao"
        fallback = "kakao" if primary == "google" else "google"
        return [primary, fallback]
    return ["google"]


def failed_geocode_cache_providers(entry: dict[str, Any]) -> set[str]:
    providers: set[str] = set()
    attempted_providers = entry.get("attempted_providers")
    if isinstance(attempted_providers, list):
        providers.update(str(provider).strip().lower() for provider in attempted_providers if str(provider).strip())
    attempted_provider = str(entry.get("attempted_provider", "")).strip().lower()
    if attempted_provider:
        providers.add(attempted_provider)
    return providers


def run_geocode_provider(provider_name: str, country: str, city: str, address: str) -> dict[str, Any]:
    if provider_name == "google":
        return google_geocode_query(country, city, address)
    if provider_name == "kakao":
        return kakao_geocode_query(country, city, address)
    if provider_name == "amap":
        return amap_geocode_query(country, city, address)
    raise RuntimeError(f"Unsupported geocode provider: {provider_name}")


def _normalize_korea_city_key(city: str) -> str:
    normalized = city.strip().lower()
    if normalized in {"seoul", "서울"}:
        return "seoul"
    if normalized in {"seongnam", "seongnam-si", "seongnam si", "성남", "성남시"}:
        return "seongnam"
    if normalized in {"gimpo", "gimpo-si", "gimpo si", "김포", "김포시"}:
        return "gimpo"
    if normalized in {"daejeon", "대전", "대전광역시"}:
        return "daejeon"
    return normalized


def _is_within_south_korea_bbox(lat: float, lng: float) -> bool:
    # Korea workbooks often use Seoul as the operating city while valid stops
    # can sit in nearby Gyeonggi/Incheon metro areas. Keep this as a country
    # sanity check instead of a city-level rejection gate.
    north, east, south, west = (38.70, 132.10, 33.00, 124.50)
    return south <= lat <= north and west <= lng <= east


def is_plausible_korea_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
    requested_address: str = "",
) -> bool:
    if not is_korea_country(country):
        return True
    return _is_within_south_korea_bbox(lat, lng)


def _is_within_bangkok_market_bbox(lat: float, lng: float) -> bool:
    # Keep validation aligned with the Bangkok/metro OSRM coverage used for the BK market.
    north, east, south, west = (14.8, 101.6, 12.4, 99.0)
    return south <= lat <= north and west <= lng <= east


def is_plausible_bangkok_market_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
    requested_address: str = "",
) -> bool:
    if not is_bangkok_market_country(country):
        return True
    return _is_within_bangkok_market_bbox(lat, lng)


def _is_within_china_city_bbox(city: str, lat: float, lng: float) -> bool:
    config = _china_city_config(city)
    if config is None:
        return True
    north, east, south, west = config["bbox"]
    return south <= lat <= north and west <= lng <= east


def _compact_china_location_text(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _is_china_city_only_result(config: dict[str, Any], formatted_address: str) -> bool:
    text = _compact_china_location_text(formatted_address)
    if not text:
        return False
    return text in {_compact_china_location_text(alias) for alias in config["aliases"]}


def _distinctive_address_tokens(requested_address: str) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(r"[A-Za-z0-9]+", str(requested_address or "")):
        token = match.group(0)
        has_alpha = any(char.isalpha() for char in token)
        has_digit = any(char.isdigit() for char in token)
        is_upper_brand = token.isupper() and len(token) >= 4
        if has_alpha and ((has_digit and len(token) >= 2) or is_upper_brand):
            normalized = token.lower()
            if normalized not in tokens:
                tokens.append(normalized)
    return tokens


def _preserves_distinctive_address_tokens(requested_address: str, candidate_text: str) -> bool:
    tokens = _distinctive_address_tokens(requested_address)
    if not tokens:
        return True
    normalized_candidate = str(candidate_text or "").lower()
    return all(token in normalized_candidate for token in tokens)


def is_plausible_china_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
    adcode: str = "",
    requested_address: str = "",
) -> bool:
    if not is_china_country(country):
        return True
    config = _china_city_config(city)
    if config is None:
        return True

    if _is_china_city_only_result(config, formatted_address):
        return False

    if not _preserves_distinctive_address_tokens(requested_address, formatted_address):
        return False

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
    requested_address: str = "",
) -> bool:
    if is_korea_country(country):
        return is_plausible_korea_geocode_result(country, city, lat, lng, formatted_address, requested_address)
    if is_bangkok_market_country(country):
        return is_plausible_bangkok_market_geocode_result(country, city, lat, lng, formatted_address, requested_address)
    if is_china_country(country):
        return is_plausible_china_geocode_result(
            country, city, lat, lng, formatted_address, adcode, requested_address
        )
    return True


def annotate_geocode_point(
    point: dict[str, Any],
    *,
    country: str,
    city: str,
    address: str,
    requested_city_param: str = "",
    validation_status: str = "ok",
) -> dict[str, Any]:
    enriched = dict(point)
    enriched["requested_country"] = country.strip()
    enriched["requested_city"] = city.strip()
    enriched["requested_address"] = address.strip()
    if requested_city_param:
        enriched["requested_city_param"] = requested_city_param
    enriched["validation_status"] = validation_status
    return enriched


def haversine_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _point_lat_lng(point: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(point.get("lat", 0.0) or 0.0)
        lng = float(point.get("lng", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if not lat or not lng:
        return None
    return lat, lng


def geocode_school_distance_km(school_point: dict[str, Any], point: dict[str, Any]) -> float | None:
    school_coords = _point_lat_lng(school_point)
    point_coords = _point_lat_lng(point)
    if school_coords is None or point_coords is None:
        return None
    return haversine_distance_km(school_coords[0], school_coords[1], point_coords[0], point_coords[1])


def build_school_distance_review_warning(
    school_point: dict[str, Any],
    point: dict[str, Any],
    *,
    threshold_km: float | None = None,
) -> dict[str, str] | None:
    threshold = GEOCODE_SCHOOL_DISTANCE_REVIEW_KM if threshold_km is None else threshold_km
    if threshold <= 0:
        return None
    distance_km = geocode_school_distance_km(school_point, point)
    if distance_km is None or distance_km <= threshold:
        return None
    distance_label = f"{distance_km:.1f}"
    threshold_label = f"{threshold:.0f}"
    return {
        "country": str(point.get("country", point.get("requested_country", ""))).strip(),
        "city": str(point.get("city", point.get("requested_city", ""))).strip(),
        "address": str(point.get("display_address") or point.get("address") or point.get("requested_address", "")).strip(),
        "source_excel_rows": str(point.get("source_excel_rows", "")).strip(),
        "status": "needs_review",
        "accepted": "true",
        "distance_to_school_km": distance_label,
        "warning": (
            f"Resolved coordinate is {distance_label} km from the school, above the {threshold_label} km review "
            "threshold. Check that the workbook city/province/country and the resolved address match the intended stop."
        ),
        "suggestion": (
            "If the resolved city or province is wrong, correct the workbook city/address and rerun geocoding. "
            "If the distance is expected, keep the result."
        ),
        "formatted_address": str(point.get("formatted_address", "")).strip(),
        "provider": str(point.get("provider", "")).strip(),
    }


def apply_school_distance_review(
    school_point: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    threshold_km: float | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("status", "ok")).strip().lower() not in {"", "ok"}:
            continue
        warning = build_school_distance_review_warning(school_point, row, threshold_km=threshold_km)
        if warning is None:
            continue
        row["geocode_status"] = "needs_review"
        row["distance_to_school_km"] = warning["distance_to_school_km"]
        existing_warning = str(row.get("warning", "")).strip()
        row["warning"] = f"{existing_warning} {warning['warning']}".strip()
        warnings.append(warning)
    return warnings


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


def amap_request_json(endpoint: str, params: dict[str, Any], limiter: RateLimiter) -> dict[str, Any]:
    limiter.wait()
    api_key = require_api_key("AMAP_API_KEY", AMAP_KEY, "AMap")
    response = requests.get(
        f"https://restapi.amap.com{endpoint}",
        params={**params, "key": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status")) != "1":
        info = payload.get("info", "UNKNOWN_ERROR")
        infocode = payload.get("infocode", "")
        raise RuntimeError(f"AMap request failed: {endpoint} -> {info} ({infocode})")
    return payload


def kakao_request_json(endpoint: str, params: dict[str, Any], limiter: RateLimiter) -> dict[str, Any]:
    last_error: Exception | None = None
    api_key = require_api_key("KAKAO_REST_API_KEY", KAKAO_REST_API_KEY, "Kakao")
    for attempt in range(1, KAKAO_REQUEST_MAX_RETRIES + 1):
        limiter.wait()
        try:
            response = requests.get(
                f"https://dapi.kakao.com{endpoint}",
                params=params,
                headers={"Authorization": f"KakaoAK {api_key}"},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt >= KAKAO_REQUEST_MAX_RETRIES:
                break
            time.sleep(0.6 * attempt)
    if last_error:
        raise last_error
    raise RuntimeError(f"Kakao request failed: {endpoint}")


def google_geocode_relay_request_json(country: str, params: dict[str, Any]) -> dict[str, Any]:
    relay_urls = google_geocode_relay_urls()
    if not relay_urls:
        raise RuntimeError("Google geocode relay URL is not configured. Set BRP_GOOGLE_GEOCODE_RELAY_URL.")
    if not GOOGLE_GEOCODE_RELAY_TOKEN:
        raise RuntimeError("Google geocode relay token is not configured. Set BRP_GOOGLE_GEOCODE_RELAY_TOKEN.")
    last_error = ""
    for relay_url in relay_urls:
        GOOGLE_GEOCODE_LIMITER.wait()
        try:
            response = requests.post(
                relay_url,
                json={"country": country, "params": params},
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {GOOGLE_GEOCODE_RELAY_TOKEN}",
                },
                timeout=GOOGLE_GEOCODE_RELAY_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as exc:
            last_error = exc.__class__.__name__
            continue
        try:
            payload = response.json()
        except Exception:
            payload = {"error": response.text[:200]}
        if response.status_code >= 400:
            error_message = payload.get("error") if isinstance(payload, dict) else ""
            details = f": {error_message}" if error_message else ""
            last_error = f"HTTP {response.status_code}{details}"
            continue
        if not isinstance(payload, dict):
            last_error = "non-object payload"
            continue
        return payload
    details = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Google geocode relay request failed for all configured relays{details}")


def google_geocode_relay_urls() -> list[str]:
    urls: list[str] = []
    for relay_url in (GOOGLE_GEOCODE_RELAY_URL, GOOGLE_GEOCODE_RELAY_FALLBACK_URL):
        if relay_url and relay_url not in urls:
            urls.append(relay_url)
    return urls


def google_geocode_request_json(params: dict[str, Any], country: str = "") -> dict[str, Any]:
    if use_google_geocode_relay(country):
        payload = google_geocode_relay_request_json(country, params)
    else:
        api_key = require_api_key("GOOGLE_GEOCODE_API_KEY", GOOGLE_GEOCODE_API_KEY, "Google Geocoding")
        GOOGLE_GEOCODE_LIMITER.wait()
        reserve_google_geocode_monthly_usage()
        response = requests.get(
            GOOGLE_GEOCODE_BASE_URL,
            params={**params, "key": api_key},
            timeout=GOOGLE_GEOCODE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    status = str(payload.get("status", "")).strip().upper()
    if status in {"OK", "ZERO_RESULTS"}:
        return payload
    error_message = str(payload.get("error_message", "")).strip()
    details = f": {error_message}" if error_message else ""
    raise RuntimeError(f"Google geocode request failed: {status}{details}")


def google_country_code(country: str) -> str:
    normalized = " ".join(country.strip().lower().split())
    values = {
        "korea": "KR",
        "south korea": "KR",
        "republic of korea": "KR",
        "대한민국": "KR",
        "한국": "KR",
        "bangkok": "TH",
        "bk": "TH",
        "bangkok market": "TH",
        "thailand": "TH",
        "thai": "TH",
        "th": "TH",
        "ประเทศไทย": "TH",
        "ไทย": "TH",
        "united states": "US",
        "usa": "US",
        "us": "US",
        "united kingdom": "GB",
        "uk": "GB",
        "great britain": "GB",
        "japan": "JP",
        "singapore": "SG",
        "malaysia": "MY",
        "vietnam": "VN",
        "china": "CN",
        "中国": "CN",
        "中华人民共和国": "CN",
    }
    return values.get(normalized, "")


def google_geocode_params(country: str, query: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "address": query,
        "language": "en",
    }
    country_code = google_country_code(country)
    if country_code:
        params["components"] = f"country:{country_code}"
        params["region"] = country_code.lower()
    return params


def geocode_cache_key(country: str, city: str, address: str) -> str:
    normalized_country = canonical_cache_country(country)
    normalized_city = canonical_cache_city(country, city)
    normalized_address = canonical_cache_address(address)
    return f"{normalized_country}|{normalized_city}|{normalized_address}"


def canonical_cache_country(country: str) -> str:
    normalized = country.strip()
    if not normalized:
        return ""
    if is_korea_country(normalized):
        return "South Korea"
    if is_bangkok_market_country(normalized):
        return "Bangkok"
    if normalized.strip().lower() in {"china", "中国", "中华人民共和国"}:
        return "China"
    return normalized


def canonical_cache_city(country: str, city: str) -> str:
    normalized = city.strip()
    if not normalized:
        return ""
    if is_korea_country(country):
        city_key = _normalize_korea_city_key(normalized)
        if city_key == "seoul":
            return "Seoul"
        if city_key == "seongnam":
            return "Seongnam"
        if city_key == "daejeon":
            return "Daejeon"
    if is_china_country(country):
        config = _china_city_config(normalized)
        if config:
            return str(config["canonical"])
    if is_bangkok_market_country(country):
        config = _bangkok_market_city_config(normalized)
        if config:
            return str(config["canonical"])
    return normalized


def canonical_cache_address(address: str) -> str:
    return " ".join(str(address).strip().split())


def geocode_cache_lookup_keys(country: str, city: str, address: str) -> list[str]:
    raw_country = str(country).strip()
    raw_city = str(city).strip()
    raw_address = str(address).strip()
    normalized_country = canonical_cache_country(raw_country)
    normalized_city = canonical_cache_city(raw_country, raw_city)
    normalized_address = canonical_cache_address(raw_address)

    candidates = [
        f"{raw_country}|{raw_city}|{raw_address}",
        f"{normalized_country}|{normalized_city}|{normalized_address}",
    ]

    if raw_country or raw_address:
        candidates.append(f"{normalized_country}|{raw_city}|{normalized_address}")
        candidates.append(f"{raw_country}|{normalized_city}|{normalized_address}")

    if is_korea_country(raw_country):
        korea_city_variants = [raw_city, normalized_city, ""]
        for city_variant in korea_city_variants:
            candidates.append(f"{normalized_country}|{city_variant.strip()}|{normalized_address}")
            candidates.append(f"{raw_country}|{city_variant.strip()}|{raw_address}")

    if is_bangkok_market_country(raw_country):
        bangkok_city_variants = [raw_city, normalized_city, ""]
        for city_variant in bangkok_city_variants:
            candidates.append(f"{normalized_country}|{city_variant.strip()}|{normalized_address}")
            candidates.append(f"{raw_country}|{city_variant.strip()}|{raw_address}")

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def _format_source_excel_rows(source_excel_rows: list[int] | None) -> str:
    rows = sorted({int(row) for row in list(source_excel_rows or []) if int(row) > 0})
    return ", ".join(str(row) for row in rows)


def build_geocode_warning(
    country: str,
    city: str,
    address: str,
    source_excel_rows: list[int] | None = None,
    reason: str = "",
) -> dict[str, str]:
    return {
        "country": country,
        "city": city,
        "address": address,
        "source_excel_rows": _format_source_excel_rows(source_excel_rows),
        "warning": reason.strip() or "This address could not be resolved to coordinates from the original user input.",
        "suggestion": (
            "Review the source address manually. Use a complete postal-style address with city, district, road name, "
            "and building number. Avoid route labels, landmarks only, shorthand notes, or pickup/dropoff annotations."
        ),
    }


def is_failed_geocode_cache_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and str(entry.get("cache_status", "")).strip().lower() == "failed"


def resolve_geocoded_point(
    country: str,
    city: str,
    address: str,
    source_excel_rows: list[int] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None, bool]:
    cache_key = geocode_cache_key(country, city, address)
    cached = None
    matched_cache_key = None
    for lookup_key in geocode_cache_lookup_keys(country, city, address):
        candidate = GEOCODE_CACHE.get(lookup_key)
        if candidate is not None:
            cached = candidate
            matched_cache_key = lookup_key
            break
    provider_names = geocode_provider_order(country, address)
    cache_changed = False
    if cached:
        if is_failed_geocode_cache_entry(cached):
            if is_korea_country(country):
                if matched_cache_key:
                    GEOCODE_CACHE.pop(matched_cache_key, None)
                    cache_changed = True
                if matched_cache_key != cache_key:
                    GEOCODE_CACHE.pop(cache_key, None)
                    cache_changed = True
            else:
                attempted_providers = failed_geocode_cache_providers(cached)
                if attempted_providers.issuperset(set(provider_names)):
                    if matched_cache_key and matched_cache_key != cache_key:
                        GEOCODE_CACHE[cache_key] = dict(cached)
                        return None, build_geocode_warning(country, city, address, source_excel_rows), True
                    return None, build_geocode_warning(country, city, address, source_excel_rows), False
        elif str(cached.get("provider", "")).strip().lower() in set(provider_names):
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
                requested_address=address,
            ):
                enriched_cached = annotate_geocode_point(
                    dict(cached),
                    country=country,
                    city=city,
                    address=address,
                    validation_status=str(cached.get("validation_status", "") or "ok"),
                )
                if matched_cache_key and matched_cache_key != cache_key:
                    GEOCODE_CACHE[cache_key] = enriched_cached
                    return dict(enriched_cached), None, True
                if enriched_cached != cached:
                    GEOCODE_CACHE[cache_key] = enriched_cached
                    return dict(enriched_cached), None, True
                return dict(enriched_cached), None, False
            if matched_cache_key:
                GEOCODE_CACHE.pop(matched_cache_key, None)
                cache_changed = True
            if matched_cache_key != cache_key:
                GEOCODE_CACHE.pop(cache_key, None)
                cache_changed = True

    errors: list[str] = []
    attempted_providers: list[str] = []
    for provider_name in provider_names:
        attempted_providers.append(provider_name)
        try:
            point = run_geocode_provider(provider_name, country, city, address)
            point["geocode_provider_chain"] = " -> ".join(attempted_providers)
            GEOCODE_CACHE[cache_key] = point
            if len(attempted_providers) > 1:
                log(f"[INFO] geocode fallback succeeded: {address} via {point.get('geocode_provider_chain')}")
            return dict(point), None, True
        except Exception as exc:
            error_text = f"{provider_name}: {exc}"
            errors.append(error_text)
            log(f"[WARN] geocode provider failed: {address} -> {error_text}")

    reason = "; ".join(errors) or "No geocode provider returned a usable coordinate."
    log(f"[WARN] geocode failed: {address} -> {reason}")
    GEOCODE_CACHE[cache_key] = {
        "cache_status": "failed",
        "attempted_provider": attempted_providers[-1] if attempted_providers else "",
        "attempted_providers": attempted_providers,
        "country": country,
        "city": city,
        "address": address,
        "error": reason,
    }
    return None, build_geocode_warning(country, city, address, source_excel_rows, reason=reason), True


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
                if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, adcode, requested_address=address):
                    last_error = RuntimeError(
                        f"AMap geocode returned result outside {city.strip()}: {formatted_address}"
                    )
                    continue
                plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
                return annotate_geocode_point(
                    {
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
                    },
                    country=country,
                    city=city,
                    address=address,
                    requested_city_param=amap_city,
                )
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
            candidate_address = str(candidate.get("address", "") or "").strip()
            candidate_name = str(candidate.get("name", "") or "").strip()
            formatted_parts = [
                str(candidate.get("pname", "") or "").strip(),
                str(candidate.get("cityname", "") or "").strip(),
                str(candidate.get("adname", "") or "").strip(),
                candidate_address,
                "" if candidate_name == candidate_address else candidate_name,
                address.strip() if not candidate_address and not candidate_name else "",
            ]
            formatted_address = "".join(part for index, part in enumerate(formatted_parts) if part and part not in formatted_parts[:index])
            adcode = str(candidate.get("adcode", "") or "").strip()
            if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, adcode, requested_address=address):
                last_error = RuntimeError(f"AMap place search returned result outside {city.strip()}: {formatted_address}")
                continue
            plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
            return annotate_geocode_point(
                {
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
                },
                country=country,
                city=city,
                address=address,
                requested_city_param=amap_city,
            )
    except Exception as exc:
        last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"AMap geocode failed for {address.strip()}")


def kakao_geocode_query(country: str, city: str, address: str) -> dict[str, Any]:
    queries = build_geocode_queries(country, city, address)

    last_error = None
    for query in queries:
        try:
            payload = kakao_request_json(
                "/v2/local/search/address.json",
                {"query": query},
                KAKAO_GEOCODE_LIMITER,
            )
            documents = payload.get("documents") or []
            if not documents:
                continue
            first = documents[0]
            lng = float(first["x"])
            lat = float(first["y"])
            formatted_address = (
                ((first.get("road_address") or {}).get("address_name"))
                or ((first.get("address") or {}).get("address_name"))
                or first.get("address_name")
                or address.strip()
            )
            if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, requested_address=address):
                continue
            return annotate_geocode_point(
                {
                    "provider": "kakao",
                    "address": address.strip(),
                    "city": city.strip(),
                    "country": country.strip(),
                    "lat": lat,
                    "lng": lng,
                    "plot_lat": lat,
                    "plot_lng": lng,
                    "formatted_address": formatted_address,
                },
                country=country,
                city=city,
                address=address,
            )
        except Exception as exc:
            last_error = exc

    for query in queries:
        try:
            payload = kakao_request_json(
                "/v2/local/search/keyword.json",
                {"query": query},
                KAKAO_GEOCODE_LIMITER,
            )
            documents = payload.get("documents") or []
            if not documents:
                continue
            first = documents[0]
            lng = float(first["x"])
            lat = float(first["y"])
            formatted_address = (
                first.get("road_address_name")
                or first.get("address_name")
                or first.get("place_name")
                or address.strip()
            )
            if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, requested_address=address):
                continue
            return annotate_geocode_point(
                {
                    "provider": "kakao",
                    "address": address.strip(),
                    "city": city.strip(),
                    "country": country.strip(),
                    "lat": lat,
                    "lng": lng,
                    "plot_lat": lat,
                    "plot_lng": lng,
                    "formatted_address": formatted_address,
                },
                country=country,
                city=city,
                address=address,
            )
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"Kakao geocode failed for {address.strip()}")


def google_geocode_query(country: str, city: str, address: str) -> dict[str, Any]:
    queries = build_geocode_queries(country, city, address)
    last_error: Exception | None = None

    for query in queries:
        try:
            payload = google_geocode_request_json(google_geocode_params(country, query), country=country)
            results = payload.get("results") or []
            if not results:
                continue
            first = results[0]
            location = (first.get("geometry") or {}).get("location") or {}
            lat = float(location["lat"])
            lng = float(location["lng"])
            formatted_address = str(first.get("formatted_address") or address.strip()).strip()
            if not is_plausible_geocode_result(country, city, lat, lng, formatted_address, requested_address=address):
                continue
            return annotate_geocode_point(
                {
                    "provider": "google",
                    "address": address.strip(),
                    "city": city.strip(),
                    "country": country.strip(),
                    "lat": lat,
                    "lng": lng,
                    "plot_lat": lat,
                    "plot_lng": lng,
                    "formatted_address": formatted_address,
                },
                country=country,
                city=city,
                address=address,
            )
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"Google geocode failed for {address.strip()}")


def geocode_records(input_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    points: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    changed = False
    unique_records: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for item in input_records:
        country = str(item.get("country", "")).strip()
        city = str(item.get("city", "")).strip()
        address = str(item.get("address", "")).strip()
        cache_key = geocode_cache_key(country, city, address)
        source_excel_row = item.get("source_excel_row")
        if cache_key not in unique_records:
            unique_records[cache_key] = {
                "country": country,
                "city": city,
                "address": address,
                "source_excel_rows": [],
            }
            ordered_keys.append(cache_key)
        if source_excel_row not in (None, "", 0):
            unique_records[cache_key]["source_excel_rows"].append(int(source_excel_row))

    resolved_points: dict[str, dict[str, Any]] = {}
    failed_keys: set[str] = set()
    for cache_key in ordered_keys:
        record = unique_records[cache_key]
        point, warning, cache_changed = resolve_geocoded_point(
            record["country"],
            record["city"],
            record["address"],
            record.get("source_excel_rows"),
        )
        changed = changed or cache_changed
        if point is not None:
            resolved_points[cache_key] = point
            continue
        failed_keys.add(cache_key)
        if warning is not None:
            warnings.append(warning)

    for index, item in enumerate(input_records):
        country = str(item.get("country", "")).strip()
        city = str(item.get("city", "")).strip()
        address = str(item.get("address", "")).strip()
        passenger_count = int(item.get("passenger_count", 0 if index == 0 else 1))
        cache_key = geocode_cache_key(country, city, address)
        if cache_key in failed_keys:
            continue
        point = resolved_points.get(cache_key)
        if point is None:
            continue
        point = dict(point)
        point["node_id"] = len(points)
        point["passenger_count"] = passenger_count
        point["original_members"] = [address]
        point["display_address"] = address
        if item.get("source_excel_row") not in (None, "", 0):
            point["source_excel_rows"] = str(int(item.get("source_excel_row") or 0))
        point["is_depot"] = len(points) == 0
        points.append(point)
    if points:
        warnings.extend(apply_school_distance_review(points[0], points[1:]))
    if changed:
        save_json_cache(GEOCODE_CACHE_PATH, GEOCODE_CACHE)
    if not points:
        details = []
        for warning in warnings[:20]:
            address = str(warning.get("address", "")).strip()
            reason = str(warning.get("warning", "") or warning.get("reason", "")).strip()
            if address and reason:
                details.append(f"- {address}: {reason}")
            elif address:
                details.append(f"- {address}")
        suffix = "\n\nGeocode failures:\n" + "\n".join(details) if details else ""
        raise RuntimeError(f"No valid stops were accepted as valid input stops.{suffix}")
    return points, warnings


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
    traffic_profile_name: str = "Off-Peak",
    annotation_route_duration_seconds: int = ANNOTATION_ROUTE_DURATION_SECONDS,
    service_direction: str = "From School",
    route_palette: list[str] | None = None,
    outlying_private_access_rows: list[dict[str, Any]] | None = None,
    private_access_mode: str = "private_drive_stop",
) -> str:
    traffic_note = f"{traffic_profile_name} profile using unscaled OSRM candidate time"
    palette = list(route_palette or route_colors(len(routes)))
    normalized_direction = "To School" if str(service_direction).strip() == "To School" else "From School"
    endpoint_note = (
        "The final address in each route is the school / destination."
        if normalized_direction == "To School"
        else "The first address is used as the departure point."
    )
    lines = [
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.45;'>",
        "<h3 style='margin:0 0 8px 0;'>Bus Route Summary</h3>",
        f"<p style='margin:0 0 6px 0;'><b>Service direction:</b> {normalized_direction}</p>"
        f"<p style='margin:0 0 12px 0;'>{endpoint_note} "
        f"Routes are estimated under the {traffic_note}, one-way only, and the map marks the farthest stop "
        f"reachable within {annotation_route_duration_seconds // 60} minutes.</p>",
    ]
    private_access_by_route: dict[str, list[dict[str, Any]]] = {}
    private_access_by_pickup: dict[str, list[dict[str, Any]]] = {}
    for item in list(outlying_private_access_rows or []):
        access_type = str(item.get("private_access_type", "clustered_rider")).strip() or "clustered_rider"
        if access_type == "private_drive_stop":
            route_id = str(item.get("pickup_route_id", "")).strip()
            if route_id:
                private_access_by_route.setdefault(route_id, []).append(item)
        else:
            pickup_address = str(item.get("pickup_address", "")).strip()
            if pickup_address:
                private_access_by_pickup.setdefault(pickup_address, []).append(item)
    for route_index, route in enumerate(routes):
        color = palette[route_index % len(palette)] if palette else "#2563eb"
        route_label = str(route.get("route_id", "")).strip() or f"Bus {route['vehicle_id']}"
        lines.extend(
            [
                (
                    "<h4 style='margin:12px 0 4px 0;display:flex;align-items:center;gap:8px;'>"
                    f"<span style='display:inline-block;width:12px;height:12px;border-radius:999px;background:{color};"
                    "border:1px solid rgba(17,24,39,0.22);flex:0 0 auto;'></span>"
                    f"<span>{html.escape(route_label)}</span>"
                    "</h4>"
                ),
                f"<div>Vehicle type: {route['bus_type_name']}</div>",
                f"<div>Passengers: {route['load']} / {route['bus_capacity']} seats</div>",
                f"<div>Estimated time: {seconds_to_human(route['time_s'])}</div>",
                f"<div>Estimated distance: {route['distance_m']/1000.0:.1f} km</div>",
            ]
        )
        if route.get("limit_stop_order") is not None:
            lines.append(
                f"<div><b>{annotation_route_duration_seconds // 60}-minute mark:</b> Stop {route['limit_stop_order']} "
                f"({seconds_to_human(route['limit_stop_elapsed_s'])})</div>"
            )
        lines.append("<ol style='padding-left:18px;'>")
        final_index = max(0, len(route["nodes"]) - 1)
        for order, node in enumerate(route["nodes"]):
            prefix = (
                "Start"
                if order == 0
                else "School" if normalized_direction == "To School" and order == final_index
                else f"Stop {order}"
            )
            lines.append(f"<li>{prefix}: {points[node]['address']}</li>")
        lines.append("</ol>")
        route_private_drive_stops = list(private_access_by_route.get(route_label) or [])
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
    traffic_profile_name: str = "Off-Peak",
    annotation_route_duration_seconds: int = ANNOTATION_ROUTE_DURATION_SECONDS,
    service_direction: str = "From School",
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
        base_popup = f"{html.escape(address)}"
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
        route_label = str(route.get("route_id", "")).strip() or f"Bus {route['vehicle_id']}"
        geometry: list[tuple[float, float]] = []
        for leg_detail in route.get("leg_details", []):
            leg = leg_detail["geometry"]
            geometry.extend(leg if not geometry else leg[1:])
        if len(geometry) < 2:
            geometry = [
                (points[node]["plot_lat"], points[node]["plot_lng"])
                for node in list(route.get("nodes") or [])
                if 0 <= int(node) < len(points)
            ]
        if len(geometry) >= 2:
            folium.PolyLine(geometry, color="#ffffff", weight=9, opacity=0.9).add_to(fmap)
            folium.PolyLine(
                geometry,
                color=color,
                weight=6,
                opacity=0.9,
                tooltip=f"{route_label} | {route['bus_type_name']} | {route['load']}/{route['bus_capacity']}",
            ).add_to(fmap)
        for order, node in enumerate(route["nodes"]):
            point = points[node]
            final_index = max(0, len(route["nodes"]) - 1)
            point_label = (
                "Start"
                if order == 0
                else "School" if str(service_direction).strip() == "To School" and order == final_index
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
                tooltip=f"{annotation_route_duration_seconds // 60}-minute mark for {route_label}: {point['address']}",
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
        f"{build_map_summary_html(points, routes, traffic_profile_name=traffic_profile_name, annotation_route_duration_seconds=annotation_route_duration_seconds, service_direction=service_direction, route_palette=colors, outlying_private_access_rows=outlying_private_access_rows, private_access_mode=private_access_mode)}</div>"
    )
    fmap.get_root().html.add_child(Element(panel))
    folium.LayerControl().add_to(fmap)
    fmap.save(str(output_path))
