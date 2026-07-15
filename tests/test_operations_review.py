from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import operations_review  # noqa: E402


def _scenario(route_label: str, *, excess: float, affected: int, worst: float) -> dict:
    return {
        "enabled": True,
        "route_count": 1,
        "points": [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.0, "lng": 121.0},
            {"node_id": 1, "address": "Stop A", "lat": 31.1, "lng": 121.1, "passenger_count": 4},
            {"node_id": 2, "address": "Stop B", "lat": 31.2, "lng": 121.2, "passenger_count": 3},
        ],
        "routes": [
            {
                "route_id": route_label,
                "bus_type_name": "18-fbus",
                "nodes": [1, 2, 0],
                "load": 7,
                "final_route_traffic_gate": {"status": "failed", "time_window_overrun_minutes": worst},
            }
        ],
        "traffic_gate": {
            "status": "failed",
            "checked_route_count": 1,
            "failed_route_count": 1,
            "unavailable_route_count": 0,
            "failed_route_ids": [route_label],
            "max_time_window_overrun_minutes": worst,
        },
        "final_time_impact_gate": {
            "status": "failed",
            "over_limit_rider_count": affected,
            "max_over_limit_minutes": worst,
            "unavailable_stop_count": 0,
            "unavailable_route_count": 0,
            "violations": [{"node_index": 1, "affected_rider_count": affected, "over_limit_minutes": excess / max(affected, 1)}],
        },
    }


def _job(job_id: str, scheduled_at: str, started_at: str, route_label: str, excess: float) -> dict:
    protected = _scenario(route_label, excess=excess, affected=7, worst=10)
    strict = _scenario(f"strict-{route_label}", excess=excess + 100, affected=7, worst=20)
    config = {
        "service_direction": "To School",
        "traffic_profile_name": "AM Peak",
        "time_window_start": "06:30",
        "time_window_end": "08:00",
        "max_route_duration_minutes": 122,
        "time_impact_limit_minutes": 15,
        "minimum_vehicle_reduction": 2,
        "route_stop_limit": 10,
        "comfort_load_factor": 0.85,
    }
    return {
        "job_id": job_id,
        "status": "succeeded",
        "scheduled_start_at": scheduled_at,
        "started_at": started_at,
        "metadata": {"job_name": job_id, "planner_config": config},
        "config": config,
        "prepared_payload_summary": {"country_samples": ["China"], "city_samples": ["Shanghai"]},
        "result": {
            "service_direction": "To School",
            "traffic_profile_name": "AM Peak",
            "current_plan_scenario": {
                "points": protected["points"],
                "routes": protected["routes"],
            },
            "time_constrained_optimization": strict,
            "exception_preserving_optimization": protected,
        },
    }


def test_plan_fingerprint_ignores_route_label_but_keeps_stop_order() -> None:
    first = _scenario("Bus 1", excess=10, affected=2, worst=3)
    renamed = _scenario("Bus 99", excess=10, affected=2, worst=3)
    reversed_plan = _scenario("Bus 1", excess=10, affected=2, worst=3)
    reversed_plan["routes"][0]["nodes"] = [2, 1, 0]

    assert operations_review._plan_fingerprint(first) == operations_review._plan_fingerprint(renamed)
    assert operations_review._plan_fingerprint(first) != operations_review._plan_fingerprint(reversed_plan)


def test_four_day_review_excludes_weekend_and_late_start_then_groups_stable_plan() -> None:
    jobs = [
        _job("sun", "2026-07-12T06:30:00+08:00", "2026-07-12T06:50:00+08:00", "Bus 1", 300),
        _job("mon", "2026-07-13T06:30:00+08:00", "2026-07-13T06:50:00+08:00", "Bus 2", 250),
        _job("tue", "2026-07-14T06:30:00+08:00", "2026-07-14T06:30:00+08:00", "Bus 3", 200),
        _job("wed", "2026-07-15T06:30:00+08:00", "2026-07-15T06:30:00+08:00", "Bus 4", 180),
    ]

    review = operations_review.build_operations_review(jobs)

    assert review["compatibility"]["compatible"] is True
    assert review["qualified_sample_count"] == 2
    assert review["excluded_sample_count"] == 2
    assert review["status"] == "review_reference"
    assert review["recommendation"]["sample_count"] == 2
    assert review["recommendation"]["representative_job_id"] == "wed"
    assert review["recommendation"]["scenario_key"] == "exception_preserving"
    assert all("plan_fingerprint" not in item for item in review["daily_evidence"])


def test_review_rejects_incompatible_time_windows() -> None:
    first = _job("first", "2026-07-14T06:30:00+08:00", "2026-07-14T06:30:00+08:00", "Bus 1", 10)
    second = _job("second", "2026-07-15T06:30:00+08:00", "2026-07-15T06:30:00+08:00", "Bus 2", 10)
    second["config"]["time_window_start"] = "06:00"

    review = operations_review.build_operations_review([first, second])

    assert review["compatibility"]["compatible"] is False
    assert review["status"] == "insufficient_evidence"
    assert review["candidates"] == []
