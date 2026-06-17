#!/usr/bin/env python3
"""Summarize BRP live-traffic sample coverage and geo-readiness.

This script is read-only. It does not call traffic providers, start jobs, or
modify sample files. Operators can use it after timers/manual samples to verify
whether route-level traffic attribution has geo-ready observations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SAMPLE_DIR = Path("/opt/brp/shared/runtime/traffic_samples")


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _route_has_fingerprint(route: dict[str, Any]) -> bool:
    fingerprint = route.get("route_fingerprint") or route.get("traffic_fingerprint")
    if not isinstance(fingerprint, dict):
        return False
    return bool(fingerprint.get("cell_count") or fingerprint.get("corridor_cells") or fingerprint.get("cells"))


def _load_sample(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _sample_key(payload: dict[str, Any]) -> tuple[str, str, str]:
    market = str(payload.get("market") or payload.get("country") or "").strip() or "unknown"
    city = str(payload.get("city") or "").strip() or "unknown"
    period = str(payload.get("period") or "").strip() or "unknown"
    return market, city, period


def _empty_group() -> dict[str, Any]:
    return {
        "sample_file_count": 0,
        "route_sample_count": 0,
        "geo_route_sample_count": 0,
        "scale_only_route_sample_count": 0,
        "api_call_count": 0,
        "estimated_api_call_count": 0,
        "latest_measured_at": "",
        "latest_sample": "",
        "providers": set(),
        "weekdays": set(),
    }


def summarize(sample_dir: Path) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(_empty_group)
    total_files = 0
    unreadable_files = 0

    for path in sorted(sample_dir.glob("*.json")):
        payload = _load_sample(path)
        if payload is None:
            unreadable_files += 1
            continue
        if bool(payload.get("dry_run")):
            continue
        total_files += 1
        group = groups[_sample_key(payload)]
        routes = list(payload.get("routes") or [])
        geo_count = sum(1 for route in routes if isinstance(route, dict) and _route_has_fingerprint(route))
        route_count = len(routes)
        measured_at = str(payload.get("measured_at") or "")

        group["sample_file_count"] += 1
        group["route_sample_count"] += route_count
        group["geo_route_sample_count"] += geo_count
        group["scale_only_route_sample_count"] += max(0, route_count - geo_count)
        group["api_call_count"] += int(payload.get("api_call_count", 0) or 0)
        group["estimated_api_call_count"] += int(payload.get("estimated_api_call_count", 0) or 0)
        if measured_at and measured_at > str(group["latest_measured_at"] or ""):
            group["latest_measured_at"] = measured_at
            group["latest_sample"] = path.name
        provider = str(payload.get("provider") or "").strip()
        if provider:
            group["providers"].add(provider)
        weekday = str(payload.get("sample_weekday") or "").strip()
        if weekday:
            group["weekdays"].add(weekday)

    rows: list[dict[str, Any]] = []
    for (market, city, period), group in sorted(groups.items()):
        route_count = int(group["route_sample_count"])
        geo_count = int(group["geo_route_sample_count"])
        rows.append(
            {
                "market": market,
                "city": city,
                "period": period,
                "sample_file_count": int(group["sample_file_count"]),
                "route_sample_count": route_count,
                "geo_route_sample_count": geo_count,
                "scale_only_route_sample_count": int(group["scale_only_route_sample_count"]),
                "geo_route_sample_ratio": (geo_count / route_count) if route_count else 0.0,
                "api_call_count": int(group["api_call_count"]),
                "estimated_api_call_count": int(group["estimated_api_call_count"]),
                "latest_measured_at": str(group["latest_measured_at"] or ""),
                "latest_sample": str(group["latest_sample"] or ""),
                "providers": sorted(group["providers"]),
                "weekdays": sorted(group["weekdays"]),
            }
        )

    return {
        "sample_dir": str(sample_dir),
        "sample_file_count": total_files,
        "unreadable_file_count": unreadable_files,
        "groups": rows,
    }


def _parse_requirement(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            "requirements must use MARKET:CITY:PERIOD, for example CN:Shanghai:am_peak"
        )
    return parts[0], parts[1], parts[2]


def evaluate_requirements(
    summary: dict[str, Any],
    requirements: list[tuple[str, str, str]],
    *,
    min_geo_ratio: float,
) -> list[dict[str, Any]]:
    rows = {
        (_norm(row.get("market")), _norm(row.get("city")), _norm(row.get("period"))): row
        for row in summary.get("groups", [])
        if isinstance(row, dict)
    }
    results: list[dict[str, Any]] = []
    for market, city, period in requirements:
        row = rows.get((_norm(market), _norm(city), _norm(period)))
        if row is None:
            results.append(
                {
                    "market": market,
                    "city": city,
                    "period": period,
                    "passed": False,
                    "reason": "missing_sample_group",
                    "geo_route_sample_ratio": 0.0,
                    "geo_route_sample_count": 0,
                    "route_sample_count": 0,
                    "required_geo_route_sample_ratio": min_geo_ratio,
                }
            )
            continue
        geo_ratio = float(row.get("geo_route_sample_ratio") or 0.0)
        geo_count = int(row.get("geo_route_sample_count") or 0)
        route_count = int(row.get("route_sample_count") or 0)
        passed = route_count > 0 and geo_count > 0 and geo_ratio >= min_geo_ratio
        reason = "ok" if passed else "geo_ratio_below_requirement"
        if route_count <= 0:
            reason = "no_route_samples"
        elif geo_count <= 0:
            reason = "no_geo_route_samples"
        results.append(
            {
                "market": row.get("market") or market,
                "city": row.get("city") or city,
                "period": row.get("period") or period,
                "passed": passed,
                "reason": reason,
                "geo_route_sample_ratio": geo_ratio,
                "geo_route_sample_count": geo_count,
                "route_sample_count": route_count,
                "required_geo_route_sample_ratio": min_geo_ratio,
                "latest_measured_at": row.get("latest_measured_at") or "",
                "latest_sample": row.get("latest_sample") or "",
            }
        )
    return results


def _print_table(summary: dict[str, Any]) -> None:
    print(f"sample_dir: {summary['sample_dir']}")
    print(f"sample_files: {summary['sample_file_count']} unreadable: {summary['unreadable_file_count']}")
    header = (
        "market city period files routes geo scale_only geo_ratio api_calls "
        "latest_measured_at latest_sample"
    )
    print(header)
    for row in summary["groups"]:
        print(
            row["market"],
            row["city"],
            row["period"],
            row["sample_file_count"],
            row["route_sample_count"],
            row["geo_route_sample_count"],
            row["scale_only_route_sample_count"],
            f"{row['geo_route_sample_ratio']:.3f}",
            row["api_call_count"],
            row["latest_measured_at"],
            row["latest_sample"],
        )
    requirements = summary.get("requirements") or []
    if requirements:
        print("requirements")
        for result in requirements:
            status = "ok" if result["passed"] else "fail"
            print(
                status,
                result["market"],
                result["city"],
                result["period"],
                f"geo_ratio={float(result['geo_route_sample_ratio']):.3f}",
                f"geo={result['geo_route_sample_count']}",
                f"routes={result['route_sample_count']}",
                result["reason"],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--require-geo",
        action="append",
        default=[],
        type=_parse_requirement,
        metavar="MARKET:CITY:PERIOD",
        help="Require a market/city/period group to have geo-ready route samples.",
    )
    parser.add_argument(
        "--min-geo-ratio",
        type=float,
        default=1.0,
        help="Minimum geo-ready route sample ratio for --require-geo checks. Default: 1.0.",
    )
    args = parser.parse_args()

    if not 0.0 <= args.min_geo_ratio <= 1.0:
        parser.error("--min-geo-ratio must be between 0 and 1")

    summary = summarize(args.sample_dir)
    requirement_results = evaluate_requirements(
        summary,
        list(args.require_geo),
        min_geo_ratio=float(args.min_geo_ratio),
    )
    if requirement_results:
        summary["requirements"] = requirement_results
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_table(summary)
    if any(not result["passed"] for result in requirement_results):
        print("One or more geo-readiness requirements failed.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
