from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
from direct_school_review import present_direct_school_result, route_coverage


def fixture(direction="To School"):
    evidence = {"complete": True, "provider": "amap", "source": "amap_continuous_waypoint_legs",
                "point_count": 3, "duration_s": 1800, "distance_m": 10000,
                "leg_durations_s": [600, 1200], "leg_distances_m": [6000, 4000],
                "called_at": "2026-09-10T00:00:00+00:00",
                "issues": [{"code": "provider_distance_disagreement", "leg_index": 0}]}
    return {"status": "partial", "parameters": {"stop_service_minutes": 1}, "service_direction": direction,
            "summary": {}, "routes": [{"route_id": "R2", "status": "failed", "error": "distance warning", "route_evidence": evidence},
                                      {"route_id": "R9", "status": "resolved"}],
            "stops": [{"address": "Shared", "route_contexts": [{"route_id": "R2", "stop_sequence": 1, "riders": 2,
                         "estimated_current_ride_min": None, "operational_category": "direct_over_limit"},
                        {"route_id": "R9", "stop_sequence": 1, "estimated_current_ride_min": 15}], "estimated_current_ride_min": 15},
                      {"address": "Zero rider waypoint", "route_contexts": [{"route_id": "R2", "stop_sequence": 2,
                        "riders": 0, "estimated_current_ride_min": None, "operational_category": "data_review"}]}],
            "operational_conclusion": {"additional_removal": {"rider_count": 0}, "final": {"data_review_count": 1}},
            "route_window_analysis": [{"route_id": "R2", "status": "data_review"}]}


@pytest.mark.parametrize("direction,expected", [("To School", [32, 21]), ("From School", [11, 32])])
def test_provisional_remaining_time_keeps_trusted_values_and_zero_waypoint(direction, expected):
    raw = fixture(direction)
    before = deepcopy(raw)
    result = present_direct_school_result(raw)
    assert raw == before
    assert result["routes"][0]["status"] == "needs_review"
    assert result["routes"][0]["provisional_total_duration_min"] == 32
    assert result["stops"][0]["estimated_current_ride_min"] == 15
    for row, minutes in zip(result["stops"], expected):
        c = row["route_contexts"][0]
        assert c["provisional_current_ride_min"] == minutes
        assert c["estimated_current_ride_min"] is None
    assert result["operational_conclusion"] == before["operational_conclusion"]
    assert result["route_window_analysis"] == before["route_window_analysis"]
    assert result == present_direct_school_result(result)
    assert route_coverage(result) == {"route_measurement_verified_count": 1, "route_measurement_review_count": 1,
                                     "route_measurement_failed_count": 0, "route_measurement_total_count": 2}


@pytest.mark.parametrize("change", [
    {"complete": False}, {"source": "adjacent_pairs"}, {"point_count": 4}, {"provider": "kakao"},
    {"duration_s": 1805}, {"leg_durations_s": [600, float("nan")]}, {"leg_durations_s": [0, 1800]},
    {"issues": [{"code": "stop_road_continuity_needs_review"}]}, {"issues": []},
    {"issues": [{"code": "provider_distance_disagreement"}, {"code": "reported_distance_shorter_than_direct"}]},
])
def test_integrity_failure_never_exposes_unverified_number(change):
    raw = fixture()
    raw["routes"][0]["route_evidence"].update(change)
    result = present_direct_school_result(raw)
    assert result["routes"][0]["status"] == "failed"
    assert result["routes"][0]["measurement_status"] == "unavailable"
    assert "provisional_current_ride_min" not in result["stops"][0]["route_contexts"][0]
    assert result["summary"]["route_measurement_failed_count"] == 1


def test_incomplete_occurrences_do_not_guess_stop_ride_or_default_dwell():
    for mutate in (lambda r: r["stops"].pop(), lambda r: r["parameters"].clear(),
                   lambda r: r["stops"][1]["route_contexts"][0].update(stop_sequence=1)):
        raw = fixture()
        mutate(raw)
        result = present_direct_school_result(raw)
        assert result["routes"][0]["provisional_provider_duration_min"] == 30
        assert "provisional_current_ride_min" not in result["stops"][0]["route_contexts"][0]


def test_legacy_complete_result_is_presented_partial_without_rewriting():
    raw = fixture()
    raw["status"] = "complete"
    assert present_direct_school_result(raw)["status"] == "partial"
    assert raw["status"] == "complete"


def test_large_detour_retains_warning_and_never_certifies():
    raw = fixture()
    raw["routes"][0]["route_evidence"]["issues"] = [{"code": "large_direct_detour_needs_review"}]
    result = present_direct_school_result(raw)
    assert result["stops"][0]["route_contexts"][0]["review_codes"] == ["large_direct_detour_needs_review"]
    assert result["routes"][0]["status"] == "needs_review"


def test_missing_route_results_are_not_counted_as_complete_coverage():
    raw = fixture()
    raw['summary']['route_count'] = 4
    assert route_coverage(raw)['route_measurement_total_count'] == 4
    assert route_coverage(raw)['route_measurement_failed_count'] == 2
