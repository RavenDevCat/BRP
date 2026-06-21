from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_JOBS_DIR = Path("/opt/brp/shared/runtime/jobs")
DEFAULT_BASELINE_DIR = Path("/opt/brp/shared/runtime/traffic_baselines")
ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "apps" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from runtime_store_sqlite import SqliteRuntimeStore  # noqa: E402


def _default_runtime_db_path() -> Path:
    configured = str(os.environ.get("BRP_RUNTIME_DB_PATH", "")).strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return ROOT_DIR / "state" / "brp_runtime.sqlite"
    return Path("/opt/brp/shared/runtime/brp_runtime.sqlite")


DEFAULT_RUNTIME_DB_PATH = _default_runtime_db_path()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_runtime_job(job_id: str, jobs_dir: Path, sqlite_path: Path) -> dict[str, Any]:
    db_path = sqlite_path.expanduser()
    if db_path.exists():
        payload = SqliteRuntimeStore(db_path).get_job(job_id)
        if payload:
            return payload
    return _load_json(jobs_dir / f"{job_id}.json")


def _sorted_stops(route: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(list(route.get("stops") or []), key=lambda item: int(item.get("stop_sequence", 0) or 0))


def _point_address(point: dict[str, Any]) -> str:
    return str(point.get("address") or "").strip()


def _stop_address(stop: dict[str, Any]) -> str:
    return str(stop.get("address") or "").strip()


def _copy_point_fields(stop: dict[str, Any], point: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(stop)
    for key in (
        "lat",
        "lng",
        "plot_lat",
        "plot_lng",
        "formatted_address",
        "display_address",
        "provider",
        "validation_status",
        "requested_address",
        "requested_city",
        "requested_country",
    ):
        if point.get(key) is not None:
            enriched[key] = point.get(key)
    if "lat" not in enriched and point.get("latitude") is not None:
        enriched["lat"] = point.get("latitude")
    if "lng" not in enriched and point.get("longitude") is not None:
        enriched["lng"] = point.get("longitude")
    return enriched


def _route_metrics(route: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for source_key, target_key in (
        ("raw_osrm_time_s", "raw_osrm_time_s"),
        ("distance_m", "distance_m"),
        ("time_s", "historical_display_time_s"),
        ("traffic_buffer_factor", "historical_traffic_buffer_factor"),
        ("traffic_time_source", "historical_traffic_time_source"),
    ):
        if route.get(source_key) is not None:
            metrics[target_key] = route.get(source_key)
    if route.get("raw_osrm_time_s") is not None:
        metrics["historical_duration_s"] = route.get("raw_osrm_time_s")
    if route.get("distance_m") is not None:
        metrics["historical_distance_m"] = route.get("distance_m")
    return metrics


def enrich_baseline_from_job(
    baseline: dict[str, Any],
    job: dict[str, Any],
    *,
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = job.get("result") or {}
    scenario = result.get("current_plan_scenario") or {}
    points = list(scenario.get("points") or [])
    routes = list(scenario.get("routes") or [])
    if not points or not routes:
        raise ValueError("Job does not contain current_plan_scenario points/routes")

    route_by_id = {str(route.get("route_id") or "").strip(): route for route in routes}
    enriched = copy.deepcopy(baseline)
    stats = {
        "route_count": 0,
        "stop_count": 0,
        "coordinate_stop_count": 0,
        "metric_route_count": 0,
        "missing_routes": [],
        "mismatched_routes": [],
    }

    for baseline_route in list(enriched.get("routes") or []):
        route_id = str(baseline_route.get("route_id") or "").strip()
        job_route = route_by_id.get(route_id)
        if not job_route:
            stats["missing_routes"].append(route_id)
            continue
        baseline_stops = _sorted_stops(baseline_route)
        job_nodes = list(job_route.get("nodes") or [])
        job_points = [points[int(node_id)] for node_id in job_nodes]
        baseline_addresses = [_stop_address(stop) for stop in baseline_stops]
        job_addresses = [_point_address(point) for point in job_points]
        if baseline_addresses != job_addresses:
            stats["mismatched_routes"].append(
                {
                    "route_id": route_id,
                    "baseline_addresses": baseline_addresses,
                    "job_addresses": job_addresses,
                }
            )
            continue
        enriched_stops = [
            _copy_point_fields(stop, point)
            for stop, point in zip(baseline_stops, job_points)
        ]
        baseline_route["stops"] = enriched_stops
        baseline_route.update(_route_metrics(job_route))
        baseline_route["baseline_metric_source"] = "route_audit_current_plan"
        baseline_route["baseline_coordinate_source"] = "route_audit_current_plan"
        baseline_route["source_job_id"] = job_id
        stats["route_count"] += 1
        stats["stop_count"] += len(enriched_stops)
        stats["coordinate_stop_count"] += sum(1 for stop in enriched_stops if stop.get("lat") is not None and stop.get("lng") is not None)
        if baseline_route.get("raw_osrm_time_s") is not None and baseline_route.get("distance_m") is not None:
            stats["metric_route_count"] += 1

    if stats["missing_routes"] or stats["mismatched_routes"]:
        raise ValueError(
            "Baseline/job mismatch: "
            f"missing={stats['missing_routes']} mismatched={len(stats['mismatched_routes'])}"
        )
    if stats["route_count"] != len(list(enriched.get("routes") or [])):
        raise ValueError("Not all baseline routes were enriched")
    if stats["coordinate_stop_count"] != stats["stop_count"]:
        raise ValueError("Not all baseline stops received coordinates")
    if stats["metric_route_count"] != stats["route_count"]:
        raise ValueError("Not all baseline routes received metrics")

    enriched["enriched_at"] = datetime.now().isoformat(timespec="seconds")
    enriched["enriched_from_job_id"] = job_id
    enriched["coordinate_source"] = "route_audit_current_plan"
    enriched["route_metric_source"] = "route_audit_current_plan"
    return enriched, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich a live-traffic baseline JSON from an existing route-audit current plan.")
    parser.add_argument("--baseline-path", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS_DIR)
    parser.add_argument("--sqlite-path", type=Path, default=DEFAULT_RUNTIME_DB_PATH)
    parser.add_argument("--write", action="store_true", help="Write the enriched baseline in place after creating a backup.")
    parser.add_argument("--output-path", type=Path, default=None, help="Optional output path for dry-run or alternate write.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    baseline_path = Path(args.baseline_path)
    if not baseline_path.is_absolute():
        baseline_path = args.baseline_dir / baseline_path
    baseline = _load_json(baseline_path)
    job = _load_runtime_job(args.job_id, args.jobs_dir, args.sqlite_path)
    enriched, stats = enrich_baseline_from_job(baseline, job, job_id=args.job_id)

    output_path = args.output_path
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.write:
        backup_path = baseline_path.with_name(
            f"{baseline_path.name}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        shutil.copy2(baseline_path, backup_path)
        baseline_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stats["backup_path"] = str(backup_path)
        stats["written_path"] = str(baseline_path)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if not args.write and output_path is None:
        print("dry_run=true")


if __name__ == "__main__":
    main()
