#!/usr/bin/env python3
"""Summarize BRP live-traffic sample coverage and geo-readiness.

This script is read-only. It does not call traffic providers, start jobs, or
modify sample files. Operators can use it after timers/manual samples to verify
whether route-level traffic attribution has geo-ready observations.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SAMPLE_DIR = Path("/opt/brp/shared/runtime/traffic_samples")


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    summary = summarize(args.sample_dir)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_table(summary)


if __name__ == "__main__":
    main()
