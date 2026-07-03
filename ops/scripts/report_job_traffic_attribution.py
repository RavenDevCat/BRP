#!/usr/bin/env python3
"""Inspect route-level traffic attribution on a completed BRP job.

This script is read-only. It does not start jobs, call traffic providers, or
modify runtime files. Use it after a representative Route Audit run to verify
whether optimized scenarios actually used geo-attributed route traffic factors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_JOB_DIR = Path("/opt/brp/shared/runtime/jobs")
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
DEFAULT_LATEST_SCAN_LIMIT = 200


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _job_path(job: str, job_dir: Path) -> Path:
    candidate = Path(job)
    if candidate.exists():
        return candidate
    if candidate.suffix != ".json":
        candidate = candidate.with_suffix(".json")
    if candidate.is_absolute():
        return candidate
    return job_dir / candidate


def _load_job_json(job: str, job_dir: Path) -> dict[str, Any]:
    path = _job_path(job, job_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    payload["_resolved_path"] = str(path)
    return payload


def _sqlite_store(sqlite_path: Path | None) -> SqliteRuntimeStore | None:
    if sqlite_path is None:
        return None
    path = sqlite_path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Runtime SQLite database not found: {path}")
    return SqliteRuntimeStore(path)


def _load_job(job: str, job_dir: Path, sqlite_path: Path | None = None) -> dict[str, Any]:
    candidate = Path(job)
    if candidate.exists() or candidate.suffix == ".json":
        return _load_job_json(job, job_dir)

    store = _sqlite_store(sqlite_path)
    if store is not None:
        payload = store.get_job(str(job))
        if payload:
            payload["_resolved_path"] = f"sqlite:{store.db_path}:{job}"
            return payload
    raise FileNotFoundError(f"Job not found in runtime SQLite store: {job}")


def _normal_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _contains_text(value: Any, needle: str) -> bool:
    expected = str(needle or "").strip().lower()
    if not expected:
        return True
    return expected in str(value or "").strip().lower()


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
    if not str(minimum or "").strip():
        return True
    actual = _parse_timestamp(value)
    threshold = _parse_timestamp(minimum)
    if actual is None or threshold is None:
        return False
    return actual >= threshold


def _job_sort_key(entry: dict[str, Any]) -> str:
    return str(
        entry.get("finished_at")
        or entry.get("started_at")
        or entry.get("created_at")
        or entry.get("mtime")
        or ""
    )


def _job_index_candidates(
    job_dir: Path,
    *,
    limit: int,
    sqlite_path: Path | None = None,
) -> list[dict[str, Any]]:
    store = _sqlite_store(sqlite_path)
    if store is not None:
        entries = [_as_dict(entry) for entry in store.list_jobs(include_all=True)]
        entries = [entry for entry in entries if str(entry.get("job_id") or "").strip()]
        entries.sort(key=_job_sort_key, reverse=True)
        return entries[: max(1, limit)]
    raise FileNotFoundError("Runtime SQLite database path is required to list jobs")


def _result_sections(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _as_dict(job.get("result"))
    structured = _as_dict(result.get("structured_results"))
    return result, structured


def _traffic_attribution(result: dict[str, Any], structured: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(structured.get("traffic_attribution") or result.get("traffic_attribution"))


def _scenario_candidates(structured: dict[str, Any]) -> dict[str, str]:
    return {
        "free_optimization_baseline": "free_optimization_baseline",
        "time_constrained": "time_constrained_optimization",
        "subway": "subway",
        "nearby": "nearby",
        "further_most": "further_most",
        "further_most_nearby": "further_most_nearby",
    }


def _method_counts(estimates: list[Any]) -> dict[str, int]:
    return dict(Counter(str(_as_dict(item).get("method") or "unknown") for item in estimates))


def _quality_counts(estimates: list[Any]) -> dict[str, int]:
    return dict(Counter(str(_as_dict(item).get("quality_reason") or "unknown") for item in estimates))


def _non_geo_route_summaries(estimates: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for item in estimates:
        estimate = _as_dict(item)
        method = str(estimate.get("method") or "unknown")
        if method == "geo_route_similarity":
            continue
        routes.append(
            {
                "route_id": str(estimate.get("route_id") or ""),
                "method": method,
                "quality_reason": str(estimate.get("quality_reason") or "unknown"),
                "reason": str(estimate.get("reason") or ""),
                "factor": float(estimate.get("factor") or 0.0),
                "avg_similarity": float(estimate.get("avg_similarity") or 0.0),
                "matched_sample_count": int(estimate.get("matched_sample_count") or 0),
                "geo_candidate_count": int(estimate.get("geo_candidate_count") or 0),
                "usable_geo_candidate_count": int(estimate.get("usable_geo_candidate_count") or 0),
            }
        )
    return routes[:limit]


def _summarize_route_evidence(item: Any, *, include_top_matches: bool) -> dict[str, Any]:
    estimate = _as_dict(item)
    evidence: dict[str, Any] = {
        "route_id": str(estimate.get("route_id") or ""),
        "scenario": str(estimate.get("scenario") or ""),
        "vehicle_id": str(estimate.get("vehicle_id") or ""),
        "bus_type_name": str(estimate.get("bus_type_name") or ""),
        "method": str(estimate.get("method") or "unknown"),
        "quality_reason": str(estimate.get("quality_reason") or "unknown"),
        "factor": float(estimate.get("factor") or 0.0),
        "avg_similarity": float(estimate.get("avg_similarity") or 0.0),
        "matched_sample_count": int(estimate.get("matched_sample_count") or 0),
        "candidate_count": int(estimate.get("candidate_count") or 0),
        "geo_candidate_count": int(estimate.get("geo_candidate_count") or 0),
        "usable_geo_candidate_count": int(estimate.get("usable_geo_candidate_count") or 0),
        "osrm_duration_min": round(float(estimate.get("osrm_duration_s") or 0.0) / 60.0, 2),
        "stop_count": int(estimate.get("stop_count") or 0),
        "fallback": bool(estimate.get("fallback")),
        "reason": str(estimate.get("reason") or ""),
    }
    if include_top_matches:
        evidence["top_matches"] = [
            {
                "route_id": str(match.get("route_id") or ""),
                "source_id": str(match.get("source_id") or ""),
                "factor": float(match.get("factor") or 0.0),
                "similarity_score": float(match.get("similarity_score") or 0.0),
                "similarity_method": str(match.get("similarity_method") or "unknown"),
                "geo_similarity_score": float(match.get("geo_similarity_score") or 0.0),
                "corridor_overlap": float(match.get("corridor_overlap") or 0.0),
                "center_distance_km": float(match.get("center_distance_km") or 0.0),
                "bearing_score": float(match.get("bearing_score") or 0.0),
                "duration_score": float(match.get("duration_score") or 0.0),
                "stop_score": float(match.get("stop_score") or 0.0),
                "scale_score": float(match.get("scale_score") or 0.0),
            }
            for match in (_as_dict(match) for match in _as_list(estimate.get("top_matches")))
        ]
    else:
        evidence["top_match_count"] = len(_as_list(estimate.get("top_matches")))
    return evidence


def _summarize_scenario(
    name: str,
    payload: dict[str, Any],
    *,
    include_route_evidence: bool = False,
    include_top_matches: bool = False,
) -> dict[str, Any]:
    estimates = _as_list(payload.get("route_estimates"))
    method_counts = _as_dict(payload.get("method_counts")) or _method_counts(estimates)
    quality_counts = _as_dict(payload.get("quality_reason_counts")) or _quality_counts(estimates)
    route_count = int(payload.get("route_count") or len(estimates) or 0)
    geo_count = int(payload.get("geo_attributed_route_count") or method_counts.get("geo_route_similarity", 0) or 0)
    route_similarity_count = int(
        payload.get("route_similarity_route_count") or method_counts.get("route_similarity", 0) or 0
    )
    fallback_count = int(payload.get("fallback_route_count") or method_counts.get("fallback", 0) or 0)
    non_geo_routes = _non_geo_route_summaries(estimates)
    summary: dict[str, Any] = {
        "scenario": name,
        "present": True,
        "route_estimate_count": route_count,
        "geo_attributed_route_count": geo_count,
        "route_similarity_route_count": route_similarity_count,
        "fallback_route_count": fallback_count,
        "non_geo_route_count": max(0, route_count - geo_count),
        "non_geo_routes": non_geo_routes,
        "geo_attributed_route_ratio": (geo_count / route_count) if route_count else 0.0,
        "observed_route_sample_count": int(payload.get("observed_route_sample_count") or 0),
        "geo_route_sample_count": int(payload.get("geo_route_sample_count") or 0),
        "scale_only_route_sample_count": int(payload.get("scale_only_route_sample_count") or 0),
        "geo_route_sample_ratio": float(payload.get("geo_route_sample_ratio") or 0.0),
        "geo_ready": bool(payload.get("geo_ready")),
        "method_counts": method_counts,
        "quality_reason_counts": quality_counts,
    }
    if include_route_evidence:
        summary["route_evidence"] = [
            _summarize_route_evidence(item, include_top_matches=include_top_matches)
            for item in estimates
        ]
    return summary


def summarize_job(
    job: str,
    job_dir: Path = DEFAULT_JOB_DIR,
    sqlite_path: Path | None = None,
    *,
    include_route_evidence: bool = False,
    include_top_matches: bool = False,
) -> dict[str, Any]:
    payload = _load_job(job, job_dir, sqlite_path)
    result, structured = _result_sections(payload)
    metadata = _as_dict(payload.get("metadata"))
    traffic = _traffic_attribution(result, structured)
    scenario_estimates = _as_dict(traffic.get("scenario_route_estimates"))

    scenarios: list[dict[str, Any]] = []
    for name, data in sorted(scenario_estimates.items()):
        if isinstance(data, dict):
            scenarios.append(
                _summarize_scenario(
                    str(name),
                    data,
                    include_route_evidence=include_route_evidence,
                    include_top_matches=include_top_matches,
                )
            )

    present_scenarios = {str(item.get("scenario")) for item in scenarios}
    for scenario_name, structured_key in _scenario_candidates(structured).items():
        if scenario_name in present_scenarios:
            continue
        scenario_payload = _as_dict(structured.get(structured_key))
        route_attribution = _as_dict(scenario_payload.get("traffic_route_attribution"))
        if route_attribution:
            scenarios.append(
                _summarize_scenario(
                    scenario_name,
                    route_attribution,
                    include_route_evidence=include_route_evidence,
                    include_top_matches=include_top_matches,
                )
            )
            present_scenarios.add(scenario_name)

    return {
        "job_id": str(payload.get("job_id") or Path(str(payload.get("_resolved_path"))).stem),
        "path": str(payload.get("_resolved_path") or ""),
        "status": str(payload.get("status") or ""),
        "created_at": str(payload.get("created_at") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "finished_at": str(payload.get("finished_at") or ""),
        "job_name": str(metadata.get("job_name") or metadata.get("job_default_name") or payload.get("name") or ""),
        "source_label": str(metadata.get("source_label") or ""),
        "service_direction": str(structured.get("service_direction") or result.get("service_direction") or ""),
        "traffic_profile_name": str(structured.get("traffic_profile_name") or result.get("traffic_profile_name") or ""),
        "traffic_time_multiplier": float(
            structured.get("traffic_time_multiplier") or result.get("traffic_time_multiplier") or 0.0
        ),
        "traffic_coefficient_mode": str(structured.get("traffic_coefficient_mode") or ""),
        "has_traffic_attribution": bool(traffic),
        "attribution_enabled": bool(traffic.get("enabled")),
        "attribution_succeeded": bool(traffic.get("succeeded")),
        "attribution_mode": str(traffic.get("mode") or ""),
        "attribution_method": str(traffic.get("method") or ""),
        "attribution_reason": str(traffic.get("reason") or ""),
        "attribution_confidence": str(traffic.get("confidence") or ""),
        "route_level_applied": bool(traffic.get("route_level_applied")),
        "observed_route_sample_count": int(traffic.get("observed_route_sample_count") or 0),
        "geo_route_sample_count": int(traffic.get("geo_route_sample_count") or 0),
        "scale_only_route_sample_count": int(traffic.get("scale_only_route_sample_count") or 0),
        "geo_route_sample_ratio": float(traffic.get("geo_route_sample_ratio") or 0.0),
        "scenario_count": len(scenarios),
        "scenarios": sorted(scenarios, key=lambda item: str(item.get("scenario") or "")),
    }


def find_latest_job(
    job_dir: Path = DEFAULT_JOB_DIR,
    sqlite_path: Path | None = None,
    *,
    status: str = "",
    service_direction: str = "",
    traffic_coefficient_mode: str = "",
    job_name_contains: str = "",
    source_label_contains: str = "",
    min_created_at: str = "",
    min_finished_at: str = "",
    require_attribution: bool = False,
    limit: int = DEFAULT_LATEST_SCAN_LIMIT,
) -> tuple[str, dict[str, Any]]:
    expected_status = _normal_key(status)
    expected_direction = _normal_key(service_direction)
    expected_mode = _normal_key(traffic_coefficient_mode)
    diagnostics: dict[str, Any] = {
        "mode": "latest",
        "scanned_job_count": 0,
        "skipped_job_count": 0,
        "filters": {
            "status": status,
            "service_direction": service_direction,
            "traffic_coefficient_mode": traffic_coefficient_mode,
            "job_name_contains": job_name_contains,
            "source_label_contains": source_label_contains,
            "min_created_at": min_created_at,
            "min_finished_at": min_finished_at,
            "require_attribution": require_attribution,
            "limit": limit,
        },
    }
    for entry in _job_index_candidates(job_dir, limit=limit, sqlite_path=sqlite_path):
        job_id = str(entry.get("job_id") or "").strip()
        if not job_id:
            continue
        diagnostics["scanned_job_count"] += 1
        try:
            summary = summarize_job(job_id, job_dir, sqlite_path)
        except Exception:
            diagnostics["skipped_job_count"] += 1
            continue
        if expected_status and _normal_key(summary.get("status")) != expected_status:
            diagnostics["skipped_job_count"] += 1
            continue
        if expected_direction and _normal_key(summary.get("service_direction")) != expected_direction:
            diagnostics["skipped_job_count"] += 1
            continue
        if expected_mode and _normal_key(summary.get("traffic_coefficient_mode")) != expected_mode:
            diagnostics["skipped_job_count"] += 1
            continue
        if not _contains_text(summary.get("job_name"), job_name_contains):
            diagnostics["skipped_job_count"] += 1
            continue
        if not _contains_text(summary.get("source_label"), source_label_contains):
            diagnostics["skipped_job_count"] += 1
            continue
        if not _timestamp_at_or_after(summary.get("created_at"), min_created_at):
            diagnostics["skipped_job_count"] += 1
            continue
        if not _timestamp_at_or_after(summary.get("finished_at"), min_finished_at):
            diagnostics["skipped_job_count"] += 1
            continue
        if require_attribution and not (
            bool(summary.get("attribution_succeeded")) and bool(summary.get("route_level_applied"))
        ):
            diagnostics["skipped_job_count"] += 1
            continue
        diagnostics["selected_job_id"] = job_id
        return job_id, diagnostics
    raise LookupError(
        "No matching job found in latest job history. "
        f"Scanned {diagnostics['scanned_job_count']} job(s) from {sqlite_path or job_dir}."
    )


def evaluate_requirements(
    summary: dict[str, Any],
    *,
    require_attribution: bool,
    required_scenarios: list[str],
    min_geo_route_ratio: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if require_attribution:
        passed = bool(summary.get("attribution_succeeded")) and bool(summary.get("route_level_applied"))
        reason = "ok" if passed else "attribution_not_applied"
        if not summary.get("has_traffic_attribution"):
            reason = "missing_traffic_attribution"
        elif not summary.get("attribution_succeeded"):
            reason = "traffic_attribution_not_succeeded"
        elif not summary.get("route_level_applied"):
            reason = "route_level_attribution_not_applied"
        results.append({"requirement": "attribution", "passed": passed, "reason": reason})

    scenarios = {str(item.get("scenario") or ""): item for item in _as_list(summary.get("scenarios"))}
    for scenario in required_scenarios:
        item = scenarios.get(scenario)
        if item is None:
            results.append(
                {
                    "requirement": "scenario",
                    "scenario": scenario,
                    "passed": False,
                    "reason": "missing_scenario_attribution",
                    "geo_attributed_route_ratio": 0.0,
                }
            )
            continue
        ratio = float(item.get("geo_attributed_route_ratio") or 0.0)
        route_count = int(item.get("route_estimate_count") or 0)
        geo_count = int(item.get("geo_attributed_route_count") or 0)
        passed = route_count > 0 and geo_count > 0 and ratio >= min_geo_route_ratio
        reason = "ok" if passed else "geo_route_ratio_below_requirement"
        if route_count <= 0:
            reason = "no_route_estimates"
        elif geo_count <= 0:
            reason = "no_geo_attributed_routes"
        results.append(
            {
                "requirement": "scenario",
                "scenario": scenario,
                "passed": passed,
                "reason": reason,
                "geo_attributed_route_ratio": ratio,
                "geo_attributed_route_count": geo_count,
                "non_geo_route_count": int(item.get("non_geo_route_count") or max(0, route_count - geo_count)),
                "non_geo_routes": _as_list(item.get("non_geo_routes")),
                "route_estimate_count": route_count,
                "required_geo_attributed_route_ratio": min_geo_route_ratio,
            }
        )
    return results


def _print_summary(summary: dict[str, Any], *, show_route_evidence: bool = False) -> None:
    print(f"job_id: {summary['job_id']}")
    print(f"status: {summary['status']}")
    print(f"service_direction: {summary['service_direction']}")
    print(f"traffic_profile: {summary['traffic_profile_name']} x{summary['traffic_time_multiplier']:.3f}")
    print(
        "attribution:",
        f"present={summary['has_traffic_attribution']}",
        f"enabled={summary['attribution_enabled']}",
        f"succeeded={summary['attribution_succeeded']}",
        f"route_level={summary['route_level_applied']}",
        f"mode={summary['attribution_mode'] or '-'}",
        f"method={summary['attribution_method'] or '-'}",
        f"reason={summary['attribution_reason'] or '-'}",
    )
    print(
        "samples:",
        f"observed={summary['observed_route_sample_count']}",
        f"geo={summary['geo_route_sample_count']}",
        f"scale_only={summary['scale_only_route_sample_count']}",
        f"geo_ratio={summary['geo_route_sample_ratio']:.3f}",
    )
    print("scenarios")
    for scenario in summary["scenarios"]:
        print(
            scenario["scenario"],
            f"routes={scenario['route_estimate_count']}",
            f"geo={scenario['geo_attributed_route_count']}",
            f"route_similarity={scenario['route_similarity_route_count']}",
            f"fallback={scenario['fallback_route_count']}",
            f"non_geo={scenario['non_geo_route_count']}",
            f"geo_ratio={float(scenario['geo_attributed_route_ratio']):.3f}",
        )
        non_geo_routes = _as_list(scenario.get("non_geo_routes"))
        if non_geo_routes:
            route_labels = [
                f"{_as_dict(route).get('route_id') or '-'}"
                f"({_as_dict(route).get('method') or '-'}/"
                f"{_as_dict(route).get('quality_reason') or '-'})"
                for route in non_geo_routes
            ]
            print("  non_geo_routes:", ", ".join(route_labels))
        if show_route_evidence:
            for route in _as_list(scenario.get("route_evidence")):
                route_dict = _as_dict(route)
                print(
                    "  route",
                    route_dict.get("route_id") or "-",
                    f"factor={float(route_dict.get('factor') or 0.0):.3f}",
                    f"method={route_dict.get('method') or '-'}",
                    f"quality={route_dict.get('quality_reason') or '-'}",
                    f"matches={int(route_dict.get('matched_sample_count') or 0)}",
                    f"avg_similarity={float(route_dict.get('avg_similarity') or 0.0):.3f}",
                    f"geo_candidates={int(route_dict.get('usable_geo_candidate_count') or 0)}/"
                    f"{int(route_dict.get('geo_candidate_count') or 0)}",
                )
                for match in _as_list(route_dict.get("top_matches")):
                    match_dict = _as_dict(match)
                    print(
                        "    match",
                        match_dict.get("source_id") or "-",
                        match_dict.get("route_id") or "-",
                        f"factor={float(match_dict.get('factor') or 0.0):.3f}",
                        f"score={float(match_dict.get('similarity_score') or 0.0):.3f}",
                        f"method={match_dict.get('similarity_method') or '-'}",
                    )
    requirements = _as_list(summary.get("requirements"))
    if requirements:
        print("requirements")
        for requirement in requirements:
            status = "ok" if requirement["passed"] else "fail"
            detail = str(requirement.get("scenario") or requirement.get("requirement") or "")
            print(status, detail, requirement["reason"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", help="Job seed/id or path to a job JSON file.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Inspect the latest job matching the optional filters instead of passing --job manually.",
    )
    parser.add_argument("--job-dir", type=Path, default=DEFAULT_JOB_DIR)
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=DEFAULT_RUNTIME_DB_PATH,
        help="SQLite runtime DB path. Used first for job lookup; JSON job-dir is archive fallback.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--include-route-evidence",
        action="store_true",
        help="Include per-route attribution factor evidence in JSON output.",
    )
    parser.add_argument(
        "--include-top-matches",
        action="store_true",
        help="Include top matched historical sample routes inside route evidence.",
    )
    parser.add_argument(
        "--show-route-evidence",
        action="store_true",
        help="Print per-route attribution factor evidence in text output. Implies --include-route-evidence.",
    )
    parser.add_argument("--require-attribution", action="store_true", help="Require job-level attribution success.")
    parser.add_argument(
        "--require-scenario",
        action="append",
        default=[],
        metavar="SCENARIO",
        help="Require a scenario to have geo-attributed route estimates.",
    )
    parser.add_argument(
        "--min-geo-route-ratio",
        type=float,
        default=1.0,
        help="Minimum scenario route ratio that must use geo_route_similarity. Default: 1.0.",
    )
    parser.add_argument(
        "--latest-limit",
        type=int,
        default=DEFAULT_LATEST_SCAN_LIMIT,
        help=f"Maximum recent jobs to scan with --latest. Default: {DEFAULT_LATEST_SCAN_LIMIT}.",
    )
    parser.add_argument(
        "--latest-status",
        default="succeeded",
        help="Status filter used with --latest. Default: succeeded. Use empty string to disable.",
    )
    parser.add_argument(
        "--service-direction",
        default="",
        help="Optional service direction filter used with --latest, for example 'To School' or 'From School'.",
    )
    parser.add_argument(
        "--traffic-coefficient-mode",
        default="",
        help="Optional traffic coefficient mode filter used with --latest, for example 'attributed'.",
    )
    parser.add_argument(
        "--latest-job-name-contains",
        default="",
        help="Optional substring filter for metadata.job_name used with --latest.",
    )
    parser.add_argument(
        "--latest-source-label-contains",
        default="",
        help="Optional substring filter for metadata.source_label used with --latest.",
    )
    parser.add_argument(
        "--latest-min-created-at",
        default="",
        help="Optional ISO timestamp lower bound for job created_at used with --latest.",
    )
    parser.add_argument(
        "--latest-min-finished-at",
        default="",
        help="Optional ISO timestamp lower bound for job finished_at used with --latest.",
    )
    parser.add_argument(
        "--latest-require-attribution",
        action="store_true",
        help="With --latest, only select jobs where route-level traffic attribution was applied.",
    )
    args = parser.parse_args()

    if bool(args.job) == bool(args.latest):
        parser.error("Provide exactly one of --job or --latest")
    if not 0.0 <= args.min_geo_route_ratio <= 1.0:
        parser.error("--min-geo-route-ratio must be between 0 and 1")
    if args.latest_limit <= 0:
        parser.error("--latest-limit must be positive")

    include_route_evidence = bool(args.include_route_evidence or args.show_route_evidence)
    include_top_matches = bool(args.include_top_matches)
    selection: dict[str, Any] = {}
    job = str(args.job or "")
    if args.latest:
        try:
            job, selection = find_latest_job(
                args.job_dir,
                args.sqlite_path,
                status=str(args.latest_status or ""),
                service_direction=str(args.service_direction or ""),
                traffic_coefficient_mode=str(args.traffic_coefficient_mode or ""),
                job_name_contains=str(args.latest_job_name_contains or ""),
                source_label_contains=str(args.latest_source_label_contains or ""),
                min_created_at=str(args.latest_min_created_at or ""),
                min_finished_at=str(args.latest_min_finished_at or ""),
                require_attribution=bool(args.latest_require_attribution),
                limit=int(args.latest_limit),
            )
        except LookupError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
    summary = summarize_job(
        job,
        args.job_dir,
        args.sqlite_path,
        include_route_evidence=include_route_evidence,
        include_top_matches=include_top_matches,
    )
    if selection:
        summary["selection"] = selection
    requirements = evaluate_requirements(
        summary,
        require_attribution=bool(args.require_attribution),
        required_scenarios=[str(item) for item in args.require_scenario],
        min_geo_route_ratio=float(args.min_geo_route_ratio),
    )
    if requirements:
        summary["requirements"] = requirements
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(summary, show_route_evidence=bool(args.show_route_evidence))
    if any(not item["passed"] for item in requirements):
        print("One or more job traffic-attribution requirements failed.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
