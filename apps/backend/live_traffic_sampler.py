from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

import BusingProblem as planner


AMAP_DIRECTION_URL = "https://restapi.amap.com/v3/direction/driving"
DEFAULT_JOB_DIR = Path(os.environ.get("BRP_BACKEND_JOBS_DIR", "/opt/brp/shared/runtime/jobs"))
DEFAULT_OUTPUT_DIR = Path(os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DIR", "/opt/brp/shared/runtime/traffic_samples"))
DEFAULT_SLEEP_SECONDS = float(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_SLEEP_SECONDS", "0.45") or 0.45)
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_TIMEOUT_SECONDS", "20") or 20)
DEFAULT_STRATEGY = os.environ.get("BRP_LIVE_TRAFFIC_AMAP_STRATEGY", "4").strip() or "4"
DEFAULT_SIDE_TOOLS_DIR = Path(os.environ.get("BRP_SIDE_TOOLS_DIR", "/opt/brp/shared/runtime/side_tools"))
DEFAULT_TZ = ZoneInfo(os.environ.get("BRP_LIVE_TRAFFIC_TIMEZONE", "Asia/Shanghai") or "Asia/Shanghai")
DEFAULT_DEPARTURE_MULTIPLIER = float(os.environ.get("BRP_LIVE_TRAFFIC_DEPARTURE_MULTIPLIER", "1.84") or 1.84)
DEFAULT_DUE_WINDOW_MINUTES = int(os.environ.get("BRP_LIVE_TRAFFIC_ROUTE_DUE_WINDOW_MINUTES", "5") or 5)


def _coord(point: dict[str, Any]) -> str:
    return f"{float(point['lng']):.6f},{float(point['lat']):.6f}"


def _raw_osrm_seconds(route: dict[str, Any]) -> float:
    raw = route.get("raw_osrm_time_s")
    if raw is not None:
        return float(raw)
    return sum(float(leg.get("raw_osrm_duration_s", 0.0) or 0.0) for leg in route.get("leg_details") or [])


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


def _load_job(job_id: str, jobs_dir: Path) -> dict[str, Any]:
    path = jobs_dir / f"{job_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Job file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "succeeded":
        raise ValueError(f"Job {job_id} is not succeeded: {payload.get('status')}")
    return payload


def _load_fleet_planner_run(run_id: str, side_tools_dir: Path) -> dict[str, Any]:
    path = side_tools_dir / "fleet_planner" / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Fleet Planner run file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _route_points(points: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    return [points[int(node_id)] for node_id in list(route.get("nodes") or [])]


def _scenario_from_route_audit_job(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    job = _load_job(args.job_id, args.jobs_dir)
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
    run = _load_fleet_planner_run(args.run_id, args.side_tools_dir)
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
    raise ValueError(f"Unsupported source: {args.source}")


def _call_amap_route(
    route_points: list[dict[str, Any]],
    *,
    strategy: str,
    timeout_seconds: int,
) -> tuple[float, float]:
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
    return float(path.get("duration", 0.0) or 0.0), float(path.get("distance", 0.0) or 0.0)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_osrm = sum(float(row["osrm_duration_s"]) for row in rows)
    total_amap = sum(float(row["amap_duration_s"]) for row in rows)
    factors = [float(row["factor"]) for row in rows]
    return {
        "route_count": len(rows),
        "total_osrm_duration_s": total_osrm,
        "total_amap_duration_s": total_amap,
        "weighted_factor": (total_amap / total_osrm) if total_osrm else None,
        "median_factor": statistics.median(factors) if factors else None,
        "mean_factor": statistics.mean(factors) if factors else None,
        "min_factor": min(factors) if factors else None,
        "max_factor": max(factors) if factors else None,
    }


def _route_sampling_schedule(args: argparse.Namespace, osrm_s: float, now: datetime) -> dict[str, Any]:
    target_arrival_clock = _parse_clock(args.target_arrival_local_time)
    departure_clock = _parse_clock(args.departure_local_time)
    planned_departure = None
    target_arrival = None
    due_for_sample = True
    if target_arrival_clock is not None:
        target_arrival = _combine_today(target_arrival_clock, now=now)
        planned_departure = target_arrival - timedelta(seconds=osrm_s * args.departure_multiplier)
    elif departure_clock is not None:
        planned_departure = _combine_today(departure_clock, now=now)
    if args.sample_due_routes_only and planned_departure is not None:
        due_start = now - timedelta(minutes=args.route_due_window_minutes)
        due_end = now + timedelta(minutes=args.route_due_window_minutes)
        due_for_sample = due_start <= planned_departure <= due_end
    return {
        "target_arrival_local_time": target_arrival.isoformat(timespec="seconds") if target_arrival else None,
        "planned_departure_local_time": planned_departure.isoformat(timespec="seconds") if planned_departure else None,
        "due_for_sample": due_for_sample,
    }


def run_sample(args: argparse.Namespace) -> dict[str, Any]:
    _source_payload, source_metadata, points, routes = _load_source(args)
    if not points or not routes:
        raise ValueError(f"Source {source_metadata.get('source_id')} does not have points/routes")

    route_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    now = datetime.now(DEFAULT_TZ)
    if args.now_local_time:
        now = _combine_today(_parse_clock(args.now_local_time), now=now)
    for route in routes:
        route_id = str(route.get("route_id") or route.get("vehicle_id") or "").strip()
        stop_count = len(route.get("nodes") or [])
        osrm_s = _raw_osrm_seconds(route)
        if stop_count < 2 or osrm_s <= 0:
            continue
        schedule = _route_sampling_schedule(args, osrm_s, now)
        if not schedule["due_for_sample"]:
            continue
        route_points = _route_points(points, route)
        if args.dry_run:
            amap_s = osrm_s
            amap_distance_m = float(route.get("distance_m", 0.0) or 0.0)
        else:
            try:
                amap_s, amap_distance_m = _call_amap_route(
                    route_points,
                    strategy=args.strategy,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:
                errors.append({"route_id": route_id, "error": str(exc)})
                print(f"ERROR {route_id}: {exc}", file=sys.stderr)
                time.sleep(args.sleep_seconds)
                continue
            time.sleep(args.sleep_seconds)
        factor = amap_s / osrm_s
        route_rows.append(
            {
                "job_id": args.job_id,
                "source": source_metadata.get("source"),
                "source_id": source_metadata.get("source_id"),
                "period": args.period,
                "market": args.market,
                "city": args.city,
                "service_direction": source_metadata.get("service_direction"),
                "route_id": route_id,
                "vehicle_id": route.get("vehicle_id"),
                "stop_count": stop_count,
                "osrm_duration_s": osrm_s,
                "amap_duration_s": amap_s,
                "amap_distance_m": amap_distance_m,
                "factor": factor,
                **schedule,
            }
        )
        print(f"{route_id}: stops={stop_count} osrm={osrm_s:.0f}s amap={amap_s:.0f}s factor={factor:.3f}")

    summary = {
        "measured_at": now.isoformat(timespec="seconds"),
        "local_date": now.date().isoformat(),
        "job_id": args.job_id,
        "run_id": args.run_id,
        "source": source_metadata.get("source"),
        "source_id": source_metadata.get("source_id"),
        "source_title": source_metadata.get("title"),
        "period": args.period,
        "market": args.market,
        "city": args.city,
        "service_direction": source_metadata.get("service_direction"),
        "target_arrival_local_time": args.target_arrival_local_time,
        "departure_local_time": args.departure_local_time,
        "departure_multiplier": args.departure_multiplier,
        "sample_due_routes_only": bool(args.sample_due_routes_only),
        "route_due_window_minutes": args.route_due_window_minutes,
        "api": AMAP_DIRECTION_URL,
        "strategy": args.strategy,
        "dry_run": bool(args.dry_run),
        "error_count": len(errors),
        **_summarize(route_rows),
        "routes": route_rows,
        "errors": errors,
    }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample live AMap driving durations for a current-plan job.")
    parser.add_argument("--source", choices=("route_audit_job", "fleet_planner"), default="route_audit_job")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--period", required=True, choices=("am_peak", "pm_peak", "off_peak"))
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOB_DIR)
    parser.add_argument("--side-tools-dir", type=Path, default=DEFAULT_SIDE_TOOLS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--market", default=os.environ.get("BRP_LIVE_TRAFFIC_MARKET", "CN"))
    parser.add_argument("--city", default=os.environ.get("BRP_LIVE_TRAFFIC_CITY", "Shanghai"))
    parser.add_argument("--target-arrival-local-time", default=os.environ.get("BRP_LIVE_TRAFFIC_TARGET_ARRIVAL_LOCAL_TIME", ""))
    parser.add_argument("--departure-local-time", default=os.environ.get("BRP_LIVE_TRAFFIC_DEPARTURE_LOCAL_TIME", ""))
    parser.add_argument("--departure-multiplier", type=float, default=DEFAULT_DEPARTURE_MULTIPLIER)
    parser.add_argument("--sample-due-routes-only", action="store_true")
    parser.add_argument("--route-due-window-minutes", type=int, default=DEFAULT_DUE_WINDOW_MINUTES)
    parser.add_argument("--now-local-time", default="")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-empty", action="store_true")
    args = parser.parse_args()
    if args.source == "route_audit_job" and not args.job_id:
        parser.error("--job-id is required for route_audit_job source")
    if args.source == "fleet_planner" and not args.run_id:
        parser.error("--run-id is required for fleet_planner source")
    return args


def main() -> None:
    args = parse_args()
    summary = run_sample(args)
    if args.skip_empty and not summary.get("route_count"):
        print("No routes were due for sampling; no sample file written.")
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_id = summary.get("source_id") or args.job_id or args.run_id
    filename = (
        f"{summary['city']}_{summary['period']}_{summary['service_direction']}_"
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
                    "route_count",
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
