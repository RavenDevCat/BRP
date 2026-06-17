#!/usr/bin/env python3
"""Summarize traffic rollout readiness, timers, and OSRM manager health.

This script is read-only. It does not call traffic providers, start jobs, start
OSRM, or modify sample files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import report_traffic_rollout_readiness  # noqa: E402


DEFAULT_TIMER_UNITS = (
    "brp-live-traffic-am.timer",
    "brp-live-traffic-pm.timer",
    "brp-live-traffic-suzhou-am.timer",
    "brp-live-traffic-suzhou-pm.timer",
    "brp-osrm-cleanup.timer",
)

DEFAULT_SERVICE_UNITS = (
    "brp-live-traffic-am.service",
    "brp-live-traffic-pm.service",
    "brp-live-traffic-suzhou-am.service",
    "brp-live-traffic-suzhou-pm.service",
    "brp-osrm-cleanup.service",
)


def _run_command(command: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def _parse_show_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def collect_timer_status(timer_units: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit in timer_units:
        try:
            result = _run_command(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "Result",
                    "-p",
                    "NextElapseUSecRealtime",
                    "-p",
                    "LastTriggerUSec",
                    "--no-pager",
                ]
            )
        except Exception as exc:
            rows.append({"unit": unit, "available": False, "error": str(exc)})
            continue
        if result.returncode != 0:
            rows.append(
                {
                    "unit": unit,
                    "available": False,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
            continue
        props = _parse_show_properties(result.stdout)
        rows.append(
            {
                "unit": unit,
                "available": True,
                "active_state": props.get("ActiveState", ""),
                "sub_state": props.get("SubState", ""),
                "result": props.get("Result", ""),
                "next_elapse": props.get("NextElapseUSecRealtime", ""),
                "last_trigger": props.get("LastTriggerUSec", ""),
            }
        )
    return rows


def _status_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def collect_service_status(service_units: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit in service_units:
        try:
            result = _run_command(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "Result",
                    "-p",
                    "ExecMainStatus",
                    "-p",
                    "ExecMainCode",
                    "-p",
                    "ExecMainStartTimestamp",
                    "-p",
                    "ExecMainExitTimestamp",
                    "--no-pager",
                ]
            )
        except Exception as exc:
            rows.append({"unit": unit, "available": False, "problem": True, "error": str(exc)})
            continue
        if result.returncode != 0:
            rows.append(
                {
                    "unit": unit,
                    "available": False,
                    "problem": True,
                    "error": (result.stderr or result.stdout or "").strip(),
                }
            )
            continue
        props = _parse_show_properties(result.stdout)
        exec_status = _status_int(props.get("ExecMainStatus"))
        result_status = props.get("Result", "")
        active_state = props.get("ActiveState", "")
        problem = bool(
            result_status not in {"", "success"}
            or active_state == "failed"
            or (exec_status is not None and exec_status != 0)
        )
        rows.append(
            {
                "unit": unit,
                "available": True,
                "active_state": active_state,
                "sub_state": props.get("SubState", ""),
                "result": result_status,
                "exec_main_code": props.get("ExecMainCode", ""),
                "exec_main_status": exec_status,
                "exec_main_start": props.get("ExecMainStartTimestamp", ""),
                "exec_main_exit": props.get("ExecMainExitTimestamp", ""),
                "problem": problem,
            }
        )
    return rows


def collect_osrm_manager_status() -> dict[str, Any]:
    script = SCRIPT_DIR / "report_osrm_manager.py"
    if not script.exists():
        return {"available": False, "error": f"missing {script}"}
    try:
        result = _run_command([sys.executable, str(script), "status", "--json"], timeout=15)
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if result.returncode != 0:
        return {"available": False, "error": (result.stderr or result.stdout or "").strip()}
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {"available": False, "error": f"invalid json: {exc}"}
    regions = [row for row in payload.get("regions", []) if isinstance(row, dict)]
    locks = [row for row in payload.get("locks", []) if isinstance(row, dict)]
    running_regions = [
        row.get("region")
        for row in regions
        if isinstance(row.get("container_status"), dict) and row["container_status"].get("running")
    ]
    return {
        "available": True,
        "on_demand_enabled": bool(payload.get("on_demand_enabled")),
        "lock_wait_seconds": payload.get("lock_wait_seconds"),
        "max_running_regions": payload.get("max_running_regions"),
        "available_memory_mb": payload.get("available_memory_mb"),
        "lock_count": len(locks),
        "stale_lock_count": sum(1 for row in locks if row.get("stale")),
        "running_region_count": len(running_regions),
        "running_regions": running_regions,
    }


def summarize_rollout_gate(gate: dict[str, Any]) -> dict[str, Any]:
    readiness = dict(gate.get("readiness") or {})
    requirements = [row for row in readiness.get("requirements", []) if isinstance(row, dict)]
    reason_counts = Counter(str(row.get("reason") or "") for row in requirements if not row.get("passed"))
    passed_count = sum(1 for row in requirements if row.get("passed"))
    return {
        "status": gate.get("status"),
        "passed_requirement_count": passed_count,
        "failed_requirement_count": max(0, len(requirements) - passed_count),
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "sample_file_count": readiness.get("sample_file_count"),
        "filtered_file_count": readiness.get("filtered_file_count"),
        "requirements": requirements,
    }


def build_status(
    *,
    sample_dir: Path,
    min_measured_at: str,
    profiles: list[tuple[str, str, str]],
    min_geo_ratio: float,
    include_timers: bool,
    include_osrm: bool,
) -> dict[str, Any]:
    gate = report_traffic_rollout_readiness.build_report(
        sample_dir,
        min_measured_at=min_measured_at,
        profiles=profiles,
        min_geo_ratio=min_geo_ratio,
    )
    rollout_summary = summarize_rollout_gate(gate)
    timer_status = collect_timer_status(list(DEFAULT_TIMER_UNITS)) if include_timers else []
    service_status = collect_service_status(list(DEFAULT_SERVICE_UNITS)) if include_timers else []
    osrm_status = collect_osrm_manager_status() if include_osrm else {"available": False, "skipped": True}
    timer_problem_count = sum(1 for row in timer_status if not row.get("available") or row.get("active_state") == "failed")
    service_problem_count = sum(1 for row in service_status if bool(row.get("problem")))
    osrm_problem = bool(
        osrm_status.get("available")
        and (int(osrm_status.get("lock_count") or 0) > 0 or int(osrm_status.get("stale_lock_count") or 0) > 0)
    )
    status = (
        "ready"
        if gate.get("status") == "ok" and timer_problem_count == 0 and service_problem_count == 0 and not osrm_problem
        else "waiting"
    )
    return {
        "status": status,
        "rollout_gate": rollout_summary,
        "timers": {
            "problem_count": timer_problem_count,
            "items": timer_status,
        },
        "services": {
            "problem_count": service_problem_count,
            "items": service_status,
        },
        "osrm_manager": osrm_status,
        "next_step": (
            "Run representative route audits and job attribution gates."
            if status == "ready"
            else "Wait for fresh timer samples or fix reported timer/OSRM issues."
        ),
    }


def _print_status(report: dict[str, Any]) -> None:
    print(f"status: {report.get('status')}")
    print(f"next_step: {report.get('next_step')}")
    gate = dict(report.get("rollout_gate") or {})
    print(
        "rollout_gate:",
        gate.get("status"),
        f"passed={gate.get('passed_requirement_count')}",
        f"failed={gate.get('failed_requirement_count')}",
        f"reasons={gate.get('failure_reason_counts')}",
    )
    timers = dict(report.get("timers") or {})
    print(f"timer_problem_count: {timers.get('problem_count')}")
    services = dict(report.get("services") or {})
    print(f"service_problem_count: {services.get('problem_count')}")
    osrm = dict(report.get("osrm_manager") or {})
    print(
        "osrm_manager:",
        f"available={osrm.get('available')}",
        f"locks={osrm.get('lock_count')}",
        f"running={osrm.get('running_region_count')}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=report_traffic_rollout_readiness.report_live_traffic_readiness.DEFAULT_SAMPLE_DIR,
    )
    parser.add_argument(
        "--min-measured-at",
        default=report_traffic_rollout_readiness.DEFAULT_CUTOFF,
        help="Rollout cutoff passed through to the readiness gate.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        type=report_traffic_rollout_readiness._parse_profile,
        metavar="MARKET:CITY:PERIOD",
        help="Profile to require. Defaults to the current CN Shanghai/Suzhou rollout profiles.",
    )
    parser.add_argument("--min-geo-ratio", type=float, default=1.0)
    parser.add_argument("--no-timers", action="store_true", help="Skip systemd timer status collection.")
    parser.add_argument("--no-osrm", action="store_true", help="Skip OSRM manager status collection.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_geo_ratio <= 1.0:
        raise SystemExit("--min-geo-ratio must be between 0 and 1")
    profiles = list(args.profile) if args.profile else list(report_traffic_rollout_readiness.DEFAULT_PROFILES)
    report = build_status(
        sample_dir=args.sample_dir,
        min_measured_at=str(args.min_measured_at or "").strip(),
        profiles=profiles,
        min_geo_ratio=float(args.min_geo_ratio),
        include_timers=not args.no_timers,
        include_osrm=not args.no_osrm,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_status(report)
    if report["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
