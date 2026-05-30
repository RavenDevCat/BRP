from __future__ import annotations

import colorsys
from contextlib import contextmanager
from datetime import datetime
import html
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import folium
import requests
from folium import Element


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
GEOCODE_CACHE_PATH = CACHE_DIR / "geocode_cache.json"
SUBWAY_CACHE_PATH = CACHE_DIR / "subway_search_cache.json"
GOOGLE_GEOCODE_USAGE_PATH = CACHE_DIR / "google_geocode_usage.json"

AMAP_KEY = os.environ.get("AMAP_API_KEY", "").strip()
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "").strip()
GOOGLE_GEOCODE_API_KEY = os.environ.get("GOOGLE_GEOCODE_API_KEY", "").strip()
AMAP_GEOCODE_MAX_QPS = 2.8
AMAP_PLACES_MAX_QPS = 2.8
KAKAO_GEOCODE_MAX_QPS = 2.8
KAKAO_PLACES_MAX_QPS = 2.8
GOOGLE_GEOCODE_MAX_QPS = 2.8
GOOGLE_GEOCODE_MONTHLY_LIMIT = 10_000
REQUEST_TIMEOUT = 20
KAKAO_REQUEST_MAX_RETRIES = 3

SUBWAY_SEARCH_RADIUS_M = 1500
MAX_SUBWAY_WALK_DISTANCE_M = 800
ANNOTATION_ROUTE_DURATION_SECONDS = 60 * 60
NEARBY_CLUSTER_RADIUS_M = 500


class RateLimiter:
    def __init__(self, max_qps: float) -> None:
        self.min_interval = 1.0 / max_qps
        self.lock = threading.Lock()
        self.next_allowed_time = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_allowed_time:
                time.sleep(self.next_allowed_time - now)
            self.next_allowed_time = time.monotonic() + self.min_interval


AMAP_GEOCODE_LIMITER = RateLimiter(AMAP_GEOCODE_MAX_QPS)
AMAP_PLACES_LIMITER = RateLimiter(AMAP_PLACES_MAX_QPS)
KAKAO_GEOCODE_LIMITER = RateLimiter(KAKAO_GEOCODE_MAX_QPS)
KAKAO_PLACES_LIMITER = RateLimiter(KAKAO_PLACES_MAX_QPS)
GOOGLE_GEOCODE_LIMITER = RateLimiter(GOOGLE_GEOCODE_MAX_QPS)
CACHE_FILE_LOCKS: dict[Path, threading.Lock] = {}


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with lock:
        temp_path.write_text(body, encoding="utf-8")
        temp_path.replace(path)


@contextmanager
def cross_process_file_lock(path: Path):
    ensure_cache_dir()
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
SUBWAY_CACHE = load_json_cache(SUBWAY_CACHE_PATH)
GOOGLE_GEOCODE_USAGE = _load_google_geocode_usage_payload()


def log(message: str) -> None:
    print(message, flush=True)


def require_api_key(env_name: str, value: str, provider_label: str) -> str:
    if value:
        return value
    raise RuntimeError(
        f"{provider_label} API key is not configured. "
        f"Set environment variable `{env_name}` before running this workflow."
    )


def current_google_usage_month_key() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")


def read_google_geocode_monthly_usage(month_key: str | None = None) -> int:
    usage_month_key = month_key or current_google_usage_month_key()
    with cross_process_file_lock(GOOGLE_GEOCODE_USAGE_PATH):
        payload = _load_google_geocode_usage_payload()
        _refresh_google_geocode_usage(payload)
        return _coerce_google_geocode_usage_value(payload, usage_month_key)


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
    with cross_process_file_lock(GOOGLE_GEOCODE_USAGE_PATH):
        payload = _load_google_geocode_usage_payload()
        used = _coerce_google_geocode_usage_value(payload, month_key)
        if used >= GOOGLE_GEOCODE_MONTHLY_LIMIT:
            raise RuntimeError(
                "Google geocoding monthly usage limit reached. "
                f"Usage is capped at {GOOGLE_GEOCODE_MONTHLY_LIMIT:,} requests per month."
            )
        payload[month_key] = used + 1
        save_json_cache(GOOGLE_GEOCODE_USAGE_PATH, payload)
        _refresh_google_geocode_usage(payload)
    return month_key


def increment_google_geocode_monthly_usage(month_key: str) -> None:
    with cross_process_file_lock(GOOGLE_GEOCODE_USAGE_PATH):
        payload = _load_google_geocode_usage_payload()
        payload[month_key] = _coerce_google_geocode_usage_value(payload, month_key) + 1
        save_json_cache(GOOGLE_GEOCODE_USAGE_PATH, payload)
        _refresh_google_geocode_usage(payload)


def determine_currency_code(input_records: list[dict[str, Any]]) -> str:
    countries = " ".join(str(item.get("country", "")).strip().lower() for item in input_records)
    if any(token in countries for token in ("korea", "south korea", "대한민국", "한국")):
        return "KRW"
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
    if is_korea_country(country):
        return "google" if is_likely_english_korean_address(country, address) else "kakao"
    return "amap"


def _normalize_korea_city_key(city: str) -> str:
    normalized = city.strip().lower()
    if normalized in {"seoul", "서울"}:
        return "seoul"
    if normalized in {"seongnam", "seongnam-si", "seongnam si", "성남", "성남시"}:
        return "seongnam"
    if normalized in {"daejeon", "대전", "대전광역시"}:
        return "daejeon"
    return normalized


def _korea_city_aliases(city: str) -> list[str]:
    city_key = _normalize_korea_city_key(city)
    aliases = {
        "seoul": ["seoul", "서울", "seoul-si", "seoul special city"],
        "seongnam": ["seongnam", "seongnam-si", "성남", "성남시"],
        "daejeon": ["daejeon", "대전", "대전광역시", "daejeon-si", "daejeon metropolitan city"],
    }
    values = aliases.get(city_key, [city.strip()])
    return [item.strip().lower() for item in values if item.strip()]


def _is_within_korea_city_bbox(city: str, lat: float, lng: float) -> bool:
    city_key = _normalize_korea_city_key(city)
    bboxes = {
        # Broad Seoul metro-safe bounds, intentionally including Seongnam fringe.
        "seoul": (37.72, 127.30, 37.35, 126.75),
        "seongnam": (37.50, 127.20, 37.32, 126.95),
        "daejeon": (36.45, 127.55, 36.20, 127.25),
    }
    bbox = bboxes.get(city_key)
    if bbox is None:
        return True
    north, east, south, west = bbox
    return south <= lat <= north and west <= lng <= east


def is_plausible_korea_geocode_result(
    country: str,
    city: str,
    lat: float,
    lng: float,
    formatted_address: str,
) -> bool:
    if not is_korea_country(country):
        return True
    normalized_city = str(city).strip()
    if not normalized_city:
        return True

    address_text = formatted_address.strip().lower()
    city_aliases = _korea_city_aliases(normalized_city)
    if any(alias in address_text for alias in city_aliases):
        return True
    return _is_within_korea_city_bbox(normalized_city, lat, lng)


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


def google_geocode_request_json(params: dict[str, Any]) -> dict[str, Any]:
    api_key = require_api_key("GOOGLE_GEOCODE_API_KEY", GOOGLE_GEOCODE_API_KEY, "Google Geocoding")
    GOOGLE_GEOCODE_LIMITER.wait()
    reserve_google_geocode_monthly_usage()
    response = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={**params, "key": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    status = str(payload.get("status", "")).strip().upper()
    if status in {"OK", "ZERO_RESULTS"}:
        return payload
    error_message = str(payload.get("error_message", "")).strip()
    details = f": {error_message}" if error_message else ""
    raise RuntimeError(f"Google geocode request failed: {status}{details}")


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
    china_aliases = {
        "shanghai": "Shanghai",
        "上海": "Shanghai",
        "beijing": "Beijing",
        "北京": "Beijing",
        "suzhou": "Suzhou",
        "苏州": "Suzhou",
        "xian": "Xian",
        "xi'an": "Xian",
        "xi an": "Xian",
        "西安": "Xian",
    }
    return china_aliases.get(normalized.strip().lower(), normalized)


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
        korea_city_variants = [raw_city, normalized_city, "Seoul", "Seongnam", ""]
        for city_variant in korea_city_variants:
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
    provider_name = expected_geocode_provider(country, address)
    if cached:
        if is_failed_geocode_cache_entry(cached):
            attempted_provider = str(cached.get("attempted_provider", "")).strip().lower()
            if attempted_provider == provider_name:
                if matched_cache_key and matched_cache_key != cache_key:
                    GEOCODE_CACHE[cache_key] = dict(cached)
                    return None, build_geocode_warning(country, city, address, source_excel_rows), True
                return None, build_geocode_warning(country, city, address, source_excel_rows), False
        elif str(cached.get("provider", "")).strip().lower() == provider_name:
            cached_lat = float(cached.get("lat", 0.0) or 0.0)
            cached_lng = float(cached.get("lng", 0.0) or 0.0)
            cached_formatted_address = str(cached.get("formatted_address", "") or address).strip()
            if is_plausible_korea_geocode_result(country, city, cached_lat, cached_lng, cached_formatted_address):
                if matched_cache_key and matched_cache_key != cache_key:
                    GEOCODE_CACHE[cache_key] = dict(cached)
                    return dict(cached), None, True
                return dict(cached), None, False

    try:
        if provider_name == "google":
            point = google_geocode_query(country, city, address)
        elif provider_name == "kakao":
            point = kakao_geocode_query(country, city, address)
        else:
            point = amap_geocode_query(country, city, address)
        GEOCODE_CACHE[cache_key] = point
        return dict(point), None, True
    except Exception as exc:
        log(f"[WARN] geocode failed: {address} -> {exc}")
        GEOCODE_CACHE[cache_key] = {
            "cache_status": "failed",
            "attempted_provider": provider_name,
            "country": country,
            "city": city,
            "address": address,
        }
        return None, build_geocode_warning(country, city, address, source_excel_rows, reason=str(exc)), True


def amap_geocode_query(country: str, city: str, address: str) -> dict[str, Any]:
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
            if city.strip():
                params["city"] = city.strip()
            payload = amap_request_json("/v3/geocode/geo", params, AMAP_GEOCODE_LIMITER)
            geocodes = payload.get("geocodes") or []
            if geocodes:
                first = geocodes[0]
                lng_str, lat_str = str(first["location"]).split(",")
                lat = float(lat_str)
                lng = float(lng_str)
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
                    "formatted_address": first.get("formatted_address") or address.strip(),
                }
        except Exception as exc:
            last_error = exc

    try:
        payload = amap_request_json(
            "/v3/place/text",
            {
                "keywords": address.strip(),
                "city": city.strip(),
                "citylimit": "true" if city.strip() else "false",
                "offset": 10,
                "page": 1,
            },
            AMAP_GEOCODE_LIMITER,
        )
        pois = payload.get("pois") or []
        if pois:
            first = pois[0]
            lng_str, lat_str = str(first["location"]).split(",")
            lat = float(lat_str)
            lng = float(lng_str)
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
                "formatted_address": first.get("address") or address.strip(),
            }
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
            if not is_plausible_korea_geocode_result(country, city, lat, lng, formatted_address):
                continue
            return {
                "provider": "kakao",
                "address": address.strip(),
                "city": city.strip(),
                "country": country.strip(),
                "lat": lat,
                "lng": lng,
                "plot_lat": lat,
                "plot_lng": lng,
                "formatted_address": formatted_address,
            }
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
            if not is_plausible_korea_geocode_result(country, city, lat, lng, formatted_address):
                continue
            return {
                "provider": "kakao",
                "address": address.strip(),
                "city": city.strip(),
                "country": country.strip(),
                "lat": lat,
                "lng": lng,
                "plot_lat": lat,
                "plot_lng": lng,
                "formatted_address": formatted_address,
            }
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
            payload = google_geocode_request_json(
                {
                    "address": query,
                    "components": "country:KR",
                    "region": "kr",
                    "language": "en",
                }
            )
            results = payload.get("results") or []
            if not results:
                continue
            first = results[0]
            location = (first.get("geometry") or {}).get("location") or {}
            lat = float(location["lat"])
            lng = float(location["lng"])
            formatted_address = str(first.get("formatted_address") or address.strip()).strip()
            if not is_plausible_korea_geocode_result(country, city, lat, lng, formatted_address):
                continue
            return {
                "provider": "google",
                "address": address.strip(),
                "city": city.strip(),
                "country": country.strip(),
                "lat": lat,
                "lng": lng,
                "plot_lat": lat,
                "plot_lng": lng,
                "formatted_address": formatted_address,
            }
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
        point["is_depot"] = len(points) == 0
        points.append(point)
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


def subway_cache_key(point: dict[str, Any], radius_m: int, walk_distance_m: int) -> str:
    return f"{point['lat']:.6f},{point['lng']:.6f}|{radius_m}|{walk_distance_m}"


def find_nearby_subway_station(
    point: dict[str, Any],
    radius_m: int = SUBWAY_SEARCH_RADIUS_M,
    walk_distance_m: int = MAX_SUBWAY_WALK_DISTANCE_M,
) -> dict[str, Any] | None:
    cache_key = subway_cache_key(point, radius_m, walk_distance_m)
    if cache_key in SUBWAY_CACHE:
        return SUBWAY_CACHE[cache_key]
    try:
        if is_korea_country(str(point.get("country", ""))):
            payload = kakao_request_json(
                "/v2/local/search/category.json",
                {
                    "category_group_code": "SW8",
                    "x": str(point["lng"]),
                    "y": str(point["lat"]),
                    "radius": radius_m,
                    "sort": "distance",
                    "page": 1,
                    "size": 15,
                },
                KAKAO_PLACES_LIMITER,
            )
            for poi in payload.get("documents") or []:
                distance = int(float(poi.get("distance", 0) or 0))
                if distance > walk_distance_m:
                    continue
                lng = float(poi["x"])
                lat = float(poi["y"])
                result = {
                    "provider": "kakao",
                    "name": poi.get("place_name") or "Nearby Subway Station",
                    "station_id": poi.get("id") or poi.get("place_name") or f"{lat:.6f},{lng:.6f}",
                    "lat": lat,
                    "lng": lng,
                    "plot_lat": lat,
                    "plot_lng": lng,
                    "walking_distance_m": distance,
                }
                SUBWAY_CACHE[cache_key] = result
                save_json_cache(SUBWAY_CACHE_PATH, SUBWAY_CACHE)
                return result
        else:
            payload = amap_request_json(
                "/v3/place/around",
                {
                    "location": f"{point['lng']},{point['lat']}",
                    "keywords": "地铁站",
                    "radius": radius_m,
                    "sortrule": "distance",
                    "offset": 10,
                    "page": 1,
                },
                AMAP_PLACES_LIMITER,
            )
            for poi in payload.get("pois") or []:
                distance = int(float(poi.get("distance", 0) or 0))
                if distance > walk_distance_m:
                    continue
                lng_str, lat_str = str(poi["location"]).split(",")
                lat = float(lat_str)
                lng = float(lng_str)
                plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
                result = {
                    "provider": "amap",
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


def build_subway_aggregated_points(
    points: list[dict[str, Any]],
    radius_m: int = SUBWAY_SEARCH_RADIUS_M,
    walk_distance_m: int = MAX_SUBWAY_WALK_DISTANCE_M,
) -> list[dict[str, Any]]:
    if not points:
        return []
    depot = dict(points[0])
    depot["node_id"] = 0
    aggregated: list[dict[str, Any]] = [depot]
    groups: dict[str, dict[str, Any]] = {}
    for point in points[1:]:
        station = find_nearby_subway_station(point, radius_m=radius_m, walk_distance_m=walk_distance_m)
        if station is None:
            standalone = dict(point)
            standalone["node_id"] = len(aggregated)
            aggregated.append(standalone)
            continue
        key = str(station["station_id"])
        if key not in groups:
            groups[key] = {
                "provider": station.get("provider", point.get("provider", "map")),
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


def build_nearby_aggregated_points(
    points: list[dict[str, Any]],
    cluster_radius_m: int = NEARBY_CLUSTER_RADIUS_M,
) -> list[dict[str, Any]]:
    if not points:
        return []
    depot = dict(points[0])
    depot["node_id"] = 0
    aggregated: list[dict[str, Any]] = [depot]
    remaining = points[1:]
    used = [False] * len(remaining)
    radius_km = cluster_radius_m / 1000.0
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
    traffic_time_multiplier: float = 1.0,
    annotation_route_duration_seconds: int = ANNOTATION_ROUTE_DURATION_SECONDS,
    service_direction: str = "From School",
    route_palette: list[str] | None = None,
    outlying_private_access_rows: list[dict[str, Any]] | None = None,
    private_access_mode: str = "private_drive_stop",
) -> str:
    traffic_note = (
        f"{traffic_profile_name} traffic assumption "
        f"({float(traffic_time_multiplier):.2f}x travel-time multiplier; distance unchanged)"
    )
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
    traffic_time_multiplier: float = 1.0,
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
        f"{build_map_summary_html(points, routes, traffic_profile_name=traffic_profile_name, traffic_time_multiplier=traffic_time_multiplier, annotation_route_duration_seconds=annotation_route_duration_seconds, service_direction=service_direction, route_palette=colors, outlying_private_access_rows=outlying_private_access_rows, private_access_mode=private_access_mode)}</div>"
    )
    fmap.get_root().html.add_child(Element(panel))
    folium.LayerControl().add_to(fmap)
    fmap.save(str(output_path))
