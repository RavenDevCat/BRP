#!/usr/bin/env python3
"""Summarize reusable route-network traffic profiles from live samples.

This script is read-only. It scans stored live-traffic sample JSON files and
builds profile/corridor/area summaries that explain which historical route
segments can support route-level traffic attribution for future jobs.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SAMPLE_DIR = Path("/opt/brp/shared/runtime/traffic_samples")
DEFAULT_TOP_BUCKETS = 20
DEFAULT_MIN_BUCKET_ROUTE_COUNT = 3
KOREA_TRAFFIC_METRO_CITY = "Seoul Metro"
KOREA_TRAFFIC_METRO_CITY_ALIASES = {
    "seoul",
    "seoul si",
    "seoul-si",
    "seoul special city",
    "서울",
    "서울시",
    "서울특별시",
    "incheon",
    "inchon",
    "incheon si",
    "incheon-si",
    "incheon metropolitan city",
    "인천",
    "인천시",
    "인천광역시",
    "gyeonggi",
    "gyeonggi do",
    "gyeonggi-do",
    "gyeonggi province",
    "kyonggi",
    "경기",
    "경기도",
    "seongnam",
    "seongnam si",
    "seongnam-si",
    "bundang",
    "bundang gu",
    "bundang-gu",
    "성남",
    "성남시",
    "분당",
    "분당구",
    "gimpo",
    "gimpo si",
    "gimpo-si",
    "김포",
    "김포시",
    "suwon",
    "suwon si",
    "suwon-si",
    "수원",
    "수원시",
    "yongin",
    "yongin si",
    "yongin-si",
    "용인",
    "용인시",
    "goyang",
    "goyang si",
    "goyang-si",
    "고양",
    "고양시",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normal_country(value: Any) -> str:
    normalized = _normal_text(value).casefold()
    if normalized in {"kr", "korea", "south korea", "republic of korea", "korea, republic of", "대한민국", "한국"}:
        return "south korea"
    return normalized


def _traffic_city_for_profile(market: Any, country: Any, city: Any) -> str:
    normalized_city = " ".join(_normal_text(city).casefold().replace("_", " ").replace("-", " ").split())
    if (
        _normal_country(country) == "south korea"
        or _normal_country(market) == "south korea"
    ) and normalized_city in KOREA_TRAFFIC_METRO_CITY_ALIASES:
        return KOREA_TRAFFIC_METRO_CITY
    return _normal_text(city) or "unknown"


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_at_or_after(value: Any, minimum: str) -> bool:
    minimum = str(minimum or "").strip()
    if not minimum:
        return True
    actual = _parse_timestamp(value)
    threshold = _parse_timestamp(minimum)
    if actual is None or threshold is None:
        return False
    return actual >= threshold


def _load_sample(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    payload["_path"] = str(path)
    measured_at = _parse_timestamp(payload.get("measured_at"))
    if measured_at is not None:
        payload["_measured_at"] = measured_at
    return payload


def _iter_samples(sample_dir: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not sample_dir.exists():
        return samples
    for path in sorted(sample_dir.glob("*.json")):
        sample = _load_sample(path)
        if sample is not None:
            samples.append(sample)
    return samples


def _profile_key(sample: dict[str, Any]) -> str:
    market = _normal_text(sample.get("market") or sample.get("country") or "unknown")
    city = _traffic_city_for_profile(market, sample.get("country"), sample.get("city") or "unknown")
    period = _normal_text(sample.get("period") or "unknown")
    return f"{market}:{city}:{period}"


def _canonical_profile_key(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(":")]
    if len(parts) != 3:
        return str(value or "").strip()
    market, city, period = parts
    return f"{market}:{_traffic_city_for_profile(market, market, city)}:{period}"


def _normal_profile_key(value: str) -> str:
    return _canonical_profile_key(value).casefold()


def _profile_key_matches(profile_key: str, filters: list[str]) -> bool:
    if not filters:
        return True
    normal_key = _normal_profile_key(profile_key)
    return any(normal_key == _normal_profile_key(str(item or "")) for item in filters)


def _route_factor(route: dict[str, Any]) -> float:
    factor = _float(route.get("factor"))
    if factor > 0:
        return factor
    osrm_s = _float(route.get("osrm_duration_s"))
    api_s = _float(
        route.get("api_duration_s")
        or route.get("amap_duration_s")
        or route.get("google_duration_s")
        or route.get("kakao_duration_s")
    )
    if osrm_s > 0 and api_s > 0:
        return api_s / osrm_s
    return 0.0


def _route_fingerprint(route: dict[str, Any]) -> dict[str, Any]:
    fingerprint = route.get("route_fingerprint")
    if not isinstance(fingerprint, dict):
        fingerprint = route.get("traffic_fingerprint")
    return dict(fingerprint) if isinstance(fingerprint, dict) else {}


def _has_geo_fingerprint(fingerprint: dict[str, Any]) -> bool:
    return bool(
        _int(fingerprint.get("cell_count"))
        or _as_list(fingerprint.get("cells"))
        or _as_list(fingerprint.get("corridor_cells"))
        or _as_list(fingerprint.get("stop_cells"))
        or _as_dict(fingerprint.get("center"))
    )


def _grid_cell_from_center(fingerprint: dict[str, Any]) -> str:
    center = _as_dict(fingerprint.get("center"))
    lat = _float(center.get("lat"), default=float("nan"))
    lng = _float(center.get("lng"), default=float("nan"))
    grid = _float(fingerprint.get("grid_degrees"), default=0.01)
    if not math.isfinite(lat) or not math.isfinite(lng) or grid <= 0:
        cells = _as_list(fingerprint.get("corridor_cells")) or _as_list(fingerprint.get("cells"))
        return str(cells[0]) if cells else ""
    return f"{math.floor(lat / grid)}:{math.floor(lng / grid)}"


def _route_rows_from_sample(sample: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    profile_key = _profile_key(sample)
    measured_at = sample.get("measured_at") or ""
    local_date = str(sample.get("local_date") or sample.get("sample_date") or "")[:10]
    for route in _as_list(sample.get("routes")):
        route = _as_dict(route)
        factor = _route_factor(route)
        osrm_s = _float(route.get("osrm_duration_s"))
        if factor <= 0 or osrm_s <= 0:
            continue
        fingerprint = _route_fingerprint(route)
        corridor_cells = [
            str(cell)
            for cell in (_as_list(fingerprint.get("corridor_cells")) or _as_list(fingerprint.get("cells")))
            if str(cell)
        ]
        stop_cells = [str(cell) for cell in _as_list(fingerprint.get("stop_cells")) if str(cell)]
        rows.append(
            {
                "profile_key": profile_key,
                "sample_path": str(sample.get("_path") or ""),
                "source_id": str(route.get("source_id") or sample.get("source_id") or ""),
                "route_id": str(route.get("route_id") or ""),
                "provider": str(route.get("provider") or sample.get("provider") or ""),
                "measured_at": measured_at,
                "local_date": local_date,
                "factor": float(factor),
                "osrm_duration_s": float(osrm_s),
                "api_duration_s": _float(
                    route.get("api_duration_s")
                    or route.get("amap_duration_s")
                    or route.get("google_duration_s")
                    or route.get("kakao_duration_s")
                ),
                "stop_count": _int(route.get("stop_count")),
                "has_geo_fingerprint": _has_geo_fingerprint(fingerprint),
                "corridor_cells": corridor_cells[:500],
                "stop_cells": stop_cells[:200],
                "center_cell": _grid_cell_from_center(fingerprint),
            }
        )
    return rows


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])
    weight = rank - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _factor_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    factors = [float(row["factor"]) for row in rows if float(row.get("factor", 0.0) or 0.0) > 0]
    weights = [max(60.0, float(row.get("osrm_duration_s", 0.0) or 0.0)) for row in rows]
    weighted_denominator = sum(weights)
    weighted_mean = (
        sum(float(row["factor"]) * max(60.0, float(row.get("osrm_duration_s", 0.0) or 0.0)) for row in rows)
        / weighted_denominator
        if rows and weighted_denominator > 0
        else 0.0
    )
    return {
        "count": len(factors),
        "min": min(factors) if factors else 0.0,
        "max": max(factors) if factors else 0.0,
        "mean": statistics.fmean(factors) if factors else 0.0,
        "median": statistics.median(factors) if factors else 0.0,
        "p90": _percentile(factors, 0.9),
        "weighted_mean": float(weighted_mean),
    }


def _confidence(route_count: int, local_date_count: int) -> str:
    if route_count >= 12 and local_date_count >= 3:
        return "high"
    if route_count >= 5 and local_date_count >= 2:
        return "medium"
    return "low"


def _summarize_bucket(
    cell: str,
    rows: list[dict[str, Any]],
    *,
    bucket_type: str,
    min_bucket_route_count: int,
) -> dict[str, Any]:
    dates = sorted({str(row.get("local_date") or "") for row in rows if str(row.get("local_date") or "")})
    providers = Counter(str(row.get("provider") or "unknown") for row in rows)
    return {
        "cell": cell,
        "bucket_type": bucket_type,
        "route_sample_count": len(rows),
        "sample_file_count": len({str(row.get("sample_path") or "") for row in rows}),
        "local_date_count": len(dates),
        "local_dates": dates[:10],
        "confidence": _confidence(len(rows), len(dates)),
        "usable": len(rows) >= min_bucket_route_count,
        "factor": _factor_summary(rows),
        "providers": dict(providers),
        "source_ids": sorted({str(row.get("source_id") or "") for row in rows if str(row.get("source_id") or "")})[:8],
        "route_ids": sorted({str(row.get("route_id") or "") for row in rows if str(row.get("route_id") or "")})[:12],
    }


def _bucket_summaries(
    rows: list[dict[str, Any]],
    *,
    bucket_type: str,
    min_bucket_route_count: int,
    top_buckets: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cells = [str(row.get("center_cell") or "")] if bucket_type == "center" else list(row.get("corridor_cells") or [])
        for cell in sorted({cell for cell in cells if cell}):
            grouped[cell].append(row)
    summaries = [
        _summarize_bucket(cell, bucket_rows, bucket_type=bucket_type, min_bucket_route_count=min_bucket_route_count)
        for cell, bucket_rows in grouped.items()
    ]
    summaries.sort(
        key=lambda item: (
            bool(item["usable"]),
            int(item["route_sample_count"]),
            float(_as_dict(item["factor"]).get("weighted_mean") or 0.0),
        ),
        reverse=True,
    )
    return summaries[:top_buckets]


def _summarize_profile(
    profile_key: str,
    rows: list[dict[str, Any]],
    *,
    min_bucket_route_count: int,
    top_buckets: int,
) -> dict[str, Any]:
    geo_rows = [row for row in rows if bool(row.get("has_geo_fingerprint"))]
    local_dates = sorted({str(row.get("local_date") or "") for row in rows if str(row.get("local_date") or "")})
    sample_paths = sorted({str(row.get("sample_path") or "") for row in rows if str(row.get("sample_path") or "")})
    corridor_buckets = _bucket_summaries(
        geo_rows,
        bucket_type="corridor",
        min_bucket_route_count=min_bucket_route_count,
        top_buckets=top_buckets,
    )
    center_buckets = _bucket_summaries(
        geo_rows,
        bucket_type="center",
        min_bucket_route_count=min_bucket_route_count,
        top_buckets=top_buckets,
    )
    usable_corridor_count = sum(1 for item in corridor_buckets if bool(item.get("usable")))
    usable_center_count = sum(1 for item in center_buckets if bool(item.get("usable")))
    return {
        "profile_key": profile_key,
        "route_sample_count": len(rows),
        "geo_route_sample_count": len(geo_rows),
        "scale_only_route_sample_count": max(0, len(rows) - len(geo_rows)),
        "geo_route_sample_ratio": (len(geo_rows) / len(rows)) if rows else 0.0,
        "sample_file_count": len(sample_paths),
        "sample_files": [Path(path).name for path in sample_paths[:12]],
        "local_date_count": len(local_dates),
        "local_dates": local_dates[:20],
        "factor": _factor_summary(rows),
        "provider_counts": dict(Counter(str(row.get("provider") or "unknown") for row in rows)),
        "corridor_bucket_count": len(corridor_buckets),
        "usable_corridor_bucket_count": usable_corridor_count,
        "center_bucket_count": len(center_buckets),
        "usable_center_bucket_count": usable_center_count,
        "confidence": _confidence(len(geo_rows), len(local_dates)),
        "top_corridor_buckets": corridor_buckets,
        "top_center_buckets": center_buckets,
    }


def build_network_report(
    sample_dir: Path,
    *,
    profiles: list[str] | None = None,
    require_profiles: list[str] | None = None,
    min_measured_at: str = "",
    min_geo_route_ratio: float = 0.0,
    min_bucket_route_count: int = DEFAULT_MIN_BUCKET_ROUTE_COUNT,
    top_buckets: int = DEFAULT_TOP_BUCKETS,
) -> dict[str, Any]:
    profiles = list(profiles or [])
    require_profiles = list(require_profiles or [])
    samples = []
    route_rows: list[dict[str, Any]] = []
    for sample in _iter_samples(sample_dir):
        if bool(sample.get("dry_run")):
            continue
        measured_at = sample.get("measured_at") or sample.get("_measured_at")
        if not _timestamp_at_or_after(measured_at, min_measured_at):
            continue
        profile_key = _profile_key(sample)
        if not _profile_key_matches(profile_key, profiles):
            continue
        sample_rows = _route_rows_from_sample(sample)
        if not sample_rows:
            continue
        samples.append(sample)
        route_rows.extend(sample_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in route_rows:
        grouped[str(row["profile_key"])].append(row)

    profile_summaries = [
        _summarize_profile(
            profile_key,
            grouped_rows,
            min_bucket_route_count=min_bucket_route_count,
            top_buckets=top_buckets,
        )
        for profile_key, grouped_rows in grouped.items()
    ]
    profile_summaries.sort(key=lambda item: item["profile_key"])
    by_key = {_normal_profile_key(str(item["profile_key"])): item for item in profile_summaries}
    required_results: list[dict[str, Any]] = []
    for required in require_profiles:
        key = _canonical_profile_key(str(required or "").strip())
        summary = by_key.get(_normal_profile_key(key))
        if summary is None:
            required_results.append({"profile_key": key, "passed": False, "reason": "missing_profile"})
            continue
        ratio = float(summary.get("geo_route_sample_ratio") or 0.0)
        passed = ratio >= min_geo_route_ratio and int(summary.get("geo_route_sample_count") or 0) > 0
        required_results.append(
            {
                "profile_key": key,
                "passed": bool(passed),
                "reason": "ok" if passed else "geo_route_ratio_below_requirement",
                "geo_route_sample_ratio": ratio,
                "geo_route_sample_count": int(summary.get("geo_route_sample_count") or 0),
                "required_geo_route_sample_ratio": float(min_geo_route_ratio),
            }
        )
    blocker_count = sum(1 for item in required_results if not bool(item.get("passed")))
    return {
        "read_only": True,
        "provider_api_called": False,
        "osrm_started": False,
        "sample_dir": str(sample_dir),
        "min_measured_at": str(min_measured_at or ""),
        "profile_filter_count": len(profiles),
        "sample_file_count": len(samples),
        "route_sample_count": len(route_rows),
        "geo_route_sample_count": sum(1 for row in route_rows if bool(row.get("has_geo_fingerprint"))),
        "profile_count": len(profile_summaries),
        "profiles": profile_summaries,
        "required_profiles": required_results,
        "blocker_count": blocker_count,
        "status": "ready" if blocker_count == 0 else "blocked",
    }


def _print_text(report: dict[str, Any]) -> None:
    print(
        "network_profile_status",
        f"status={report['status']}",
        f"profiles={report['profile_count']}",
        f"routes={report['route_sample_count']}",
        f"geo={report['geo_route_sample_count']}",
        f"provider_api_called={str(report['provider_api_called']).lower()}",
        f"osrm_started={str(report['osrm_started']).lower()}",
    )
    for profile in _as_list(report.get("profiles")):
        factor = _as_dict(profile.get("factor"))
        print(
            "profile",
            profile["profile_key"],
            f"routes={profile['route_sample_count']}",
            f"geo={profile['geo_route_sample_count']}",
            f"geo_ratio={float(profile['geo_route_sample_ratio']):.3f}",
            f"weighted_factor={float(factor.get('weighted_mean') or 0.0):.3f}",
            f"corridor_buckets={profile['corridor_bucket_count']}",
            f"usable_corridor_buckets={profile['usable_corridor_bucket_count']}",
            f"confidence={profile['confidence']}",
        )
        for bucket in _as_list(profile.get("top_corridor_buckets"))[:5]:
            bucket_factor = _as_dict(bucket.get("factor"))
            print(
                "  corridor",
                bucket["cell"],
                f"routes={bucket['route_sample_count']}",
                f"factor={float(bucket_factor.get('weighted_mean') or 0.0):.3f}",
                f"confidence={bucket['confidence']}",
                f"usable={str(bucket['usable']).lower()}",
            )
    for result in _as_list(report.get("required_profiles")):
        if bool(result.get("passed")):
            continue
        print(
            "requirement_failed",
            result.get("profile_key", ""),
            f"reason={result.get('reason', '')}",
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", default=str(DEFAULT_SAMPLE_DIR))
    parser.add_argument("--profile", action="append", default=[], help="Only include a profile key such as CN:Shanghai:pm_peak.")
    parser.add_argument(
        "--require-profile",
        action="append",
        default=[],
        help="Fail if a profile key is missing or below --min-geo-route-ratio.",
    )
    parser.add_argument("--min-measured-at", default="", help="Only include samples measured at or after this ISO timestamp.")
    parser.add_argument("--min-geo-route-ratio", type=float, default=0.0)
    parser.add_argument("--min-bucket-route-count", type=int, default=DEFAULT_MIN_BUCKET_ROUTE_COUNT)
    parser.add_argument("--top-buckets", type=int, default=DEFAULT_TOP_BUCKETS)
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not 0.0 <= float(args.min_geo_route_ratio) <= 1.0:
        parser.error("--min-geo-route-ratio must be between 0 and 1")
    report = build_network_report(
        Path(args.sample_dir),
        profiles=list(args.profile or []),
        require_profiles=list(args.require_profile or []),
        min_measured_at=str(args.min_measured_at or ""),
        min_geo_route_ratio=float(args.min_geo_route_ratio),
        min_bucket_route_count=max(1, int(args.min_bucket_route_count)),
        top_buckets=max(1, int(args.top_buckets)),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 1 if int(report.get("blocker_count") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
