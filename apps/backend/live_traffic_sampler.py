from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

import BusingProblem as planner


AMAP_DIRECTION_URL = "https://restapi.amap.com/v3/direction/driving"
DEFAULT_JOB_DIR = Path(os.environ.get("BRP_BACKEND_JOBS_DIR", "/opt/brp/shared/runtime/jobs"))
DEFAULT_OUTPUT_DIR = Path(os.environ.get("BRP_LIVE_TRAFFIC_SAMPLE_DIR", "/opt/brp/shared/runtime/traffic_samples"))
DEFAULT_SLEEP_SECONDS = float(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_SLEEP_SECONDS", "0.45") or 0.45)
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("BRP_LIVE_TRAFFIC_REQUEST_TIMEOUT_SECONDS", "20") or 20)
DEFAULT_STRATEGY = os.environ.get("BRP_LIVE_TRAFFIC_AMAP_STRATEGY", "4").strip() or "4"


def _coord(point: dict[str, Any]) -> str:
    return f"{float(point['lng']):.6f},{float(point['lat']):.6f}"


def _raw_osrm_seconds(route: dict[str, Any]) -> float:
    raw = route.get("raw_osrm_time_s")
    if raw is not None:
        return float(raw)
    return sum(float(leg.get("raw_osrm_duration_s", 0.0) or 0.0) for leg in route.get("leg_details") or [])


def _load_job(job_id: str, jobs_dir: Path) -> dict[str, Any]:
    path = jobs_dir / f"{job_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Job file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "succeeded":
        raise ValueError(f"Job {job_id} is not succeeded: {payload.get('status')}")
    return payload


def _route_points(points: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    return [points[int(node_id)] for node_id in list(route.get("nodes") or [])]


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


def run_sample(args: argparse.Namespace) -> dict[str, Any]:
    job = _load_job(args.job_id, args.jobs_dir)
    result = dict(job.get("result") or {})
    scenario = dict(result.get("current_plan_scenario") or {})
    points = list(scenario.get("points") or [])
    routes = list(scenario.get("routes") or [])
    if not points or not routes:
        raise ValueError(f"Job {args.job_id} does not have current_plan_scenario points/routes")

    route_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for route in routes:
        route_id = str(route.get("route_id") or route.get("vehicle_id") or "").strip()
        stop_count = len(route.get("nodes") or [])
        osrm_s = _raw_osrm_seconds(route)
        if stop_count < 2 or osrm_s <= 0:
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
                "period": args.period,
                "service_direction": result.get("service_direction"),
                "route_id": route_id,
                "vehicle_id": route.get("vehicle_id"),
                "stop_count": stop_count,
                "osrm_duration_s": osrm_s,
                "amap_duration_s": amap_s,
                "amap_distance_m": amap_distance_m,
                "factor": factor,
            }
        )
        print(f"{route_id}: stops={stop_count} osrm={osrm_s:.0f}s amap={amap_s:.0f}s factor={factor:.3f}")

    summary = {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "job_id": args.job_id,
        "period": args.period,
        "service_direction": result.get("service_direction"),
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
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--period", required=True, choices=("am_peak", "pm_peak", "off_peak"))
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOB_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_sample(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{summary['period']}_{summary['service_direction']}_{args.job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
                    "period",
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
