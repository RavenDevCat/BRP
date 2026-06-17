#!/usr/bin/env python3
"""Gate traffic attribution rollout readiness after a known rollout timestamp.

This script is read-only. It does not call traffic providers, start jobs, start
OSRM, or modify sample files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import report_live_traffic_readiness  # noqa: E402


DEFAULT_CUTOFF = "2026-06-17T19:00:00+08:00"
DEFAULT_PROFILES = (
    ("CN", "Shanghai", "am_peak"),
    ("CN", "Shanghai", "pm_peak"),
    ("CN", "Suzhou", "am_peak"),
    ("CN", "Suzhou", "pm_peak"),
)


def _parse_profile(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            "profiles must use MARKET:CITY:PERIOD, for example CN:Shanghai:am_peak"
        )
    return parts[0], parts[1], parts[2]


def build_report(
    sample_dir: Path,
    *,
    min_measured_at: str,
    profiles: list[tuple[str, str, str]],
    min_geo_ratio: float,
) -> dict[str, Any]:
    summary = report_live_traffic_readiness.summarize(
        sample_dir,
        min_measured_at=min_measured_at,
    )
    requirements = report_live_traffic_readiness.evaluate_requirements(
        summary,
        profiles,
        min_geo_ratio=min_geo_ratio,
    )
    summary["requirements"] = requirements
    passed = all(bool(result.get("passed")) for result in requirements)
    return {
        "status": "ok" if passed else "failed",
        "rollout": {
            "min_measured_at": min_measured_at,
            "profiles": [
                {"market": market, "city": city, "period": period}
                for market, city, period in profiles
            ],
            "min_geo_ratio": min_geo_ratio,
        },
        "readiness": summary,
    }


def _print_report(report: dict[str, Any]) -> None:
    rollout = dict(report.get("rollout") or {})
    readiness = dict(report.get("readiness") or {})
    print(f"status: {report.get('status')}")
    print(f"min_measured_at: {rollout.get('min_measured_at')}")
    print(f"sample_dir: {readiness.get('sample_dir')}")
    print(
        "sample_files: "
        f"{readiness.get('sample_file_count', 0)} "
        f"filtered: {readiness.get('filtered_file_count', 0)} "
        f"unreadable: {readiness.get('unreadable_file_count', 0)}"
    )
    print("requirements")
    for result in readiness.get("requirements", []):
        if not isinstance(result, dict):
            continue
        status = "ok" if result.get("passed") else "fail"
        print(
            status,
            result.get("market"),
            result.get("city"),
            result.get("period"),
            f"geo_ratio={float(result.get('geo_route_sample_ratio') or 0.0):.3f}",
            f"geo={int(result.get('geo_route_sample_count') or 0)}",
            f"routes={int(result.get('route_sample_count') or 0)}",
            result.get("reason"),
            result.get("latest_sample") or "",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-dir",
        type=Path,
        default=report_live_traffic_readiness.DEFAULT_SAMPLE_DIR,
    )
    parser.add_argument(
        "--min-measured-at",
        default=os.environ.get("BRP_TRAFFIC_ROLLOUT_MIN_MEASURED_AT", DEFAULT_CUTOFF),
        help=(
            "Only include samples at or after this measured_at value. "
            f"Default: env BRP_TRAFFIC_ROLLOUT_MIN_MEASURED_AT or {DEFAULT_CUTOFF}."
        ),
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        type=_parse_profile,
        metavar="MARKET:CITY:PERIOD",
        help="Profile to require. Defaults to Shanghai/Suzhou AM/PM CN rollout profiles.",
    )
    parser.add_argument(
        "--min-geo-ratio",
        type=float,
        default=1.0,
        help="Minimum geo-ready route sample ratio. Default: 1.0.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_geo_ratio <= 1.0:
        raise SystemExit("--min-geo-ratio must be between 0 and 1")
    profiles = list(args.profile) if args.profile else list(DEFAULT_PROFILES)
    report = build_report(
        args.sample_dir,
        min_measured_at=str(args.min_measured_at or "").strip(),
        profiles=profiles,
        min_geo_ratio=float(args.min_geo_ratio),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(report)
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
