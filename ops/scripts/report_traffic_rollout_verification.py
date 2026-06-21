#!/usr/bin/env python3
"""Run the read-only traffic rollout verification sequence.

This command combines the rollout readiness/status gate with representative
job attribution checks. It never starts jobs, calls traffic providers, starts
OSRM, or mutates runtime files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import report_job_traffic_attribution  # noqa: E402
import report_traffic_rollout_status  # noqa: E402


DEFAULT_DIRECTIONS = ("To School", "From School")
DEFAULT_REQUIRED_SCENARIOS = ("free_optimization_baseline", "time_constrained")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _job_check(
    *,
    job_dir: Path,
    sqlite_path: Path | None,
    service_direction: str,
    required_scenarios: list[str],
    min_geo_route_ratio: float,
    latest_limit: int,
    latest_job_name_contains: str,
    latest_source_label_contains: str,
    latest_min_created_at: str,
    latest_min_finished_at: str,
    include_route_evidence: bool,
    include_top_matches: bool,
) -> dict[str, Any]:
    check: dict[str, Any] = {
        "service_direction": service_direction,
        "status": "failed",
        "job_id": "",
        "requirements": [],
    }
    try:
        job_id, selection = report_job_traffic_attribution.find_latest_job(
            job_dir,
            sqlite_path,
            status="succeeded",
            service_direction=service_direction,
            traffic_coefficient_mode="attributed",
            job_name_contains=latest_job_name_contains,
            source_label_contains=latest_source_label_contains,
            min_created_at=latest_min_created_at,
            min_finished_at=latest_min_finished_at,
            require_attribution=True,
            limit=latest_limit,
        )
    except LookupError as exc:
        check["reason"] = "no_matching_attributed_job"
        check["error"] = str(exc)
        return check

    summary = report_job_traffic_attribution.summarize_job(
        job_id,
        job_dir,
        sqlite_path,
        include_route_evidence=include_route_evidence,
        include_top_matches=include_top_matches,
    )
    requirements = report_job_traffic_attribution.evaluate_requirements(
        summary,
        require_attribution=True,
        required_scenarios=required_scenarios,
        min_geo_route_ratio=min_geo_route_ratio,
    )
    passed = all(bool(item.get("passed")) for item in requirements)
    check.update(
        {
            "status": "passed" if passed else "failed",
            "reason": "ok" if passed else "requirements_failed",
            "job_id": job_id,
            "selection": selection,
            "summary": summary,
            "requirements": requirements,
        }
    )
    return check


def build_verification(args: argparse.Namespace) -> dict[str, Any]:
    profiles = list(args.profile) if args.profile else list(report_traffic_rollout_status.report_traffic_rollout_readiness.DEFAULT_PROFILES)
    rollout = report_traffic_rollout_status.build_status(
        sample_dir=args.sample_dir,
        min_measured_at=str(args.min_measured_at or "").strip(),
        profiles=profiles,
        min_geo_ratio=float(args.min_geo_ratio),
        include_timers=not args.no_timers,
        include_osrm=not args.no_osrm,
        include_budget=not args.no_budget,
        local_timezone=str(args.local_timezone or report_traffic_rollout_status.DEFAULT_LOCAL_TIMEZONE),
    )
    directions = [str(item) for item in (args.service_direction or list(DEFAULT_DIRECTIONS))]
    required_scenarios = [str(item) for item in (args.require_scenario or list(DEFAULT_REQUIRED_SCENARIOS))]
    report: dict[str, Any] = {
        "status": "waiting",
        "rollout_status": rollout,
        "job_checks": [],
        "job_checks_skipped": False,
        "required_scenarios": required_scenarios,
        "service_directions": directions,
        "read_only": True,
        "provider_api_called": False,
        "osrm_started": False,
    }
    budget = rollout.get("api_budget") if isinstance(rollout.get("api_budget"), dict) else {}
    report["provider_api_called"] = bool(budget.get("provider_api_called"))
    report["osrm_started"] = bool(budget.get("osrm_started"))
    if rollout.get("status") != "ready" and not args.check_jobs_when_waiting:
        report["job_checks_skipped"] = True
        report["next_step"] = rollout.get("next_step") or "Wait for rollout status to become ready."
        return report

    job_checks = [
        _job_check(
            job_dir=args.job_dir,
            sqlite_path=args.sqlite_path,
            service_direction=direction,
            required_scenarios=required_scenarios,
            min_geo_route_ratio=float(args.min_geo_route_ratio),
            latest_limit=int(args.latest_limit),
            latest_job_name_contains=str(args.latest_job_name_contains or ""),
            latest_source_label_contains=str(args.latest_source_label_contains or ""),
            latest_min_created_at=str(args.latest_min_created_at or ""),
            latest_min_finished_at=str(args.latest_min_finished_at or ""),
            include_route_evidence=bool(args.include_route_evidence),
            include_top_matches=bool(args.include_top_matches),
        )
        for direction in directions
    ]
    all_jobs_passed = all(check.get("status") == "passed" for check in job_checks)
    if rollout.get("status") == "ready" and all_jobs_passed:
        status = "verified"
        next_step = "Traffic rollout verification passed; request operator approval before production rollout."
    elif rollout.get("status") != "ready":
        status = "waiting"
        next_step = rollout.get("next_step") or "Wait for rollout status to become ready."
    else:
        status = "failed"
        next_step = "Rerun representative attributed audits or inspect failed job attribution requirements."
    report.update({"status": status, "job_checks": job_checks, "next_step": next_step})
    return report


def _print_report(report: dict[str, Any]) -> None:
    print(f"status: {report.get('status')}")
    print(f"next_step: {report.get('next_step')}")
    rollout = report.get("rollout_status") if isinstance(report.get("rollout_status"), dict) else {}
    print(f"rollout_status: {rollout.get('status')}")
    gate = rollout.get("rollout_gate") if isinstance(rollout.get("rollout_gate"), dict) else {}
    print(
        "rollout_gate:",
        f"passed={gate.get('passed_requirement_count')}",
        f"failed={gate.get('failed_requirement_count')}",
        f"reasons={gate.get('failure_reason_counts')}",
    )
    if report.get("job_checks_skipped"):
        print("job_checks: skipped")
        return
    print("job_checks:")
    for check in _as_list(report.get("job_checks")):
        if not isinstance(check, dict):
            continue
        print(
            "-",
            check.get("service_direction"),
            check.get("status"),
            check.get("job_id") or "-",
            check.get("reason") or "-",
        )
        failed = [item for item in _as_list(check.get("requirements")) if not dict(item).get("passed")]
        for item in failed:
            item_dict = dict(item)
            print("  fail", item_dict.get("scenario") or item_dict.get("requirement"), item_dict.get("reason"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=report_traffic_rollout_status.report_traffic_rollout_readiness.report_live_traffic_readiness.DEFAULT_SAMPLE_DIR,
    )
    parser.add_argument("--job-dir", type=Path, default=report_job_traffic_attribution.DEFAULT_JOB_DIR)
    parser.add_argument("--sqlite-path", type=Path, default=report_job_traffic_attribution.DEFAULT_RUNTIME_DB_PATH)
    parser.add_argument(
        "--min-measured-at",
        default=report_traffic_rollout_status.report_traffic_rollout_readiness.DEFAULT_CUTOFF,
        help="Rollout cutoff passed through to the readiness gate.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        type=report_traffic_rollout_status.report_traffic_rollout_readiness._parse_profile,
        metavar="MARKET:CITY:PERIOD",
        help="Profile to require. Defaults to the current CN Shanghai/Suzhou rollout profiles.",
    )
    parser.add_argument(
        "--service-direction",
        action="append",
        default=[],
        help="Representative service direction to verify. Defaults to To School and From School.",
    )
    parser.add_argument(
        "--require-scenario",
        action="append",
        default=[],
        help="Scenario that must pass route attribution checks. Defaults to free baseline and time constrained.",
    )
    parser.add_argument("--min-geo-ratio", type=float, default=1.0)
    parser.add_argument("--min-geo-route-ratio", type=float, default=1.0)
    parser.add_argument("--latest-limit", type=int, default=report_job_traffic_attribution.DEFAULT_LATEST_SCAN_LIMIT)
    parser.add_argument("--latest-job-name-contains", default="")
    parser.add_argument("--latest-source-label-contains", default="")
    parser.add_argument("--latest-min-created-at", default="")
    parser.add_argument("--latest-min-finished-at", default="")
    parser.add_argument("--local-timezone", default=report_traffic_rollout_status.DEFAULT_LOCAL_TIMEZONE)
    parser.add_argument("--check-jobs-when-waiting", action="store_true")
    parser.add_argument("--include-route-evidence", action="store_true")
    parser.add_argument("--include-top-matches", action="store_true")
    parser.add_argument("--no-timers", action="store_true")
    parser.add_argument("--no-osrm", action="store_true")
    parser.add_argument("--no-budget", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_geo_ratio <= 1.0:
        raise SystemExit("--min-geo-ratio must be between 0 and 1")
    if not 0.0 <= args.min_geo_route_ratio <= 1.0:
        raise SystemExit("--min-geo-route-ratio must be between 0 and 1")
    if args.latest_limit <= 0:
        raise SystemExit("--latest-limit must be positive")
    report = build_verification(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(report)
    if report["status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
