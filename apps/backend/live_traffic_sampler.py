from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

import BusingProblem as planner
import planner_core
from quota_store_sqlite import SqliteQuotaStore
from runtime_store_sqlite import SqliteRuntimeStore


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent
AMAP_DIRECTION_URL = "https://restapi.amap.com/v3/direction/driving"
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.legs.duration,routes.legs.distanceMeters"
GOOGLE_ROUTES_PROVIDER = "google_routes"
GOOGLE_ROUTES_USAGE_COUNTER = "directions"
KAKAO_NAVI_FUTURE_DIRECTIONS_URL = os.environ.get(
    "BRP_KAKAO_NAVI_FUTURE_DIRECTIONS_URL",
    "https://apis-navi.kakaomobility.com/v1/future/directions",
).strip()
KAKAO_NAVI_PROVIDER = "kakao_navi"
KAKAO_NAVI_USAGE_COUNTER = "future_directions"


def _default_runtime_path(*parts: str) -> str:
    if os.name == "nt":
        return str(ROOT_DIR / "state" / Path(*parts))
    return str(Path("/opt/brp/shared/runtime", *parts))


DEFAULT_JOB_DIR = Path(os.environ.get("BRP_BACKEND_JOBS_DIR", _default_runtime_path("jobs")))
DEFAULT_RUNTIME_DB_PATH = Path(os.environ.get("BRP_RUNTIME_DB_PATH", _default_runtime_path("brp_runtime.sqlite")))
DEFAULT_OUTPUT_DIR = Path(os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DIR", _default_runtime_path("traffic_samples")))
DEFAULT_SLEEP_SECONDS = float(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_SLEEP_SECONDS", "0.45") or 0.45)
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_TIMEOUT_SECONDS", "20") or 20)
DEFAULT_STRATEGY = os.environ.get("BRP_LIVE_TRAFFIC_AMAP_STRATEGY", "4").strip() or "4"
DEFAULT_SIDE_TOOLS_DIR = Path(os.environ.get("BRP_SIDE_TOOLS_DIR", _default_runtime_path("side_tools")))
DEFAULT_BASELINE_DIR = Path(os.environ.get("BRP_LIVE_TRAFFIC_BASELINE_DIR", _default_runtime_path("traffic_baselines")))
DEFAULT_TZ = ZoneInfo(os.environ.get("BRP_LIVE_TRAFFIC_TIMEZONE", "Asia/Shanghai") or "Asia/Shanghai")
DEFAULT_DEPARTURE_MULTIPLIER = float(os.environ.get("BRP_LIVE_TRAFFIC_DEPARTURE_MULTIPLIER", "1.84") or 1.84)
DEFAULT_DUE_WINDOW_MINUTES = int(os.environ.get("BRP_LIVE_TRAFFIC_ROUTE_DUE_WINDOW_MINUTES", "5") or 5)
DEFAULT_ROUTE_START_TIMES_PATH = os.environ.get("BRP_LIVE_TRAFFIC_ROUTE_START_TIMES_PATH", "").strip()
DEFAULT_ROUTE_START_TIMES_JSON = os.environ.get("BRP_LIVE_TRAFFIC_ROUTE_START_TIMES_JSON", "").strip()
DEFAULT_PROVIDER = os.environ.get("BRP_LIVE_TRAFFIC_PROVIDER", "auto").strip().lower() or "auto"
DEFAULT_MAX_API_CALLS_PER_RUN = max(
    0,
    int(os.environ.get("BRP_LIVE_TRAFFIC_MAX_API_CALLS_PER_RUN", "1000") or 0),
)
DEFAULT_GOOGLE_ROUTES_USAGE_PATH = Path(
    os.environ.get("BRP_GOOGLE_ROUTES_USAGE_PATH", str(DEFAULT_OUTPUT_DIR.parent / "google_routes_usage.json"))
).expanduser()
DEFAULT_KAKAO_NAVI_USAGE_PATH = Path(
    os.environ.get("BRP_KAKAO_NAVI_USAGE_PATH", str(DEFAULT_OUTPUT_DIR.parent / "kakao_navi_usage.json"))
).expanduser()
GOOGLE_ROUTES_API_KEY = (
    os.environ.get("GOOGLE_ROUTES_API_KEY", "").strip()
    or os.environ.get("GOOGLE_GEOCODE_API_KEY", "").strip()
)
KAKAO_NAVI_API_KEY = (
    os.environ.get("KAKAO_MOBILITY_API_KEY", "").strip()
    or os.environ.get("KAKAO_REST_API_KEY", "").strip()
    or os.environ.get("KAKAO_API_KEY", "").strip()
)
GOOGLE_ROUTES_MONTHLY_SAFETY_CAP = max(
    0,
    int(os.environ.get("BRP_GOOGLE_ROUTES_MONTHLY_SAFETY_CAP", "4000") or 0),
)
GOOGLE_ROUTES_DAILY_CAP = max(
    0,
    int(os.environ.get("BRP_GOOGLE_ROUTES_DAILY_CAP", "250") or 0),
)
GOOGLE_ROUTES_MAX_CALLS_PER_REFRESH = max(
    0,
    int(os.environ.get("BRP_GOOGLE_ROUTES_MAX_CALLS_PER_REFRESH", "250") or 0),
)
GOOGLE_ROUTES_MAX_INTERMEDIATES = max(
    0,
    int(os.environ.get("BRP_GOOGLE_ROUTES_MAX_INTERMEDIATES", "25") or 25),
)
GOOGLE_ROUTES_ROUTING_PREFERENCE = (
    os.environ.get("BRP_GOOGLE_ROUTES_ROUTING_PREFERENCE", "TRAFFIC_AWARE").strip().upper()
    or "TRAFFIC_AWARE"
)
GOOGLE_ROUTES_EXTRA_COMPUTATIONS = [
    item.strip().upper()
    for item in os.environ.get("BRP_GOOGLE_ROUTES_EXTRA_COMPUTATIONS", "").split(",")
    if item.strip()
]
KAKAO_NAVI_MONTHLY_SAFETY_CAP = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_MONTHLY_SAFETY_CAP", "4000") or 0),
)
KAKAO_NAVI_DAILY_CAP = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_DAILY_CAP", "500") or 0),
)
KAKAO_NAVI_MAX_CALLS_PER_REFRESH = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_MAX_CALLS_PER_REFRESH", "500") or 0),
)
KAKAO_NAVI_MAX_WAYPOINTS = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_MAX_WAYPOINTS", "5") or 5),
)
KAKAO_NAVI_PRIORITY = os.environ.get("BRP_KAKAO_NAVI_PRIORITY", "RECOMMEND").strip() or "RECOMMEND"
KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS = max(
    0,
    int(os.environ.get("BRP_KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS", "0") or 0),
)
WEEKDAY_LABELS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]



def _coord(point: dict[str, Any]) -> str:
    return f"{float(point['lng']):.6f},{float(point['lat']):.6f}"


def _provider_for_args(args: argparse.Namespace) -> str:
    provider = str(getattr(args, "provider", "auto") or "auto").strip().lower()
    if provider != "auto":
        return provider
    market = str(getattr(args, "market", "") or "").strip().upper()
    city = str(getattr(args, "city", "") or "").strip().upper()
    if market in {"KR", "KOREA", "SOUTH_KOREA", "SOUTH KOREA"} or city in {"SEOUL", "SEONGNAM", "INCHEON"}:
        return KAKAO_NAVI_PROVIDER
    return "amap"


def _parse_sample_date(value: str | None, *, now: datetime) -> date:
    raw = str(value or "").strip()
    if not raw:
        return now.date()
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _combine_sample_date(clock: dt_time, *, sample_date: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(sample_date, clock, tzinfo=tz)


def _weekday_label(value: date | datetime) -> str:
    return WEEKDAY_LABELS[value.weekday()]


def _parse_google_duration_seconds(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if raw.endswith("s"):
        raw = raw[:-1]
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _google_routes_lat_lng(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "location": {
            "latLng": {
                "latitude": float(point["lat"]),
                "longitude": float(point["lng"]),
            }
        }
    }


def _google_routes_departure_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _kakao_navi_coord(point: dict[str, Any]) -> str:
    return f"{float(point['lng']):.7f},{float(point['lat']):.7f}"


def _kakao_navi_departure_time(value: datetime | None) -> str:
    departure = value or datetime.now(DEFAULT_TZ)
    return departure.astimezone(DEFAULT_TZ).strftime("%Y%m%d%H%M")


def _google_routes_usage_keys(now: datetime) -> tuple[str, str]:
    local_now = now.astimezone(DEFAULT_TZ)
    return local_now.strftime("%Y-%m"), local_now.date().isoformat()


def _kakao_navi_usage_keys(now: datetime) -> tuple[str, str]:
    local_now = now.astimezone(DEFAULT_TZ)
    return local_now.strftime("%Y-%m"), local_now.date().isoformat()


def _quota_store(args: argparse.Namespace) -> SqliteQuotaStore:
    quota_db_path = getattr(args, "quota_db_path", None)
    return SqliteQuotaStore(quota_db_path) if quota_db_path else SqliteQuotaStore()


def _migrate_usage_source(
    store: SqliteQuotaStore,
    *,
    source_path: Path,
    provider: str,
    counter: str,
    provider_label: str,
    sku_estimate: str,
) -> None:
    store.migrate_bucket_usage(
        source_path=source_path,
        provider=provider,
        counter=counter,
        provider_label=provider_label,
        sku_estimate=sku_estimate,
    )


def _period_usage_count(store: SqliteQuotaStore, provider: str, counter: str, period_type: str, period_key: str) -> int:
    usage = store.get_usage(provider, counter, period_type, period_key)
    return int(usage.get("attempted", usage.get("used", 0)) or 0)


def _reserve_google_routes_usage(args: argparse.Namespace, count: int, *, now: datetime) -> None:
    if count <= 0 or bool(getattr(args, "dry_run", False)):
        return
    monthly_cap = int(getattr(args, "google_routes_monthly_safety_cap", GOOGLE_ROUTES_MONTHLY_SAFETY_CAP) or 0)
    daily_cap = int(getattr(args, "google_routes_daily_cap", GOOGLE_ROUTES_DAILY_CAP) or 0)
    if monthly_cap <= 0 or daily_cap <= 0:
        raise RuntimeError("Google Routes usage cap is disabled; refusing live API call")
    path = Path(getattr(args, "google_routes_usage_path", DEFAULT_GOOGLE_ROUTES_USAGE_PATH)).expanduser()
    month_key, day_key = _google_routes_usage_keys(now)
    store = _quota_store(args)
    _migrate_usage_source(
        store,
        source_path=path,
        provider=GOOGLE_ROUTES_PROVIDER,
        counter=GOOGLE_ROUTES_USAGE_COUNTER,
        provider_label=GOOGLE_ROUTES_PROVIDER,
        sku_estimate="routes_compute_routes_pro",
    )
    month_used = _period_usage_count(store, GOOGLE_ROUTES_PROVIDER, GOOGLE_ROUTES_USAGE_COUNTER, "month", month_key)
    day_used = _period_usage_count(store, GOOGLE_ROUTES_PROVIDER, GOOGLE_ROUTES_USAGE_COUNTER, "day", day_key)
    if month_used + count > monthly_cap:
        raise RuntimeError(
            f"Google Routes monthly safety cap would be exceeded: {month_used}+{count}>{monthly_cap}"
        )
    if day_used + count > daily_cap:
        raise RuntimeError(
            f"Google Routes daily cap would be exceeded: {day_used}+{count}>{daily_cap}"
        )
    store.reserve_usage(
        GOOGLE_ROUTES_PROVIDER,
        GOOGLE_ROUTES_USAGE_COUNTER,
        [("month", month_key, monthly_cap), ("day", day_key, daily_cap)],
        count=count,
        provider_label=GOOGLE_ROUTES_PROVIDER,
        sku_estimate="routes_compute_routes_pro",
    )


def _mark_google_routes_usage_result(args: argparse.Namespace, *, now: datetime, succeeded: bool) -> None:
    if bool(getattr(args, "dry_run", False)):
        return
    path = Path(getattr(args, "google_routes_usage_path", DEFAULT_GOOGLE_ROUTES_USAGE_PATH)).expanduser()
    month_key, day_key = _google_routes_usage_keys(now)
    store = _quota_store(args)
    _migrate_usage_source(
        store,
        source_path=path,
        provider=GOOGLE_ROUTES_PROVIDER,
        counter=GOOGLE_ROUTES_USAGE_COUNTER,
        provider_label=GOOGLE_ROUTES_PROVIDER,
        sku_estimate="routes_compute_routes_pro",
    )
    store.mark_usage_result(
        GOOGLE_ROUTES_PROVIDER,
        GOOGLE_ROUTES_USAGE_COUNTER,
        [("month", month_key), ("day", day_key)],
        succeeded=succeeded,
        provider_label=GOOGLE_ROUTES_PROVIDER,
        sku_estimate="routes_compute_routes_pro",
    )


def _reserve_kakao_navi_usage(args: argparse.Namespace, count: int, *, now: datetime) -> None:
    if count <= 0 or bool(getattr(args, "dry_run", False)):
        return
    monthly_cap = int(getattr(args, "kakao_navi_monthly_safety_cap", KAKAO_NAVI_MONTHLY_SAFETY_CAP) or 0)
    daily_cap = int(getattr(args, "kakao_navi_daily_cap", KAKAO_NAVI_DAILY_CAP) or 0)
    if monthly_cap <= 0 or daily_cap <= 0:
        raise RuntimeError("Kakao Navi usage cap is disabled; refusing live API call")
    path = Path(getattr(args, "kakao_navi_usage_path", DEFAULT_KAKAO_NAVI_USAGE_PATH)).expanduser()
    month_key, day_key = _kakao_navi_usage_keys(now)
    store = _quota_store(args)
    _migrate_usage_source(
        store,
        source_path=path,
        provider=KAKAO_NAVI_PROVIDER,
        counter=KAKAO_NAVI_USAGE_COUNTER,
        provider_label=KAKAO_NAVI_PROVIDER,
        sku_estimate="kakao_navi_future_directions",
    )
    month_used = _period_usage_count(store, KAKAO_NAVI_PROVIDER, KAKAO_NAVI_USAGE_COUNTER, "month", month_key)
    day_used = _period_usage_count(store, KAKAO_NAVI_PROVIDER, KAKAO_NAVI_USAGE_COUNTER, "day", day_key)
    if month_used + count > monthly_cap:
        raise RuntimeError(
            f"Kakao Navi monthly safety cap would be exceeded: {month_used}+{count}>{monthly_cap}"
        )
    if day_used + count > daily_cap:
        raise RuntimeError(
            f"Kakao Navi daily cap would be exceeded: {day_used}+{count}>{daily_cap}"
        )
    store.reserve_usage(
        KAKAO_NAVI_PROVIDER,
        KAKAO_NAVI_USAGE_COUNTER,
        [("month", month_key, monthly_cap), ("day", day_key, daily_cap)],
        count=count,
        provider_label=KAKAO_NAVI_PROVIDER,
        sku_estimate="kakao_navi_future_directions",
    )


def _mark_kakao_navi_usage_result(args: argparse.Namespace, *, now: datetime, succeeded: bool) -> None:
    if bool(getattr(args, "dry_run", False)):
        return
    path = Path(getattr(args, "kakao_navi_usage_path", DEFAULT_KAKAO_NAVI_USAGE_PATH)).expanduser()
    month_key, day_key = _kakao_navi_usage_keys(now)
    store = _quota_store(args)
    _migrate_usage_source(
        store,
        source_path=path,
        provider=KAKAO_NAVI_PROVIDER,
        counter=KAKAO_NAVI_USAGE_COUNTER,
        provider_label=KAKAO_NAVI_PROVIDER,
        sku_estimate="kakao_navi_future_directions",
    )
    store.mark_usage_result(
        KAKAO_NAVI_PROVIDER,
        KAKAO_NAVI_USAGE_COUNTER,
        [("month", month_key), ("day", day_key)],
        succeeded=succeeded,
        provider_label=KAKAO_NAVI_PROVIDER,
        sku_estimate="kakao_navi_future_directions",
    )


def _google_routes_chunk_points(route_points: list[dict[str, Any]], max_intermediates: int) -> list[list[dict[str, Any]]]:
    max_points = max(2, int(max_intermediates) + 2)
    if len(route_points) <= max_points:
        return [route_points]
    chunks: list[list[dict[str, Any]]] = []
    start = 0
    while start < len(route_points) - 1:
        end = min(len(route_points), start + max_points)
        chunks.append(route_points[start:end])
        start = end - 1
    return chunks


def _balanced_route_point_chunks(route_points: list[dict[str, Any]], max_intermediates: int) -> list[list[dict[str, Any]]]:
    total_legs = max(0, len(route_points) - 1)
    if total_legs <= 0:
        return []
    max_legs_per_call = max(1, int(max_intermediates) + 1)
    if total_legs <= max_legs_per_call:
        return [route_points]
    chunk_count = (total_legs + max_legs_per_call - 1) // max_legs_per_call
    base_legs = total_legs // chunk_count
    extra = total_legs % chunk_count
    chunks: list[list[dict[str, Any]]] = []
    start = 0
    for index in range(chunk_count):
        leg_count = base_legs + (1 if index < extra else 0)
        end = start + leg_count
        chunks.append(route_points[start : end + 1])
        start = end
    return chunks


def _raw_osrm_seconds(route: dict[str, Any]) -> float:
    raw = route.get("raw_osrm_time_s")
    if raw is None:
        raw = route.get("time_s")
    if raw is None:
        raw = route.get("duration_s")
    if raw is not None:
        return float(raw)
    return sum(
        float(leg.get("raw_osrm_duration_s", leg.get("duration_s", 0.0)) or 0.0)
        for leg in route.get("leg_details") or []
    )


def _parse_clock(value: str | None) -> dt_time | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid local time: {value}")


def _combine_today(clock: dt_time, *, now: datetime) -> datetime:
    return datetime.combine(now.date(), clock, tzinfo=now.tzinfo)


def _normalize_route_schedule_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    prefix = raw.split("-", 1)[0].strip()
    digits = "".join(ch for ch in prefix if ch.isdigit())
    if digits:
        return f"Line {int(digits):02d}"
    return raw.lower()


def _load_route_start_times(args: argparse.Namespace) -> dict[str, str]:
    payload: Any = None
    if args.route_start_times_json:
        payload = json.loads(args.route_start_times_json)
    elif args.route_start_times_path:
        path = Path(args.route_start_times_path)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Route start times must be a JSON object")
    return {
        _normalize_route_schedule_key(key): str(value).strip()
        for key, value in payload.items()
        if str(value).strip()
    }


def _route_schedule_key(route: dict[str, Any]) -> str:
    route_id = route.get("route_id")
    key = _normalize_route_schedule_key(route_id)
    if key:
        return key
    return _normalize_route_schedule_key(route.get("vehicle_id"))


def _runtime_store(sqlite_path: Path = DEFAULT_RUNTIME_DB_PATH) -> SqliteRuntimeStore | None:
    path = sqlite_path.expanduser()
    if not path.exists():
        return None
    return SqliteRuntimeStore(path)


def _load_job(job_id: str, jobs_dir: Path, sqlite_path: Path = DEFAULT_RUNTIME_DB_PATH) -> dict[str, Any]:
    store = _runtime_store(sqlite_path)
    if store is not None:
        payload = store.get_job(job_id)
        if payload:
            if payload.get("status") != "succeeded":
                raise ValueError(f"Job {job_id} is not succeeded: {payload.get('status')}")
            return payload

    path = jobs_dir / f"{job_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Job file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "succeeded":
        raise ValueError(f"Job {job_id} is not succeeded: {payload.get('status')}")
    return payload


def _load_fleet_planner_run(
    run_id: str,
    side_tools_dir: Path,
    sqlite_path: Path = DEFAULT_RUNTIME_DB_PATH,
) -> dict[str, Any]:
    store = _runtime_store(sqlite_path)
    if store is not None:
        payload = store.get_side_tool_run("fleet_planner", run_id)
        if payload:
            return payload

    path = side_tools_dir / "fleet_planner" / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Fleet Planner run file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_service_direction(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw == "to_school":
        return "To School"
    if raw == "from_school":
        return "From School"
    text = str(value or "").strip()
    return text or "To School"


def _baseline_point_key(stop: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(stop.get("country") or "").strip(),
        str(stop.get("city") or "").strip(),
        str(stop.get("address") or "").strip(),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _baseline_route_metric(raw_route: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = _float_or_none(raw_route.get(name))
        if value is not None and value > 0:
            return value
    return None


def _baseline_stop_point(stop: dict[str, Any], node_id: int) -> dict[str, Any] | None:
    lat = _float_or_none(stop.get("lat", stop.get("latitude")))
    lng = _float_or_none(stop.get("lng", stop.get("longitude")))
    if lat is None or lng is None:
        return None
    point = dict(stop)
    point["node_id"] = node_id
    point["lat"] = lat
    point["lng"] = lng
    point.setdefault("passenger_count", int(stop.get("passenger_count", 0) or 0))
    return point


def _scenario_from_baseline_coordinates(
    baseline: dict[str, Any],
    raw_routes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    points: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for route_index, raw_route in enumerate(raw_routes, start=1):
        stops = sorted(list(raw_route.get("stops") or []), key=lambda item: int(item.get("stop_sequence", 0) or 0))
        if len(stops) < 2:
            return None
        route_points: list[dict[str, Any]] = []
        for stop in stops:
            point = _baseline_stop_point(stop, len(points))
            if point is None:
                return None
            route_points.append(point)
            points.append(point)
        duration_s = _baseline_route_metric(
            raw_route,
            "raw_osrm_time_s",
            "raw_duration_s",
            "historical_duration_s",
            "duration_s",
            "time_s",
        )
        distance_m = _baseline_route_metric(
            raw_route,
            "distance_m",
            "raw_distance_m",
            "historical_distance_m",
            "total_distance_m",
        )
        if duration_s is None or distance_m is None:
            return None
        route_id = str(raw_route.get("route_id") or route_index).strip()
        routes.append(
            {
                "route_id": route_id,
                "vehicle_id": route_index,
                "bus_type_name": str(raw_route.get("bus_type") or raw_route.get("vehicle_type") or "").strip(),
                "nodes": [int(point["node_id"]) for point in route_points],
                "raw_osrm_time_s": duration_s,
                "distance_m": distance_m,
                "leg_details": [],
                "baseline_metric_source": "baseline_json_coordinates",
            }
        )
    if not routes:
        return None
    return points, routes


def _route_osrm_metrics(route_points: list[dict[str, Any]]) -> tuple[float, float, list[dict[str, Any]]]:
    distance_m = 0.0
    duration_s = 0.0
    leg_details: list[dict[str, Any]] = []
    for origin, destination in zip(route_points[:-1], route_points[1:]):
        leg_distance_m, leg_duration_s, _geometry, metadata = planner.osrm_driving_direction_with_metadata(
            origin,
            destination,
        )
        distance_m += float(leg_distance_m)
        duration_s += float(leg_duration_s)
        leg_details.append(
            {
                "from_node": origin.get("node_id"),
                "to_node": destination.get("node_id"),
                "distance_m": int(leg_distance_m),
                "raw_osrm_duration_s": int(leg_duration_s),
                **metadata,
            }
        )
    return distance_m, duration_s, leg_details


def _scenario_from_baseline_json(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    path = Path(args.baseline_path)
    if not path.is_absolute():
        path = args.baseline_dir / path
    if not path.exists():
        raise FileNotFoundError(f"Baseline file not found: {path}")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    raw_routes = list(baseline.get("routes") or [])
    if not raw_routes:
        raise ValueError(f"Baseline {path} does not contain routes")

    coordinate_scenario = _scenario_from_baseline_coordinates(baseline, raw_routes)
    if coordinate_scenario is not None:
        points, routes = coordinate_scenario
        metadata = {
            "source": "baseline_json",
            "source_id": str(baseline.get("baseline_id") or path.stem),
            "service_direction": _normalize_service_direction(baseline.get("service_direction")),
            "title": str(baseline.get("source_workbook_name") or baseline.get("source_title") or path.name),
            "baseline_path": str(path),
            "source_workbook_sha256": baseline.get("source_workbook_sha256") or baseline.get("source_run_sha256"),
            "geocode_warning_count": 0,
            "baseline_load_mode": "coordinates",
        }
        return baseline, metadata, points, routes

    school_address = str(baseline.get("school_address") or "").strip()
    unique_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_stop(stop: dict[str, Any]) -> None:
        key = _baseline_point_key(stop)
        if not key[2] or key in seen:
            return
        seen.add(key)
        unique_records.append(
            {
                "country": key[0],
                "city": key[1],
                "address": key[2],
                "passenger_count": int(stop.get("passenger_count", 0) or 0),
            }
        )

    for route in raw_routes:
        for stop in list(route.get("stops") or []):
            if str(stop.get("address") or "").strip() == school_address or bool(stop.get("is_school")):
                add_stop(stop)
    for route in raw_routes:
        for stop in list(route.get("stops") or []):
            add_stop(stop)

    previous_osrm_base_url = planner.OSRM_BASE_URL
    points, warnings = planner.geocode_records(unique_records)
    scenario_osrm_base_url = planner.resolve_osrm_base_url(points)
    planner.OSRM_BASE_URL = scenario_osrm_base_url
    point_by_key = {
        _baseline_point_key(point): point
        for point in points
    }
    routes: list[dict[str, Any]] = []
    try:
        for route_index, raw_route in enumerate(raw_routes, start=1):
            stops = sorted(list(raw_route.get("stops") or []), key=lambda item: int(item.get("stop_sequence", 0) or 0))
            route_points = [point_by_key[_baseline_point_key(stop)] for stop in stops if _baseline_point_key(stop) in point_by_key]
            if len(route_points) < 2:
                continue
            distance_m, duration_s, leg_details = _route_osrm_metrics(route_points)
            route_id = str(raw_route.get("route_id") or route_index).strip()
            routes.append(
                {
                    "route_id": route_id,
                    "vehicle_id": route_index,
                    "bus_type_name": str(raw_route.get("bus_type") or "").strip(),
                    "nodes": [int(point["node_id"]) for point in route_points],
                    "raw_osrm_time_s": duration_s,
                    "distance_m": distance_m,
                    "leg_details": leg_details,
                }
            )
    finally:
        planner.OSRM_BASE_URL = previous_osrm_base_url

    metadata = {
        "source": "baseline_json",
        "source_id": str(baseline.get("baseline_id") or path.stem),
        "service_direction": _normalize_service_direction(baseline.get("service_direction")),
        "title": str(baseline.get("source_workbook_name") or path.name),
        "baseline_path": str(path),
        "source_workbook_sha256": baseline.get("source_workbook_sha256"),
        "geocode_warning_count": len(warnings),
        "baseline_load_mode": "geocode_osrm",
    }
    return baseline, metadata, points, routes


def _route_points(points: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    return [points[int(node_id)] for node_id in list(route.get("nodes") or [])]


def _scenario_from_route_audit_job(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    job = _load_job(args.job_id, args.jobs_dir, args.runtime_db_path)
    result = dict(job.get("result") or {})
    scenario = dict(result.get("current_plan_scenario") or {})
    points = list(scenario.get("points") or [])
    routes = list(scenario.get("routes") or [])
    metadata = {
        "source": "route_audit_job",
        "source_id": args.job_id,
        "service_direction": result.get("service_direction"),
    }
    return job, metadata, points, routes


def _scenario_from_fleet_planner(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    run = _load_fleet_planner_run(args.run_id, args.side_tools_dir, args.runtime_db_path)
    result = dict(run.get("global_plan_result") or {})
    raw_routes = list(result.get("routes") or [])
    points: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for route_index, raw_route in enumerate(raw_routes, start=1):
        route_points = [dict(point) for point in list(raw_route.get("ordered_points") or [])]
        if len(route_points) < 2:
            continue
        start_index = len(points)
        for offset, point in enumerate(route_points):
            point.setdefault("node_id", start_index + offset)
            point.setdefault("passenger_count", point.get("student_count", 0))
            points.append(point)
        node_ids = list(range(start_index, start_index + len(route_points)))
        vehicle_id = raw_route.get("vehicle_id") or raw_route.get("route_id") or route_index
        routes.append(
            {
                "route_id": f"fleet-{vehicle_id}",
                "vehicle_id": vehicle_id,
                "nodes": node_ids,
                "raw_osrm_time_s": float(raw_route.get("duration_s", 0.0) or 0.0),
                "distance_m": float(raw_route.get("distance_m", 0.0) or 0.0),
            }
        )
    metadata = {
        "source": "fleet_planner",
        "source_id": args.run_id,
        "service_direction": (run.get("scenario") or {}).get("service_direction"),
        "title": run.get("title"),
    }
    return run, metadata, points, routes


def _load_source(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if args.source == "route_audit_job":
        return _scenario_from_route_audit_job(args)
    if args.source == "fleet_planner":
        return _scenario_from_fleet_planner(args)
    if args.source == "baseline_json":
        return _scenario_from_baseline_json(args)
    raise ValueError(f"Unsupported source: {args.source}")


def _call_amap_route(
    route_points: list[dict[str, Any]],
    *,
    strategy: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    params = {
        "key": planner.AMAP_KEY,
        "origin": _coord(route_points[0]),
        "destination": _coord(route_points[-1]),
        "strategy": strategy,
        "extensions": "base",
        "output": "JSON",
    }
    waypoints = route_points[1:-1]
    if waypoints:
        params["waypoints"] = ";".join(_coord(point) for point in waypoints)
    response = requests.get(AMAP_DIRECTION_URL, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status")) != "1":
        raise RuntimeError(
            f"AMap status={payload.get('status')} infocode={payload.get('infocode')} info={payload.get('info')}"
        )
    paths = ((payload.get("route") or {}).get("paths") or [])
    if not paths:
        raise RuntimeError("AMap returned no paths")
    path = paths[0]
    duration_s = float(path.get("duration", 0.0) or 0.0)
    distance_m = float(path.get("distance", 0.0) or 0.0)
    return {
        "provider": "amap",
        "api": AMAP_DIRECTION_URL,
        "api_duration_s": duration_s,
        "api_distance_m": distance_m,
        "amap_duration_s": duration_s,
        "amap_distance_m": distance_m,
        "api_call_count": 1,
        "chunk_count": 1,
    }


def _call_google_routes_chunk(
    chunk_points: list[dict[str, Any]],
    *,
    departure_time: datetime | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not GOOGLE_ROUTES_API_KEY:
        raise RuntimeError("GOOGLE_ROUTES_API_KEY or GOOGLE_GEOCODE_API_KEY is required for Google Routes traffic sampling")
    body: dict[str, Any] = {
        "origin": _google_routes_lat_lng(chunk_points[0]),
        "destination": _google_routes_lat_lng(chunk_points[-1]),
        "travelMode": "DRIVE",
        "routingPreference": GOOGLE_ROUTES_ROUTING_PREFERENCE,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    if len(chunk_points) > 2:
        body["intermediates"] = [_google_routes_lat_lng(point) for point in chunk_points[1:-1]]
    departure = _google_routes_departure_time(departure_time)
    if departure:
        body["departureTime"] = departure
    if GOOGLE_ROUTES_EXTRA_COMPUTATIONS:
        body["extraComputations"] = GOOGLE_ROUTES_EXTRA_COMPUTATIONS
    response = requests.post(
        GOOGLE_ROUTES_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_ROUTES_API_KEY,
            "X-Goog-FieldMask": GOOGLE_ROUTES_FIELD_MASK,
        },
        json=body,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    routes = list(payload.get("routes") or [])
    if not routes:
        raise RuntimeError("Google Routes returned no routes")
    route = routes[0]
    duration_s = _parse_google_duration_seconds(route.get("duration"))
    distance_m = float(route.get("distanceMeters", 0.0) or 0.0)
    if duration_s <= 0:
        raise RuntimeError("Google Routes returned no usable duration")
    return {"duration_s": duration_s, "distance_m": distance_m}


def _call_google_routes(
    args: argparse.Namespace,
    route_points: list[dict[str, Any]],
    *,
    departure_time: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    chunks = _google_routes_chunk_points(route_points, int(args.google_routes_max_intermediates))
    if len(chunks) > int(args.google_routes_max_calls_per_refresh):
        raise RuntimeError(
            f"Google Routes chunk count {len(chunks)} exceeds max calls per refresh {args.google_routes_max_calls_per_refresh}"
        )
    total_duration_s = 0.0
    total_distance_m = 0.0
    for chunk in chunks:
        _reserve_google_routes_usage(args, 1, now=now)
        try:
            result = _call_google_routes_chunk(
                chunk,
                departure_time=departure_time,
                timeout_seconds=args.timeout_seconds,
            )
        except Exception:
            _mark_google_routes_usage_result(args, now=now, succeeded=False)
            raise
        _mark_google_routes_usage_result(args, now=now, succeeded=True)
        total_duration_s += float(result["duration_s"])
        total_distance_m += float(result["distance_m"])
        time.sleep(args.sleep_seconds)
    return {
        "provider": GOOGLE_ROUTES_PROVIDER,
        "api": GOOGLE_ROUTES_URL,
        "api_duration_s": total_duration_s,
        "api_distance_m": total_distance_m,
        "google_routes_duration_s": total_duration_s,
        "google_routes_distance_m": total_distance_m,
        "api_call_count": len(chunks),
        "chunk_count": len(chunks),
        "routing_preference": GOOGLE_ROUTES_ROUTING_PREFERENCE,
        "sku_estimate": "routes_compute_routes_pro",
    }


def _call_kakao_navi_chunk(
    chunk_points: list[dict[str, Any]],
    *,
    departure_time: datetime | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not KAKAO_NAVI_API_KEY:
        raise RuntimeError("KAKAO_MOBILITY_API_KEY or KAKAO_REST_API_KEY is required for Kakao Navi traffic sampling")
    params: dict[str, Any] = {
        "origin": _kakao_navi_coord(chunk_points[0]),
        "destination": _kakao_navi_coord(chunk_points[-1]),
        "priority": KAKAO_NAVI_PRIORITY,
        "car_fuel": "GASOLINE",
        "car_hipass": "false",
        "alternatives": "false",
        "road_details": "false",
        "departure_time": _kakao_navi_departure_time(departure_time),
    }
    waypoints = chunk_points[1:-1]
    if waypoints:
        params["waypoints"] = "|".join(_kakao_navi_coord(point) for point in waypoints)
    response = requests.get(
        KAKAO_NAVI_FUTURE_DIRECTIONS_URL,
        headers={
            "Authorization": f"KakaoAK {KAKAO_NAVI_API_KEY}",
            "Content-Type": "application/json",
        },
        params=params,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    routes = list(payload.get("routes") or [])
    if not routes:
        code = payload.get("code") or payload.get("result_code") or payload.get("msg")
        raise RuntimeError(f"Kakao Navi returned no routes: {code or 'empty response'}")
    route = routes[0]
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


def _call_kakao_navi(
    args: argparse.Namespace,
    route_points: list[dict[str, Any]],
    *,
    departure_time: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    chunks = _balanced_route_point_chunks(route_points, int(args.kakao_navi_max_waypoints))
    if len(chunks) > int(args.kakao_navi_max_calls_per_refresh):
        raise RuntimeError(
            f"Kakao Navi chunk count {len(chunks)} exceeds max calls per refresh {args.kakao_navi_max_calls_per_refresh}"
        )
    total_duration_s = 0.0
    total_distance_m = 0.0
    current_departure = departure_time or now
    dwell_seconds = int(getattr(args, "kakao_navi_inter_segment_dwell_seconds", 0) or 0)
    segment_rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        _reserve_kakao_navi_usage(args, 1, now=now)
        segment_departure = current_departure
        try:
            result = _call_kakao_navi_chunk(
                chunk,
                departure_time=segment_departure,
                timeout_seconds=args.timeout_seconds,
            )
        except Exception:
            _mark_kakao_navi_usage_result(args, now=now, succeeded=False)
            raise
        _mark_kakao_navi_usage_result(args, now=now, succeeded=True)
        duration_s = float(result["duration_s"])
        distance_m = float(result["distance_m"])
        total_duration_s += duration_s
        total_distance_m += distance_m
        segment_rows.append(
            {
                "segment_index": index,
                "point_count": len(chunk),
                "leg_count": max(0, len(chunk) - 1),
                "departure_local_time": segment_departure.astimezone(DEFAULT_TZ).isoformat(timespec="seconds"),
                "duration_s": duration_s,
                "distance_m": distance_m,
            }
        )
        current_departure = segment_departure + timedelta(seconds=duration_s + dwell_seconds)
        time.sleep(args.sleep_seconds)
    return {
        "provider": KAKAO_NAVI_PROVIDER,
        "api": KAKAO_NAVI_FUTURE_DIRECTIONS_URL,
        "api_duration_s": total_duration_s,
        "api_distance_m": total_distance_m,
        "kakao_navi_duration_s": total_duration_s,
        "kakao_navi_distance_m": total_distance_m,
        "api_call_count": len(chunks),
        "chunk_count": len(chunks),
        "max_waypoints_per_call": int(args.kakao_navi_max_waypoints),
        "inter_segment_dwell_seconds": dwell_seconds,
        "priority": KAKAO_NAVI_PRIORITY,
        "sku_estimate": "kakao_navi_future_directions",
        "kakao_navi_segments": segment_rows,
    }


def _traffic_duration_fields(row: dict[str, Any]) -> tuple[float, float]:
    osrm = float(row.get("osrm_duration_s", 0.0) or 0.0)
    api_duration = row.get("api_duration_s")
    if api_duration is None:
        api_duration = row.get("amap_duration_s")
    return osrm, float(api_duration or 0.0)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_osrm = 0.0
    total_api = 0.0
    factors = [float(row["factor"]) for row in rows]
    api_calls = 0
    providers: set[str] = set()
    for row in rows:
        osrm, api_duration = _traffic_duration_fields(row)
        total_osrm += osrm
        total_api += api_duration
        api_calls += int(row.get("api_call_count", 1) or 0)
        provider = str(row.get("provider", "") or "").strip()
        if provider:
            providers.add(provider)
    summary = {
        "route_count": len(rows),
        "api_call_count": api_calls,
        "providers": sorted(providers),
        "total_osrm_duration_s": total_osrm,
        "total_api_duration_s": total_api,
        "weighted_factor": (total_api / total_osrm) if total_osrm else None,
        "median_factor": statistics.median(factors) if factors else None,
        "mean_factor": statistics.mean(factors) if factors else None,
        "min_factor": min(factors) if factors else None,
        "max_factor": max(factors) if factors else None,
    }
    if any("amap_duration_s" in row for row in rows):
        summary["total_amap_duration_s"] = sum(float(row.get("amap_duration_s", 0.0) or 0.0) for row in rows)
    if any("google_routes_duration_s" in row for row in rows):
        summary["total_google_routes_duration_s"] = sum(float(row.get("google_routes_duration_s", 0.0) or 0.0) for row in rows)
    if any("kakao_navi_duration_s" in row for row in rows):
        summary["total_kakao_navi_duration_s"] = sum(float(row.get("kakao_navi_duration_s", 0.0) or 0.0) for row in rows)
    return summary


def _route_sampling_schedule(
    args: argparse.Namespace,
    route: dict[str, Any],
    osrm_s: float,
    now: datetime,
    route_start_times: dict[str, str],
) -> dict[str, Any]:
    target_arrival_clock = _parse_clock(args.target_arrival_local_time)
    departure_clock = _parse_clock(args.departure_local_time)
    schedule_key = _route_schedule_key(route)
    scheduled_start_time = route_start_times.get(schedule_key)
    route_start_clock = _parse_clock(scheduled_start_time)
    planned_departure = None
    target_arrival = None
    due_for_sample = True
    schedule_source = "none"
    if route_start_clock is not None:
        planned_departure = _combine_today(route_start_clock, now=now)
        schedule_source = "route_start_times"
    elif target_arrival_clock is not None:
        target_arrival = _combine_today(target_arrival_clock, now=now)
        planned_departure = target_arrival - timedelta(seconds=osrm_s * args.departure_multiplier)
        schedule_source = "target_arrival_fallback"
    elif departure_clock is not None:
        planned_departure = _combine_today(departure_clock, now=now)
        schedule_source = "period_departure"
    if args.sample_due_routes_only and planned_departure is not None:
        due_start = now - timedelta(minutes=args.route_due_window_minutes)
        due_end = now + timedelta(minutes=args.route_due_window_minutes)
        due_for_sample = due_start <= planned_departure <= due_end
    return {
        "route_schedule_key": schedule_key,
        "schedule_source": schedule_source,
        "scheduled_start_time": scheduled_start_time,
        "target_arrival_local_time": target_arrival.isoformat(timespec="seconds") if target_arrival else None,
        "planned_departure_local_time": planned_departure.isoformat(timespec="seconds") if planned_departure else None,
        "due_for_sample": due_for_sample,
    }


def _country_for_market(market: str) -> str:
    normalized = str(market or "").strip().upper().replace("_", " ")
    if normalized in {"KR", "KOREA", "SOUTH KOREA"}:
        return "South Korea"
    if normalized in {"CN", "CHINA"}:
        return "China"
    return normalized.title() if normalized else ""


def _planned_departure_from_schedule(schedule: dict[str, Any]) -> datetime | None:
    raw = str(schedule.get("planned_departure_local_time") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _dry_run_provider_result(
    provider: str,
    route: dict[str, Any],
    route_points: list[dict[str, Any]],
    osrm_s: float,
    args: argparse.Namespace,
) -> dict[str, Any]:
    distance_m = float(route.get("distance_m", 0.0) or 0.0)
    if provider == GOOGLE_ROUTES_PROVIDER:
        chunks = _google_routes_chunk_points(route_points, int(args.google_routes_max_intermediates))
        return {
            "provider": GOOGLE_ROUTES_PROVIDER,
            "api": GOOGLE_ROUTES_URL,
            "api_duration_s": osrm_s,
            "api_distance_m": distance_m,
            "google_routes_duration_s": osrm_s,
            "google_routes_distance_m": distance_m,
            "api_call_count": len(chunks),
            "chunk_count": len(chunks),
            "routing_preference": GOOGLE_ROUTES_ROUTING_PREFERENCE,
            "sku_estimate": "routes_compute_routes_pro",
        }
    if provider == KAKAO_NAVI_PROVIDER:
        chunks = _balanced_route_point_chunks(route_points, int(args.kakao_navi_max_waypoints))
        return {
            "provider": KAKAO_NAVI_PROVIDER,
            "api": KAKAO_NAVI_FUTURE_DIRECTIONS_URL,
            "api_duration_s": osrm_s,
            "api_distance_m": distance_m,
            "kakao_navi_duration_s": osrm_s,
            "kakao_navi_distance_m": distance_m,
            "api_call_count": len(chunks),
            "chunk_count": len(chunks),
            "max_waypoints_per_call": int(args.kakao_navi_max_waypoints),
            "inter_segment_dwell_seconds": int(args.kakao_navi_inter_segment_dwell_seconds),
            "priority": KAKAO_NAVI_PRIORITY,
            "sku_estimate": "kakao_navi_future_directions",
        }
    return {
        "provider": "amap",
        "api": AMAP_DIRECTION_URL,
        "api_duration_s": osrm_s,
        "api_distance_m": distance_m,
        "amap_duration_s": osrm_s,
        "amap_distance_m": distance_m,
        "api_call_count": 1,
        "chunk_count": 1,
    }


def _estimated_route_api_calls(provider: str, route_points: list[dict[str, Any]], args: argparse.Namespace) -> int:
    if provider == GOOGLE_ROUTES_PROVIDER:
        return len(_google_routes_chunk_points(route_points, int(args.google_routes_max_intermediates)))
    if provider == KAKAO_NAVI_PROVIDER:
        return len(_balanced_route_point_chunks(route_points, int(args.kakao_navi_max_waypoints)))
    return 1


def _estimated_api_call_count(
    provider: str,
    candidates: list[tuple[dict[str, Any], str, int, float, dict[str, Any], list[dict[str, Any]]]],
    args: argparse.Namespace,
) -> int:
    return sum(_estimated_route_api_calls(provider, route_points, args) for *_prefix, route_points in candidates)


def _enforce_api_call_budget(provider: str, estimated_calls: int, args: argparse.Namespace) -> None:
    per_run_cap = int(getattr(args, "max_api_calls_per_run", DEFAULT_MAX_API_CALLS_PER_RUN) or 0)
    if per_run_cap > 0 and estimated_calls > per_run_cap:
        raise RuntimeError(
            f"Live traffic refresh would use {estimated_calls} {provider} API call(s), "
            f"above per-run cap {per_run_cap}"
        )


def run_sample(args: argparse.Namespace) -> dict[str, Any]:
    _source_payload, source_metadata, points, routes = _load_source(args)
    if not points or not routes:
        raise ValueError(f"Source {source_metadata.get('source_id')} does not have points/routes")

    provider = _provider_for_args(args)
    route_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    measured_at = datetime.now(DEFAULT_TZ)
    sample_date = _parse_sample_date(getattr(args, "sample_date", ""), now=measured_at)
    schedule_now = _combine_sample_date(measured_at.time().replace(microsecond=0), sample_date=sample_date, tz=DEFAULT_TZ)
    route_start_times = _load_route_start_times(args)
    if args.now_local_time:
        schedule_now = _combine_sample_date(_parse_clock(args.now_local_time), sample_date=sample_date, tz=DEFAULT_TZ)

    candidates: list[tuple[dict[str, Any], str, int, float, dict[str, Any], list[dict[str, Any]]]] = []
    for route in routes:
        route_id = str(route.get("route_id") or route.get("vehicle_id") or "").strip()
        stop_count = len(route.get("nodes") or [])
        osrm_s = _raw_osrm_seconds(route)
        if stop_count < 2 or osrm_s <= 0:
            continue
        schedule = _route_sampling_schedule(args, route, osrm_s, schedule_now, route_start_times)
        if not schedule["due_for_sample"]:
            continue
        route_points = _route_points(points, route)
        candidates.append((route, route_id, stop_count, osrm_s, schedule, route_points))

    estimated_calls = _estimated_api_call_count(provider, candidates, args)
    _enforce_api_call_budget(provider, estimated_calls, args)
    if provider == GOOGLE_ROUTES_PROVIDER:
        if estimated_calls > int(args.google_routes_max_calls_per_refresh):
            raise RuntimeError(
                f"Google Routes refresh would use {estimated_calls} call(s), above cap {args.google_routes_max_calls_per_refresh}"
            )
    if provider == KAKAO_NAVI_PROVIDER:
        if estimated_calls > int(args.kakao_navi_max_calls_per_refresh):
            raise RuntimeError(
                f"Kakao Navi refresh would use {estimated_calls} call(s), above cap {args.kakao_navi_max_calls_per_refresh}"
            )

    for route, route_id, stop_count, osrm_s, schedule, route_points in candidates:
        if args.dry_run:
            provider_result = _dry_run_provider_result(provider, route, route_points, osrm_s, args)
        else:
            try:
                if provider == GOOGLE_ROUTES_PROVIDER:
                    provider_result = _call_google_routes(
                        args,
                        route_points,
                        departure_time=_planned_departure_from_schedule(schedule),
                        now=measured_at,
                    )
                elif provider == KAKAO_NAVI_PROVIDER:
                    provider_result = _call_kakao_navi(
                        args,
                        route_points,
                        departure_time=_planned_departure_from_schedule(schedule),
                        now=measured_at,
                    )
                elif provider == "amap":
                    provider_result = _call_amap_route(
                        route_points,
                        strategy=args.strategy,
                        timeout_seconds=args.timeout_seconds,
                    )
                    time.sleep(args.sleep_seconds)
                else:
                    raise RuntimeError(f"Unsupported live traffic provider: {provider}")
            except Exception as exc:
                errors.append({"route_id": route_id, "provider": provider, "error": str(exc)})
                print(f"ERROR {route_id}: {exc}", file=sys.stderr)
                time.sleep(args.sleep_seconds)
                continue
        api_s = float(provider_result.get("api_duration_s", 0.0) or 0.0)
        factor = api_s / osrm_s if osrm_s else 0.0
        row = {
            "job_id": args.job_id,
            "source": source_metadata.get("source"),
            "source_id": source_metadata.get("source_id"),
            "period": args.period,
            "market": args.market,
            "country": _country_for_market(args.market),
            "city": args.city,
            "service_direction": source_metadata.get("service_direction"),
            "sample_date": sample_date.isoformat(),
            "sample_weekday": _weekday_label(sample_date),
            "route_id": route_id,
            "vehicle_id": route.get("vehicle_id"),
            "stop_count": stop_count,
            "osrm_duration_s": osrm_s,
            "factor": factor,
            "route_fingerprint": planner_core.build_route_traffic_fingerprint(
                route,
                all_points=points,
                route_points=route_points,
            ) or {},
            **provider_result,
            **schedule,
        }
        route_rows.append(row)
        print(
            f"{route_id}: provider={provider_result.get('provider')} stops={stop_count} "
            f"osrm={osrm_s:.0f}s api={api_s:.0f}s factor={factor:.3f}"
        )

    summary = {
        "measured_at": measured_at.isoformat(timespec="seconds"),
        "local_date": sample_date.isoformat(),
        "sample_date": sample_date.isoformat(),
        "sample_weekday": _weekday_label(sample_date),
        "job_id": args.job_id,
        "run_id": args.run_id,
        "source": source_metadata.get("source"),
        "source_id": source_metadata.get("source_id"),
        "source_title": source_metadata.get("title"),
        "period": args.period,
        "market": args.market,
        "country": _country_for_market(args.market),
        "city": args.city,
        "service_direction": source_metadata.get("service_direction"),
        "provider": provider,
        "target_arrival_local_time": args.target_arrival_local_time,
        "departure_local_time": args.departure_local_time,
        "departure_multiplier": args.departure_multiplier,
        "route_start_times_path": args.route_start_times_path,
        "route_start_time_count": len(route_start_times),
        "sample_due_routes_only": bool(args.sample_due_routes_only),
        "route_due_window_minutes": args.route_due_window_minutes,
        "estimated_api_call_count": estimated_calls,
        "max_api_calls_per_run": int(args.max_api_calls_per_run),
        "api": (
            GOOGLE_ROUTES_URL
            if provider == GOOGLE_ROUTES_PROVIDER
            else KAKAO_NAVI_FUTURE_DIRECTIONS_URL
            if provider == KAKAO_NAVI_PROVIDER
            else AMAP_DIRECTION_URL
        ),
        "strategy": args.strategy if provider == "amap" else None,
        "routing_preference": GOOGLE_ROUTES_ROUTING_PREFERENCE if provider == GOOGLE_ROUTES_PROVIDER else None,
        "kakao_navi_priority": KAKAO_NAVI_PRIORITY if provider == KAKAO_NAVI_PROVIDER else None,
        "kakao_navi_max_waypoints": args.kakao_navi_max_waypoints if provider == KAKAO_NAVI_PROVIDER else None,
        "kakao_navi_inter_segment_dwell_seconds": (
            args.kakao_navi_inter_segment_dwell_seconds if provider == KAKAO_NAVI_PROVIDER else None
        ),
        "dry_run": bool(args.dry_run),
        "error_count": len(errors),
        **_summarize(route_rows),
        "routes": route_rows,
        "errors": errors,
    }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample live traffic route durations for a current-plan job.")
    parser.add_argument("--source", choices=("route_audit_job", "fleet_planner", "baseline_json"), default="route_audit_job")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--baseline-path", default="")
    parser.add_argument("--period", required=True, choices=("am_peak", "pm_peak", "off_peak"))
    parser.add_argument("--provider", choices=("auto", "amap", GOOGLE_ROUTES_PROVIDER, KAKAO_NAVI_PROVIDER), default=DEFAULT_PROVIDER)
    parser.add_argument("--sample-date", default=os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DATE", ""))
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOB_DIR)
    parser.add_argument("--side-tools-dir", type=Path, default=DEFAULT_SIDE_TOOLS_DIR)
    parser.add_argument("--runtime-db-path", type=Path, default=DEFAULT_RUNTIME_DB_PATH)
    parser.add_argument("--quota-db-path", type=Path, default=None)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--market", default=os.environ.get("BRP_LIVE_TRAFFIC_MARKET", "CN"))
    parser.add_argument("--city", default=os.environ.get("BRP_LIVE_TRAFFIC_CITY", "Shanghai"))
    parser.add_argument("--target-arrival-local-time", default=os.environ.get("BRP_LIVE_TRAFFIC_TARGET_ARRIVAL_LOCAL_TIME", ""))
    parser.add_argument("--departure-local-time", default=os.environ.get("BRP_LIVE_TRAFFIC_DEPARTURE_LOCAL_TIME", ""))
    parser.add_argument("--departure-multiplier", type=float, default=DEFAULT_DEPARTURE_MULTIPLIER)
    parser.add_argument("--route-start-times-path", default=DEFAULT_ROUTE_START_TIMES_PATH)
    parser.add_argument("--route-start-times-json", default=DEFAULT_ROUTE_START_TIMES_JSON)
    parser.add_argument("--sample-due-routes-only", action="store_true")
    parser.add_argument("--route-due-window-minutes", type=int, default=DEFAULT_DUE_WINDOW_MINUTES)
    parser.add_argument("--now-local-time", default="")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--max-api-calls-per-run", type=int, default=DEFAULT_MAX_API_CALLS_PER_RUN)
    parser.add_argument("--google-routes-usage-path", type=Path, default=DEFAULT_GOOGLE_ROUTES_USAGE_PATH)
    parser.add_argument("--google-routes-monthly-safety-cap", type=int, default=GOOGLE_ROUTES_MONTHLY_SAFETY_CAP)
    parser.add_argument("--google-routes-daily-cap", type=int, default=GOOGLE_ROUTES_DAILY_CAP)
    parser.add_argument("--google-routes-max-calls-per-refresh", type=int, default=GOOGLE_ROUTES_MAX_CALLS_PER_REFRESH)
    parser.add_argument("--google-routes-max-intermediates", type=int, default=GOOGLE_ROUTES_MAX_INTERMEDIATES)
    parser.add_argument("--kakao-navi-usage-path", type=Path, default=DEFAULT_KAKAO_NAVI_USAGE_PATH)
    parser.add_argument("--kakao-navi-monthly-safety-cap", type=int, default=KAKAO_NAVI_MONTHLY_SAFETY_CAP)
    parser.add_argument("--kakao-navi-daily-cap", type=int, default=KAKAO_NAVI_DAILY_CAP)
    parser.add_argument("--kakao-navi-max-calls-per-refresh", type=int, default=KAKAO_NAVI_MAX_CALLS_PER_REFRESH)
    parser.add_argument("--kakao-navi-max-waypoints", type=int, default=KAKAO_NAVI_MAX_WAYPOINTS)
    parser.add_argument("--kakao-navi-inter-segment-dwell-seconds", type=int, default=KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-empty", action="store_true")
    args = parser.parse_args()
    if args.source == "route_audit_job" and not args.job_id:
        parser.error("--job-id is required for route_audit_job source")
    if args.source == "fleet_planner" and not args.run_id:
        parser.error("--run-id is required for fleet_planner source")
    if args.source == "baseline_json" and not args.baseline_path:
        parser.error("--baseline-path is required for baseline_json source")
    return args


def main() -> None:
    args = parse_args()
    summary = run_sample(args)
    if args.skip_empty and not summary.get("route_count"):
        print("No routes were due for sampling; no sample file written.")
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_id = summary.get("source_id") or args.job_id or args.run_id
    provider = str(summary.get("provider") or "traffic")
    weekday = str(summary.get("sample_weekday") or "")
    weekday_part = f"_{weekday}" if weekday else ""
    filename = (
        f"{summary['city']}_{summary['period']}{weekday_part}_{summary['service_direction']}_{provider}_"
        f"{source_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path = args.output_dir / filename.replace(" ", "_").lower()
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== SUMMARY ==")
    print(
        json.dumps(
            {
                key: summary.get(key)
                for key in (
                    "measured_at",
                    "job_id",
                    "run_id",
                    "source",
                    "source_id",
                    "period",
                    "market",
                    "city",
                    "service_direction",
                    "provider",
                    "sample_date",
                    "sample_weekday",
                    "route_count",
                    "api_call_count",
                    "error_count",
                    "weighted_factor",
                    "median_factor",
                    "mean_factor",
                    "min_factor",
                    "max_factor",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"saved={output_path}")


if __name__ == "__main__":
    main()
