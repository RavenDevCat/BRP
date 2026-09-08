"""Insert results retain one measurement contract and replayable native inputs."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps"), str(ROOT / "apps/backend")]
import api_app as api
from route_evidence import EVIDENCE_VERSION
from route_measurement_view import route_display_metrics


def source(*, dwell=0, direction="to_school"):
    stops = [dict(id=name, route_id="R1", order=index, address=name, is_depot=depot,
                  passenger_count=count, lat=31.2 + index * .001, lng=121.4 + index * .001)
             for index, (name, depot, count) in enumerate((("A", False, 5), ("Waypoint", False, 0), ("School", True, 0)))]
    if direction == "from_school":
        stops.reverse()
        for index, stop in enumerate(stops):
            stop["order"] = index
    return {"routes": [{"id": "R1", "load": 5, "bus_capacity": 15, "stop_count": 2,
                         "duration_s": 120, "distance_m": 1000, "stop_service_time_s": dwell,
                         "geometry": [[120, 30], [120.1, 30.1]], "stop_ids": [p["id"] for p in stops]}],
            "stops": stops, "summary": {}}


def action(kind="insert_stop"):
    return {"type": kind, "route_id": "R1", "insert_slot_index": 1, "target_stop_order": 0,
            "feasible": True, "new_stop": {"index": 0, "address": "New", "lat": 31.2015,
                                           "lng": 121.4015, "passenger_count": 2}}


def measured(points, *, seconds=600, verified=True):
    count = len(points) - 1
    legs = [{"duration_s": seconds / count, "distance_m": 1000,
             "geometry": [[points[i]["lng"], points[i]["lat"]], [points[i+1]["lng"], points[i+1]["lat"]]]}
            for i in range(count)]
    evidence = {"provider": "amap", "evidence_version": EVIDENCE_VERSION, "complete": verified,
                "status": "verified" if verified else "needs_review", "issues": [],
                "duration_s": seconds, "distance_m": 1000 * count,
                "legs": legs, "leg_durations_s": [leg["duration_s"] for leg in legs],
                "leg_distances_m": [leg["distance_m"] for leg in legs],
                "geometry": [[p["lng"], p["lat"]] for p in points],
                "geometry_segments": [leg["geometry"] for leg in legs]}
    return {"geometry": evidence["geometry"], "display_geometry": evidence["geometry"],
            "display_geometry_source": "amap_continuous_waypoint_legs", "route_evidence": evidence,
            "duration_s": seconds if verified else None, "distance_m": evidence["distance_m"] if verified else None,
            "provider_verified": verified, "leg_durations_s": evidence["leg_durations_s"] if verified else [],
            "leg_distances_m": evidence["leg_distances_m"] if verified else [], "warnings": []}


def build(data, actions, **config):
    return api._insert_build_selected_plan(data, actions, country="China", constraints={},
        suggested_config={"time_window_start": "06:00", "time_window_end": "07:00", **config}, measurement_cache={})


@pytest.mark.parametrize("direction", ["to_school", "from_school"])
@pytest.mark.parametrize("dwell_minutes", [0, 1, 2])
def test_saved_zero_dwell_is_preserved_and_map_total_matches_result(monkeypatch, direction, dwell_minutes):
    data = source(dwell=0, direction=direction)
    original = deepcopy(data)
    monkeypatch.setattr(api, "_insert_route_measurement", lambda points, *_: measured(points))
    plan, map_data = build(data, [action()], stop_service_minutes=dwell_minutes, service_direction=direction)
    row, route = plan["affected_routes"][0], map_data["routes"][0]
    assert row["base_duration_s"] == 600
    assert row["selected_duration_s"] == 600 + dwell_minutes * 60
    assert route["duration_s"] == row["selected_duration_s"]
    assert route["stop_service_time_s"] == dwell_minutes * 60
    assert route_display_metrics(route)["duration_s"] == row["selected_duration_s"]
    assert map_data["summary"]["duration_s"] == row["selected_duration_s"]
    saved = row["measurement_inputs"]
    assert saved["base_stop_service_time_s"] == 0 and saved["base_dwell_source"] == "saved_route"
    assert saved["inserted_stop_dwell_s"] == dwell_minutes * 60
    assert [p["address"] for p in saved["base_points"]] == [p["address"] for p in data["stops"]]
    assert all(p["coordinate_system"] == "WGS84" for p in saved["base_points"])
    waypoint = next(p for p in saved["selected_points"] if p["address"] == "Waypoint")
    assert waypoint["passenger_count"] == 0 and not waypoint["is_depot"]
    assert [p["address"] for p in saved["selected_points"] if p["is_depot"]] == ["School"]
    assert data == original


def test_walk_only_remeasures_existing_route_once_and_rejects_new_overrun(monkeypatch):
    calls = []
    def measure(points, *_):
        calls.append(deepcopy(points))
        return measured(points, seconds=720)
    monkeypatch.setattr(api, "_insert_route_measurement", measure)
    data = source(dwell=0)
    plan, map_data = build(data, [action("walk_to_stop")], stop_service_minutes=0, time_window_end="06:10")
    assert len(calls) == 1
    assert [p["address"] for p in calls[0]] == ["A", "Waypoint", "School"]
    assert plan["feasible"] is False and plan["status"] == "needs_review"
    row = plan["affected_routes"][0]
    assert row["provider_required"] and row["provider_verified"]
    assert row["base_duration_s"] == row["selected_duration_s"] == 720
    assert row["time_window_ok"] is False
    assert map_data["routes"][0]["final_route_traffic_gate"]["status"] == "failed"
    assert map_data["routes"][0]["load"] == 7


@pytest.mark.parametrize("kind", ["walk_to_stop", "insert_stop"])
def test_unknown_provider_result_is_not_zero_or_a_time_window_pass(monkeypatch, kind):
    monkeypatch.setattr(api, "_insert_route_measurement", lambda points, *_: measured(points, verified=False))
    plan, map_data = build(source(), [action(kind)], stop_service_minutes=0)
    row = plan["affected_routes"][0]
    assert not plan["feasible"] and row["time_window_ok"] is None
    assert row["selected_duration_s"] is row["base_duration_s"] is row["delta_duration_s"] is None
    assert row["selected_distance_m"] is row["base_distance_m"] is row["delta_distance_m"] is None
    assert plan["total_added_duration_s"] is plan["total_added_distance_m"] is None
    assert map_data["summary"]["duration_s"] is map_data["summary"]["distance_m"] is None
    assert map_data["routes"][0]["duration_s"] is None
    assert map_data["routes"][0]["final_route_traffic_gate"]["status"] == "unavailable"
    assert all(p["cumulative_duration_s"] is None for p in map_data["stops"] if p["route_id"] == "R1")


def test_real_zero_measurement_stays_zero(monkeypatch):
    monkeypatch.setattr(api, "_insert_route_measurement", lambda points, *_: measured(points, seconds=0))
    plan, map_data = build(source(), [action("walk_to_stop")], stop_service_minutes=0)
    assert plan["feasible"]
    assert plan["affected_routes"][0]["selected_duration_s"] == 0
    assert map_data["summary"]["duration_s"] == 0


def test_missing_route_cannot_make_an_empty_selected_plan_feasible(monkeypatch):
    monkeypatch.setattr(api, "_insert_route_measurement", lambda *_: pytest.fail("Missing route must not be measured"))
    plan, _ = build({"routes": [], "stops": []}, [action()])
    assert not plan["feasible"] and plan["status"] == "needs_review"


@pytest.mark.parametrize("status", ["needs_review", "error", "verified"])
def test_amap_attempt_never_falls_back_to_osrm_as_measured_time(monkeypatch, status):
    points = [{"lat": 31.2, "lng": 121.4}, {"lat": 31.21, "lng": 121.41}]
    provider_result = measured(points, seconds=900, verified=status == "verified")
    class Planner:
        OSRM_BASE_URL = "test"
        @staticmethod
        def point_osrm_lat_lng(point, **_):
            return point["lat"], point["lng"]
        @staticmethod
        def osrm_request_json(*_):
            return {"routes": [{"duration": 300, "distance": 1000,
                                "geometry": {"coordinates": [[120, 30], [120.1, 30.1]]},
                                "legs": [{"duration": 300, "distance": 1000}]}]}
    def amap(_planner, _points, _cache, state):
        if status == "error":
            raise RuntimeError("provider failed")
        state["last_route_evidence"] = deepcopy(provider_result["route_evidence"])
    monkeypatch.setattr(api.backend_service, "load_legacy_planner", Planner)
    monkeypatch.setattr(api.backend_service, "_route_amap_points", lambda *_: [(31.2, 121.4), (31.21, 121.41)])
    monkeypatch.setattr(api.backend_service, "_amap_route_stats", amap)
    result = api._insert_route_measurement(points, "China", {})
    assert result["planning_reference"]["duration_s"] == 300
    if status == "verified":
        assert result["duration_s"] == 900 and result["leg_durations_s"] == [900]
        assert result["geometry"] == provider_result["geometry"]
    else:
        assert result["duration_s"] is result["distance_m"] is None
        assert not result["provider_verified"]
        assert result["leg_durations_s"] == result["leg_distances_m"] == []
        assert result["display_geometry_source"] != "osrm"
        assert result["geometry"] != [[120, 30], [120.1, 30.1]]


def test_multiple_route_summary_is_total_and_missing_values_propagate(monkeypatch):
    first, second = source(), source()
    second["routes"][0]["id"] = "R2"
    for stop in second["stops"]:
        stop["route_id"] = "R2"
    first["routes"].extend(second["routes"])
    first["stops"].extend(second["stops"])
    other = {**action("walk_to_stop"), "route_id": "R2"}
    monkeypatch.setattr(api, "_insert_route_measurement", lambda points, *_: measured(points))
    _, map_data = build(first, [action("walk_to_stop"), other], stop_service_minutes=0)
    assert map_data["summary"]["duration_s"] == sum(route["duration_s"] for route in map_data["routes"]) == 1200


@pytest.mark.parametrize("verified", [True, False])
def test_source_display_metadata_cannot_override_selected_measurement(monkeypatch, verified):
    data = source()
    data["routes"][0].update(display_metrics={"source": "measurement", "duration_s": 120, "distance_m": 1000},
                             limit_stop_node=1, limit_stop_order=1, limit_stop_elapsed_s=120)
    original = deepcopy(data)
    monkeypatch.setattr(api, "_insert_route_measurement", lambda points, *_: measured(points, verified=verified))
    _, result = build(data, [action()], stop_service_minutes=0)
    assert not {"display_metrics", "limit_stop_node", "limit_stop_order", "limit_stop_elapsed_s"}.intersection(result["routes"][0])
    assert route_display_metrics(result["routes"][0])["duration_s"] == (600 if verified else None)
    assert data == original


@pytest.mark.parametrize("country", ["China", "Korea"])
def test_osrm_failure_is_not_a_straight_line_measurement_or_baseline(monkeypatch, country):
    points = [{"lat": 31.2, "lng": 121.4}, {"lat": 31.21, "lng": 121.41}]
    class Planner:
        OSRM_BASE_URL = "test"
        @staticmethod
        def point_osrm_lat_lng(point, **_):
            return point["lat"], point["lng"]
        @staticmethod
        def osrm_request_json(*_):
            raise RuntimeError("OSRM unavailable")
    def amap(_planner, _points, _cache, state):
        assert state["expected_leg_distances_m"] == state["expected_leg_durations_s"] == []
        state["last_route_evidence"] = measured(points)["route_evidence"]
    monkeypatch.setattr(api.backend_service, "load_legacy_planner", Planner)
    monkeypatch.setattr(api.backend_service, "_route_amap_points", lambda *_: [])
    monkeypatch.setattr(api.backend_service, "_amap_route_stats", amap)
    result = api._insert_route_measurement(points, country, {})
    assert result["planning_reference"] == {"duration_s": None, "distance_m": None}
    assert result["duration_s"] == (600 if country == "China" else None)
    if country != "China":
        assert result["geometry"] == []
