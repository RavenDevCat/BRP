from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
service = importlib.import_module("backend_service")
core = importlib.import_module("planner_core")
amap = importlib.import_module("amap_driving")
quality = importlib.import_module("amap_geocode_quality")


@pytest.fixture
def preview(monkeypatch):
    coords = [(31.204, 121.404), (31.200, 121.400), (31.202, 121.402)]
    points = [dict(node_id=i, address=f"Point {i}", formatted_address=f"Point {i}", lat=lat, lng=lng,
                   provider="amap", country="China", city="Shanghai",
                   geocode_quality_version=quality.GEOCODE_QUALITY_VERSION, geocode_level="\u5174\u8da3\u70b9",
                   is_depot=i == 0, passenger_count=3 if i == 1 else 0)
              for i, (lat, lng) in enumerate(coords)]
    current = {"service_direction": "To School", "input_records": deepcopy(points)}
    scenario = {"points": points, "routes": [{
        "route_id": "R1", "vehicle_id": 1, "nodes": [1, 2, 0], "load": 3,
        "time_s": 9999, "distance_m": 9999, "stop_service_time_s": 120,
        "leg_details": [{"duration_s": 100, "distance_m": 400},
                        {"duration_s": 200, "distance_m": 400}],
    }]}
    planner = SimpleNamespace(AMAP_KEY="test-key", AMAP_ROUTING_LIMITER=None, MAX_ROUTE_DURATION_SECONDS=3600)
    monkeypatch.setattr(service, "load_legacy_planner", lambda: planner)
    monkeypatch.setattr(service, "_build_assessment_metric_matrices", lambda *_: ([], []))
    monkeypatch.setattr(service, "assess_current_plan", lambda *_, **__: {})
    monkeypatch.setattr(service, "_build_time_acceptance_constraint_builder", lambda *_: (None, lambda: None, {}))
    monkeypatch.setattr(service, "build_current_plan_map_scenario", lambda *_: deepcopy(scenario))
    monkeypatch.setattr(service, "_should_use_amap_display_geometry", lambda *_: False)
    monkeypatch.setattr(core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    calls = []

    def fetch(endpoint, params, limiter):
        assert endpoint == "/v5/direction/driving"
        request = [tuple(reversed(tuple(map(float, value.split(",")))))
                   for value in [params["origin"], *params["waypoints"].split(";"), params["destination"]]]
        calls.append(request)
        steps = [{"step_distance": "400", "cost": {"duration": str(120 if a == coords[1] else 600)},
                  "polyline": f"{a[1]},{a[0]};{b[1]},{b[0]}",
                  "navi": {"assistant_action": "\u5230\u8fbe\u9014\u7ecf\u5730" if i == 0 else "\u5230\u8fbe\u76ee\u7684\u5730"}}
                 for i, (a, b) in enumerate(zip(request, request[1:]))]
        return {"route": {"paths": [{"distance": "800", "cost": {"duration": str(sum(int(s["cost"]["duration"]) for s in steps))},
                                     "steps": steps}]}}

    planner.amap_request_json = fetch
    monkeypatch.setattr(core, "_amap_route_segment_stats", lambda *args: pytest.fail("Complete continuous route must not request pairs"))
    monkeypatch.setattr(service, "_amap_display_geometry_for_route", lambda *_, **__: pytest.fail("Map must not remeasure"))
    return current, {"original_points": points}, calls, planner, scenario


def test_old_zero_passenger_waypoint_remains_in_preview_and_measurement(preview):
    current, prepared, calls, _planner, scenario = preview
    scenario["points"][2].pop("geocode_quality_version")
    budget = {"minutes": 5}
    payload, error = service._current_plan_preview_map(current, prepared, {"service_direction": "To School"}, budget)
    assert error is None
    assert len(calls) == 1
    route = payload["routes"][0]
    assert route["evidence_status"] == "verified"
    assert route["route_evidence"]["point_count"] == 3


@pytest.mark.parametrize("dwell", [0, 2])
def test_preview_uses_one_measurement_for_budget_geometry_and_stop_timing(preview, dwell):
    current, prepared, calls, _planner, original = preview
    budget = {"minutes": 5}
    payload, error = service._current_plan_preview_map(
        current, prepared, {"service_direction": "To School", "stop_service_minutes": dwell}, budget,
    )
    assert error is None
    route = payload["routes"][0]
    assert len(calls) == 1
    assert route["evidence_status"] == "verified"
    assert route["duration_s"] == 720 + dwell * 120
    assert route["verified_drive_duration_s"] == 720
    assert route["verified_total_duration_s"] == 720 + dwell * 120
    assert route["distance_m"] == 800
    assert route["display_geometry"] == route["route_evidence"]["geometry"]
    assert budget["amap_route_api_calls"] == 1
    assert budget["amap_route_duration_minutes"] == 12 + dwell * 2
    assert budget["amap_route_distance_km"] == 0.8
    assert budget["amap_route_status"] == "ready"
    assert [s["cumulative_duration_s"] for s in payload["stops"]] == [0, 120, 720]
    assert payload["stops"][1]["is_depot"] is False
    assert payload["stops"][1]["passenger_count"] == 0
    assert payload["stops"][0]["scheduled_offset_s"] == -(720 + dwell * 120)
    assert original["routes"][0]["time_s"] == 9999


def test_preview_missing_key_is_unavailable_not_legacy_or_passed(preview):
    current, prepared, calls, planner, _ = preview
    planner.AMAP_KEY = ""
    budget = {"minutes": 5}
    payload, error = service._current_plan_preview_map(current, prepared, {"service_direction": "To School"}, budget)
    assert error is None
    assert calls == []
    assert payload["routes"][0]["evidence_status"] == "unavailable"
    assert payload["routes"][0]["final_route_traffic_gate"]["passes"] is None
    assert all(s["scheduled_time_minutes"] is None for s in payload["stops"])
    assert budget["amap_route_status"] == "unavailable"


def test_preview_does_not_reuse_measurements_across_uploads(preview):
    current, prepared, calls, _planner, _ = preview
    for _ in range(2):
        payload, error = service._current_plan_preview_map(current, prepared, {"service_direction": "To School"})
        assert error is None
        assert payload["routes"][0]["evidence_status"] == "verified"
    assert len(calls) == 2


def test_partial_preview_budget_cannot_claim_all_routes_measured():
    details = {"minutes": 60}
    scenario = {"routes": [{"route_evidence": {"status": "verified"}},
                           {"route_evidence": {"status": "needs_review"}}]}
    service._update_preview_amap_budget(details, scenario, {"api_calls": 4})
    assert details["amap_route_status"] == "unavailable"
    assert details["amap_route_measured_count"] == 1
    assert details["amap_route_expected_count"] == 2
    assert details["minutes"] == 60
    assert "amap_budget_minutes" not in details


def test_from_school_preview_keeps_uploaded_order_and_forward_timing(preview):
    current, prepared, calls, _planner, scenario = preview
    current["service_direction"] = "From School"
    scenario["routes"][0]["nodes"] = [0, 2, 1]
    payload, error = service._current_plan_preview_map(current, prepared, {
        "service_direction": "From School", "stop_service_minutes": 2,
        "time_window_start": "15:40", "time_window_end": "17:00",
    })
    assert error is None
    assert len(calls) == 1
    assert [stop["node_index"] for stop in payload["stops"]] == [0, 2, 1]
    assert [stop["scheduled_offset_s"] for stop in payload["stops"]] == [0, 600, 1320]
    assert payload["routes"][0]["evidence_status"] == "verified"
