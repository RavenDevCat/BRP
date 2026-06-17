from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "apps" / "backend"
DEFAULT_ENV_FILE = ROOT_DIR / "ops" / "env" / "local.env"


def _preparse_env_file(argv: list[str]) -> Path | None:
    for index, item in enumerate(argv):
        if item == "--env-file" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser()
        if item.startswith("--env-file="):
            return Path(item.split("=", 1)[1]).expanduser()
    return DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.exists() else None


def _load_env_file(path: Path | None) -> None:
    if not path or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_preparse_env_file(sys.argv[1:]))
sys.path.insert(0, str(BACKEND_DIR))

import live_traffic_sampler as sampler  # noqa: E402


ACTIVE_TIMER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("shanghai_am_peak", "Shanghai", "am_peak"),
    ("shanghai_pm_peak", "Shanghai", "pm_peak"),
    ("suzhou_am_peak", "Suzhou", "am_peak"),
    ("suzhou_pm_peak", "Suzhou", "pm_peak"),
)
OPTIONAL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("shanghai_off_peak", "Shanghai", "off_peak"),
    ("suzhou_off_peak", "Suzhou", "off_peak"),
)


def _env(name: str, default: Any = "") -> str:
    return str(os.environ.get(name, default) or "")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def _resolve_baseline_path(args: SimpleNamespace) -> Path:
    path = Path(str(args.baseline_path or ""))
    if not path.is_absolute():
        path = Path(args.baseline_dir) / path
    return path


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _has_stop_coordinates(stop: dict[str, Any]) -> bool:
    return _positive_number(stop.get("lat", stop.get("latitude"))) and _positive_number(stop.get("lng", stop.get("longitude")))


def _has_route_metrics(route: dict[str, Any]) -> bool:
    has_duration = any(
        _positive_number(route.get(name))
        for name in ("raw_osrm_time_s", "raw_duration_s", "historical_duration_s", "duration_s", "time_s")
    )
    has_distance = any(
        _positive_number(route.get(name))
        for name in ("distance_m", "raw_distance_m", "historical_distance_m", "total_distance_m")
    )
    return has_duration and has_distance


def _build_sampler_args(
    *,
    city: str,
    period: str,
    max_api_calls_per_run: int | None,
    sample_due_routes_only: bool,
    now_local_time: str,
) -> SimpleNamespace:
    city_key = city.strip().lower()
    source = "route_audit_job"
    job_id = ""
    run_id = ""
    baseline_path = ""
    market = "CN"
    target_arrival_local_time = ""
    departure_local_time = ""
    route_start_times_path = sampler.DEFAULT_ROUTE_START_TIMES_PATH

    if city_key == "suzhou":
        source = _env("BRP_LIVE_TRAFFIC_SUZHOU_SOURCE", "baseline_json")
        run_id = _env("BRP_LIVE_TRAFFIC_SUZHOU_RUN_ID", "0048b194830c")
        baseline_path = _env(
            "BRP_LIVE_TRAFFIC_SUZHOU_BASELINE_PATH",
            "suzhou/suzhou_fleet_planner_0048b194830c_current_plan.json",
        )
        market = _env("BRP_LIVE_TRAFFIC_SUZHOU_MARKET", "CN")
        city = _env("BRP_LIVE_TRAFFIC_SUZHOU_CITY", "Suzhou")
        if period == "am_peak":
            target_arrival_local_time = _env("BRP_LIVE_TRAFFIC_SUZHOU_AM_TARGET_ARRIVAL_LOCAL_TIME", "08:00")
        elif period == "pm_peak":
            departure_local_time = _env("BRP_LIVE_TRAFFIC_SUZHOU_PM_DEPARTURE_LOCAL_TIME", "15:40")
    elif period == "am_peak":
        job_id = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID")
        source = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_SOURCE", "route_audit_job")
        run_id = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_RUN_ID")
        baseline_path = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_BASELINE_PATH")
        market = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_MARKET", "CN")
        city = _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_CITY", "Shanghai")
        target_arrival_local_time = _env("BRP_LIVE_TRAFFIC_AM_TARGET_ARRIVAL_LOCAL_TIME", "08:00")
        route_start_times_path = _env("BRP_LIVE_TRAFFIC_AM_ROUTE_START_TIMES_PATH", route_start_times_path)
    elif period == "pm_peak":
        job_id = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_JOB_ID")
        source = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_SOURCE", "route_audit_job")
        run_id = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_RUN_ID")
        baseline_path = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_BASELINE_PATH")
        market = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_MARKET", "CN")
        city = _env("BRP_LIVE_TRAFFIC_FROM_SCHOOL_CITY", "Shanghai")
        departure_local_time = _env("BRP_LIVE_TRAFFIC_PM_DEPARTURE_LOCAL_TIME", "15:40")
    elif period == "off_peak":
        job_id = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_JOB_ID", _env("BRP_LIVE_TRAFFIC_TO_SCHOOL_JOB_ID"))
        source = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_SOURCE", "route_audit_job")
        run_id = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_RUN_ID")
        baseline_path = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_BASELINE_PATH")
        market = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_MARKET", "CN")
        city = _env("BRP_LIVE_TRAFFIC_OFF_PEAK_CITY", city)

    cap = max_api_calls_per_run
    if cap is None:
        cap = _int_env("BRP_LIVE_TRAFFIC_MAX_API_CALLS_PER_RUN", sampler.DEFAULT_MAX_API_CALLS_PER_RUN)

    return SimpleNamespace(
        source=source,
        job_id=job_id,
        run_id=run_id,
        baseline_path=baseline_path,
        period=period,
        provider=sampler.DEFAULT_PROVIDER,
        sample_date=_env("BRP_LIVE_TRAFFIC_SAMPLE_DATE"),
        jobs_dir=sampler.DEFAULT_JOB_DIR,
        side_tools_dir=sampler.DEFAULT_SIDE_TOOLS_DIR,
        baseline_dir=sampler.DEFAULT_BASELINE_DIR,
        output_dir=sampler.DEFAULT_OUTPUT_DIR,
        market=market,
        city=city,
        target_arrival_local_time=target_arrival_local_time,
        departure_local_time=departure_local_time,
        departure_multiplier=sampler.DEFAULT_DEPARTURE_MULTIPLIER,
        route_start_times_path=route_start_times_path,
        route_start_times_json=sampler.DEFAULT_ROUTE_START_TIMES_JSON,
        sample_due_routes_only=sample_due_routes_only,
        route_due_window_minutes=sampler.DEFAULT_DUE_WINDOW_MINUTES,
        now_local_time=now_local_time,
        strategy=sampler.DEFAULT_STRATEGY,
        max_api_calls_per_run=cap,
        google_routes_max_intermediates=sampler.GOOGLE_ROUTES_MAX_INTERMEDIATES,
        google_routes_max_calls_per_refresh=sampler.GOOGLE_ROUTES_MAX_CALLS_PER_REFRESH,
        kakao_navi_max_waypoints=sampler.KAKAO_NAVI_MAX_WAYPOINTS,
        kakao_navi_max_calls_per_refresh=sampler.KAKAO_NAVI_MAX_CALLS_PER_REFRESH,
        kakao_navi_inter_segment_dwell_seconds=sampler.KAKAO_NAVI_INTER_SEGMENT_DWELL_SECONDS,
    )


def _baseline_route_outlines(args: SimpleNamespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    path = _resolve_baseline_path(args)
    baseline = json.loads(path.read_text(encoding="utf-8"))
    routes: list[dict[str, Any]] = []
    total_stop_count = 0
    coordinate_stop_count = 0
    metric_route_count = 0
    for route_index, raw_route in enumerate(list(baseline.get("routes") or []), start=1):
        stops = sorted(list(raw_route.get("stops") or []), key=lambda item: int(item.get("stop_sequence", 0) or 0))
        if len(stops) < 2:
            continue
        total_stop_count += len(stops)
        coordinate_stop_count += sum(1 for stop in stops if _has_stop_coordinates(stop))
        if _has_route_metrics(raw_route):
            metric_route_count += 1
        route_id = str(raw_route.get("route_id") or route_index).strip()
        routes.append(
            {
                "route_id": route_id,
                "vehicle_id": route_index,
                "stop_count": len(stops),
                "raw_osrm_time_s": float(raw_route.get("raw_osrm_time_s", 0.0) or 0.0),
            }
        )
    metadata = {
        "source": "baseline_json",
        "source_id": str(baseline.get("baseline_id") or path.stem),
        "service_direction": baseline.get("service_direction"),
        "title": baseline.get("title"),
        "safe_outline_only": True,
        "baseline_path": str(path),
        "baseline_stop_count": total_stop_count,
        "baseline_coordinate_stop_count": coordinate_stop_count,
        "baseline_metric_route_count": metric_route_count,
        "baseline_fast_path_ready": bool(routes) and coordinate_stop_count == total_stop_count and metric_route_count == len(routes),
    }
    return baseline, metadata, routes


def _source_outlines(args: SimpleNamespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int]:
    if args.source == "baseline_json":
        payload, metadata, routes = _baseline_route_outlines(args)
        return payload, metadata, routes, 0
    payload, metadata, points, routes = sampler._load_source(args)
    return payload, metadata, routes, len(points)


def _provider_route_points(provider: str, route: dict[str, Any], args: SimpleNamespace, point_count: int) -> list[dict[str, Any]]:
    if args.source == "baseline_json":
        return [{"node_id": index} for index in range(int(route.get("stop_count", 0) or 0))]
    nodes = list(route.get("nodes") or [])
    return [{"node_id": index} for index in nodes[:point_count]]


def evaluate_profile(
    profile_name: str,
    args: SimpleNamespace,
    *,
    measured_at: datetime | None = None,
) -> dict[str, Any]:
    measured_at = measured_at or datetime.now(sampler.DEFAULT_TZ)
    provider = sampler._provider_for_args(args)
    _payload, metadata, routes, point_count = _source_outlines(args)
    sample_date = sampler._parse_sample_date(args.sample_date, now=measured_at)
    schedule_now = sampler._combine_sample_date(
        measured_at.time().replace(microsecond=0),
        sample_date=sample_date,
        tz=sampler.DEFAULT_TZ,
    )
    if args.now_local_time:
        schedule_now = sampler._combine_sample_date(
            sampler._parse_clock(args.now_local_time),
            sample_date=sample_date,
            tz=sampler.DEFAULT_TZ,
        )
    route_start_times = sampler._load_route_start_times(args)
    candidates = []
    for route in routes:
        route_id = str(route.get("route_id") or route.get("vehicle_id") or "").strip()
        stop_count = int(route.get("stop_count") or len(route.get("nodes") or []))
        if stop_count < 2:
            continue
        osrm_s = sampler._raw_osrm_seconds(route) if args.source != "baseline_json" else 0.0
        if args.sample_due_routes_only and osrm_s > 0:
            schedule = sampler._route_sampling_schedule(args, route, osrm_s, schedule_now, route_start_times)
            if not schedule["due_for_sample"]:
                continue
        else:
            schedule = {
                "route_schedule_key": route_id,
                "schedule_source": "budget_outline",
                "due_for_sample": True,
            }
        route_points = _provider_route_points(provider, route, args, point_count)
        candidates.append((route, route_id, stop_count, osrm_s, schedule, route_points))

    estimated_calls = sampler._estimated_api_call_count(provider, candidates, args)
    provider_cap = 0
    if provider == sampler.GOOGLE_ROUTES_PROVIDER:
        provider_cap = int(args.google_routes_max_calls_per_refresh)
    elif provider == sampler.KAKAO_NAVI_PROVIDER:
        provider_cap = int(args.kakao_navi_max_calls_per_refresh)
    caps = [cap for cap in (int(args.max_api_calls_per_run), provider_cap) if cap > 0]
    status = "ok" if not caps or all(estimated_calls <= cap for cap in caps) else "over_cap"
    return {
        "profile": profile_name,
        "source": metadata.get("source"),
        "source_id": metadata.get("source_id"),
        "period": args.period,
        "market": args.market,
        "city": args.city,
        "provider": provider,
        "route_count": len(routes),
        "candidate_route_count": len(candidates),
        "estimated_api_call_count": estimated_calls,
        "max_api_calls_per_run": int(args.max_api_calls_per_run),
        "provider_refresh_cap": provider_cap,
        "status": status,
        "safe_outline_only": bool(metadata.get("safe_outline_only")),
        "baseline_fast_path_ready": metadata.get("baseline_fast_path_ready"),
        "baseline_stop_count": metadata.get("baseline_stop_count"),
        "baseline_coordinate_stop_count": metadata.get("baseline_coordinate_stop_count"),
        "baseline_metric_route_count": metadata.get("baseline_metric_route_count"),
        "baseline_path": metadata.get("baseline_path"),
        "sample_due_routes_only": bool(args.sample_due_routes_only),
    }


def _profile_specs(include_off_peak: bool) -> tuple[tuple[str, str, str], ...]:
    return ACTIVE_TIMER_SPECS + (OPTIONAL_SPECS if include_off_peak else ())


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for profile_name, city, period in _profile_specs(args.include_off_peak):
        sampler_args = _build_sampler_args(
            city=city,
            period=period,
            max_api_calls_per_run=args.max_api_calls_per_run,
            sample_due_routes_only=args.sample_due_routes_only,
            now_local_time=args.now_local_time,
        )
        try:
            rows.append(evaluate_profile(profile_name, sampler_args))
        except Exception as exc:
            rows.append(
                {
                    "profile": profile_name,
                    "city": city,
                    "period": period,
                    "status": "error",
                    "error": str(exc),
                }
            )
    return {
        "generated_at": datetime.now(sampler.DEFAULT_TZ).isoformat(timespec="seconds"),
        "mode": "read_only_budget_preflight",
        "provider_api_called": False,
        "osrm_started": False,
        "profiles": rows,
    }


def _print_table(report: dict[str, Any]) -> None:
    print("profile                 provider       routes candidates calls cap provider_cap fast_path status")
    for row in report["profiles"]:
        fast_path = row.get("baseline_fast_path_ready")
        fast_path_label = "yes" if fast_path is True else "no" if fast_path is False else "-"
        print(
            f"{row.get('profile', ''):<23} "
            f"{row.get('provider', '-'):>12} "
            f"{str(row.get('route_count', '-')):>6} "
            f"{str(row.get('candidate_route_count', '-')):>10} "
            f"{str(row.get('estimated_api_call_count', '-')):>5} "
            f"{str(row.get('max_api_calls_per_run', '-')):>3} "
            f"{str(row.get('provider_refresh_cap', '-')):>12} "
            f"{fast_path_label:>9} "
            f"{row.get('status', 'error')}"
        )
        if row.get("error"):
            print(f"  error: {row['error']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only live traffic API budget preflight.")
    parser.add_argument("--env-file", type=Path, default=_preparse_env_file(sys.argv[1:]) or DEFAULT_ENV_FILE)
    parser.add_argument("--include-off-peak", action="store_true")
    parser.add_argument("--max-api-calls-per-run", type=int, default=None)
    parser.add_argument("--sample-due-routes-only", action="store_true")
    parser.add_argument("--now-local-time", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-under-cap", action="store_true")
    parser.add_argument("--require-baseline-fast-path", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_table(report)
    if args.require_under_cap:
        bad = [row for row in report["profiles"] if row.get("status") != "ok"]
        if bad:
            raise SystemExit(1)
    if args.require_baseline_fast_path:
        bad = [row for row in report["profiles"] if row.get("source") == "baseline_json" and row.get("baseline_fast_path_ready") is not True]
        if bad:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
