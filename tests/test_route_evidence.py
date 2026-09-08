from copy import deepcopy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
evidence = importlib.import_module("route_evidence")
amap = importlib.import_module("amap_driving")
core = importlib.import_module("planner_core")
PRECISE_PICKUP = {"geocode_quality_version": importlib.import_module("amap_geocode_quality").GEOCODE_QUALITY_VERSION,
                  "geocode_level": "\u5174\u8da3\u70b9"}

A, B, C = (31.20, 121.40), (31.202, 121.402), (31.204, 121.404)


def leg(_planner, points):
    return {"duration_s": 120 if points[0] == A else 600, "distance_m": 400,
            "geometry": [list(reversed(amap.gcj02_to_wgs84(*point))) for point in points]}


def measure(points, *, cache=None, state=None, fetch=leg):
    return evidence.measure_amap_route(object(), points, cache if cache is not None else {},
        state if state is not None else {"api_call_limit": 100}, fetch_leg=fetch)


def test_every_adjacent_leg_is_measured_without_whole_route_threshold():
    calls = []
    def fetch(planner, points):
        calls.append(points)
        return leg(planner, points)
    result = measure([A, B, C], fetch=fetch)
    assert calls == [[A, B], [B, C]]
    assert result["status"] == "verified"
    assert result["duration_s"] == 720
    assert result["leg_durations_s"] == [120, 600]
    assert result["distance_m"] == 800
    assert result["geometry_segments"] == [item["geometry"] for item in result["legs"]]


def test_pair_cache_is_directed_reusable_fresh_and_does_not_mutate_saved_result(monkeypatch):
    cache, state = {}, {"api_call_limit": 100}
    first = measure([A, B, C], cache=cache, state=state)
    saved = deepcopy(first)
    measure([B, C], cache=cache, state=state)
    assert state["api_calls"] == 2
    assert state["cache_hits"] == 1
    measure([B, A], cache=cache, state=state)
    assert state["api_calls"] == 3
    monkeypatch.setattr(evidence.time, "time", lambda: 10**12)
    measure([A, B], cache=cache, state=state)
    assert state["api_calls"] == 4
    assert first == saved


def test_old_whole_route_cache_cannot_supply_new_measurement():
    state = {"api_call_limit": 2}
    result = measure([A, B], cache={"amap-final-route-v3|old": {"duration_s": 1}}, state=state)
    assert result["duration_s"] == 120
    assert state["api_calls"] == 1


@pytest.mark.parametrize("problem", ["empty", "zero", "nan", "exception"])
def test_partial_or_invalid_provider_response_cannot_be_verified(problem):
    def fetch(planner, points):
        if points[0] == A:
            return leg(planner, points)
        if problem == "exception":
            raise RuntimeError("secret-key-must-not-be-returned")
        result = leg(planner, points)
        if problem == "empty":
            result["geometry"] = []
        else:
            result["duration_s"] = 0 if problem == "zero" else float("nan")
        return result
    result = measure([A, B, C], fetch=fetch)
    assert result["status"] == "unavailable"
    assert result["duration_s"] is None
    assert result["distance_m"] is None
    assert len(result["legs"]) == 1
    assert "secret-key" not in str(result)


def test_budget_counts_attempts_and_keeps_completed_segments():
    state = {"api_call_limit": 1}
    result = measure([A, B, C], state=state)
    assert result["status"] == "unavailable"
    assert state["api_calls"] == 1
    assert result["issues"] == [{"leg_index": 1, "code": "provider_call_budget_exhausted"}]


def test_short_leg_detour_under_old_three_km_threshold_is_reviewed_not_replaced():
    state = {"api_call_limit": 10, "expected_leg_distances_m": [300]}
    def fetch(planner, points):
        return {**leg(planner, points), "distance_m": 1400}
    result = measure([A, B], state=state, fetch=fetch)
    assert result["status"] == "needs_review"
    assert result["distance_m"] == 1400
    assert state["api_calls"] == 2
    assert "provider_distance_disagreement" in result["legs"][0]["issues"]
    assert result["legs"][0]["osrm_reference_distance_m"] == 300


def test_adjacent_segments_cannot_hide_a_turnaround_at_a_stop():
    result = measure([A, B, A])
    assert result["status"] == "needs_review"
    assert any(item["code"] == "stop_turnaround_needs_review" for item in result["issues"])


def test_continuous_waypoint_comparison_is_saved_without_replacing_segment_times():
    calls = []
    state = {"api_call_limit": 4}
    def context(planner, points):
        calls.append(points)
        return {"duration_s": 450, "distance_m": 950, "geometry": [[121, 31], [121.01, 31.01]]}
    result = evidence.measure_amap_route(object(), [A, B, A], {}, state, fetch_leg=leg, fetch_context=context)
    assert calls == [[A, B, A]]
    assert state["api_calls"] == 3
    assert result["context_checks"][0]["measurement"]["duration_s"] == 450
    assert result["context_checks"][0]["adjacent_duration_s"] == 720
    assert result["duration_s"] == 720
    assert result["status"] == "needs_review"


def test_context_comparison_respects_same_request_budget():
    state = {"api_call_limit": 2}
    def forbidden(*args):
        raise AssertionError("Budget exhausted")
    result = evidence.measure_amap_route(object(), [A, B, A], {}, state, fetch_leg=leg, fetch_context=forbidden)
    assert result["context_checks"][0]["reason"] == "context_check_budget_exhausted"
    assert state["api_calls"] == 2


def test_distinct_road_snaps_are_not_assumed_continuous():
    def fetch(planner, points):
        result = leg(planner, points)
        if points[0] == B:
            result["geometry"][0][0] += 0.001
        return result
    result = measure([A, B, C], fetch=fetch)
    assert result["status"] == "needs_review"
    assert any(item["code"] == "stop_road_continuity_needs_review" for item in result["issues"])


def test_co_located_demand_batches_keep_stop_boundaries_without_api_call():
    state = {"api_call_limit": 1}
    result = measure([A, A, B], state=state)
    assert result["status"] == "verified"
    assert result["leg_durations_s"] == [0, 120]
    assert state["api_calls"] == 1


def test_all_cn_coordinate_sources_use_same_conversion():
    raw = amap.wgs84_to_gcj02(*A)
    assert core._amap_route_point({"lat": A[0], "lng": A[1], "provider": "manual"}) == raw
    assert core._amap_route_point({"lat": raw[0], "lng": raw[1], "provider": "amap"}) == raw
    assert core._amap_route_point({"lat": A[0], "lng": A[1], "provider": "amap", "coordinate_system": "WGS84"}) == raw
    assert core._amap_route_point({"lat": float("nan"), "lng": 121}) is None
    with pytest.raises(ValueError):
        core._route_amap_points([{"lat": 31, "lng": 121}, {}], {"nodes": [0, 1]})


def test_final_gate_and_student_timing_use_same_saved_segments(monkeypatch):
    monkeypatch.setattr(core, "_amap_route_segment_stats", leg)
    monkeypatch.setattr(core, "infer_traffic_location", lambda _: ("CHINA", "Shanghai"))
    monkeypatch.setattr(core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    class Planner:
        AMAP_KEY = "test"
        MAX_ROUTE_DURATION_SECONDS = 7200
    points = [{"lat": p[0], "lng": p[1], **PRECISE_PICKUP, "provider": "amap", "is_depot": i == 0,
               "passenger_count": 0 if i == 0 else 1} for i, p in enumerate([C, A, B])]
    route = {"route_id": "R1", "nodes": [1, 2, 0], "time_s": 420,
             "stop_service_time_s": 120, "distance_m": 800,
             "leg_details": [{"duration_s": 150, "distance_m": 400}, {"duration_s": 150, "distance_m": 400}]}
    scenario = {"routes": [route]}
    config = core.PlannerConfig(service_direction="To School", stop_service_minutes=1)
    core.attach_final_route_traffic_gate(Planner(), scenario, points, config, [], "test")
    assert route["final_route_traffic_gate"]["verified_drive_duration_s"] == 720
    assert route["final_route_traffic_gate"]["verified_total_duration_s"] == 840
    assert route["route_evidence"]["duration_s"] == 720
    rows, unavailable = core._scenario_time_impact_rows(scenario, points, config)
    assert unavailable == 0
    assert [item["offset_s"] for item in rows] == [-840, -660]


def test_fetch_uses_metrics_and_polyline_from_exact_same_provider_path():
    class Planner:
        AMAP_ROUTING_LIMITER = object()
        def amap_request_json(self, endpoint, params, limiter):
            assert endpoint == "/v5/direction/driving"
            assert params["show_fields"] == "cost,navi,polyline"
            assert "waypoints" not in params
            assert limiter is self.AMAP_ROUTING_LIMITER
            return {"route": {"paths": [{"distance": "400", "cost": {"duration": "120"},
                "steps": [{"polyline": "121.400000,31.200000;121.402000,31.202000"}]}]}}
    result = evidence.fetch_amap_leg(Planner(), [A, B])
    assert result["duration_s"] == 120
    assert result["distance_m"] == 400
    assert result["geometry"][0] == list(reversed(amap.gcj02_to_wgs84(*A)))


def test_map_reads_saved_geometry_and_per_stop_times_without_provider_requests(monkeypatch):
    service = importlib.import_module("backend_service")
    saved = measure([A, B, C])
    def forbidden(*args, **kwargs):
        raise AssertionError("Opening a completed map must not remeasure routes")
    monkeypatch.setattr(service, "_fetch_amap_display_geometry", forbidden)
    monkeypatch.setattr(service, "_fetch_amap_display_geometry_by_leg", forbidden)
    monkeypatch.setattr(service, "_should_use_amap_display_geometry", lambda *args: True)
    monkeypatch.setattr(service, "_load_amap_display_cache_unlocked", lambda: {})
    points = [{"lat": p[0], "lng": p[1], **PRECISE_PICKUP, "provider": "amap", "is_depot": i == 2}
              for i, p in enumerate([A, B, C])]
    route = {"route_id": "R1", "nodes": [0, 1, 2], "time_s": 200, "distance_m": 200,
             "stop_service_time_s": 120,
             "leg_details": [{"duration_s": 100, "distance_m": 100}] * 2,
             "route_evidence": saved,
             "final_route_traffic_gate": {"status": "passed", "verified_drive_duration_s": 720,
                                          "verified_total_duration_s": 840, "verified_distance_m": 800}}
    job = {"job_id": "test", "result": {"service_direction": "To School", "structured_results": {
        "current_plan": {"points": points, "routes": [route]}}}}
    original = deepcopy(job)
    payload, error = service._build_job_map_payload(job, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    displayed = payload["routes"][0]
    assert displayed["geometry_segments"] == saved["geometry_segments"]
    assert displayed["duration_s"] == 840
    assert displayed["verified_drive_duration_s"] == 720
    assert displayed["distance_m"] == 800
    assert [stop["cumulative_duration_s"] for stop in payload["stops"]] == [0, 120, 720]
    assert [stop["cumulative_distance_m"] for stop in payload["stops"]] == [0, 400, 800]
    assert [stop["scheduled_offset_s"] for stop in payload["stops"]] == [-840, -660, 0]
    for stop in payload["stops"]:
        assert stop["provider"] == "amap"
        assert stop["coordinate_system"] == "WGS84"
        assert stop["geocode_quality_version"] == PRECISE_PICKUP["geocode_quality_version"]
    assert job == original


def test_legacy_cn_warning_does_not_depend_on_current_key_or_display_toggle(monkeypatch):
    service = importlib.import_module("backend_service")
    monkeypatch.setattr(service, "AMAP_DISPLAY_GEOMETRY_ENABLED", False)
    monkeypatch.setattr(service, "_amap_display_api_key", lambda: "")
    points = [{"lat": p[0], "lng": p[1], "country": "China"} for p in [A, B]]
    job = {"result": {"structured_results": {"current_plan": {
        "points": points, "routes": [{"route_id": "R1", "nodes": [0, 1]}]}}}}
    payload, error = service._build_job_map_payload(job, "current_plan", "current_plan", attach_impact=False)
    assert not error
    assert payload["routes"][0]["evidence_status"] == "legacy"
    assert "Historical result" in payload["routes"][0]["display_geometry_message"]


def test_fleet_keeps_order_and_vehicles_while_measuring_same_shared_edges(monkeypatch):
    service = importlib.import_module("backend_service")
    routing = service._client_module("demand_routing")
    monkeypatch.setattr(core, "_amap_route_segment_stats", leg)
    class Planner:
        AMAP_KEY = "test"
    monkeypatch.setattr(core, "load_legacy_planner", lambda: Planner())
    points = [{"lat": p[0], "lng": p[1], **PRECISE_PICKUP, "provider": "amap", "country": "China",
               "student_count": i + 1 if i < 2 else 0} for i, p in enumerate([A, B, C])]
    route = {"cluster_id": "G01", "order": [1, 2, 0], "selected_vehicle": {"capacity": 42},
             "duration_s": 200, "distance_m": 800, "ordered_points": points,
             "leg_details": [{"duration_s": 100, "distance_m": 400}] * 2}
    plan = {"school": {"country": "China"}, "summary": {"service_direction": "to_school", "max_route_duration_minutes": 60},
            "routes": [route], "route_rows": [{"cluster_id": "G01"}]}
    service._attach_fleet_route_measurements(plan, routing)
    assert route["order"] == [1, 2, 0]
    assert route["selected_vehicle"] == {"capacity": 42}
    assert route["route_evidence"]["leg_durations_s"] == [120, 600]
    assert route["final_route_traffic_gate"]["status"] == "passed"
    data = routing.build_route_preview_map_data(plan)
    assert data["routes"][0]["duration_s"] == 840
    assert data["routes"][0]["stop_service_time_s"] == 120
    assert route["route_evidence"]["duration_s"] == 720
    assert plan["summary"]["total_duration_min"] == 14
    assert plan["route_rows"][0]["duration_min"] == 14
    assert data["routes"][0]["geometry_segments"] == route["route_evidence"]["geometry_segments"]
    assert [stop["cumulative_duration_s"] for stop in data["stops"]] == [0, 120, 720]


def test_fleet_zero_passenger_waypoint_is_not_the_school():
    routing = importlib.import_module("backend_service")._client_module("demand_routing")
    points = [{"lat": p[0], "lng": p[1], "student_count": 0 if i > 0 else 3}
              for i, p in enumerate([A, B, C])]
    data = routing.build_route_preview_map_data({"routes": [{"ordered_points": points}]})
    assert [stop["is_depot"] for stop in data["stops"]] == [False, False, True]


def test_invalid_route_does_not_reuse_previous_route_evidence(monkeypatch):
    analysis = importlib.import_module("direct_school_analysis")
    monkeypatch.setattr(core, "load_legacy_planner", lambda: type("Planner", (), {"AMAP_KEY": "test"})())
    provider = analysis.FreshRouteProvider("amap", departure_time=None, api_call_limit=10)
    provider.state["last_route_evidence"] = {"complete": True, "status": "verified"}
    with pytest.raises(RuntimeError, match="unresolved"):
        provider.route([{}, {}])
    assert not provider.state.get("last_route_evidence")


@pytest.mark.parametrize("module_name", ["BusingProblem", "client_runtime"])
def test_legacy_exports_use_saved_segments_and_visible_review_notes(module_name, tmp_path):
    service = importlib.import_module("backend_service")
    module = core.load_legacy_planner() if module_name == "BusingProblem" else service._client_module("client_runtime")
    saved = measure([A, B, C])
    saved["status"] = "needs_review"
    saved["geometry_segments"][1][0][0] += .005
    points = [{"plot_lat": p[0], "plot_lng": p[1], "address": f"Test stop {index}", "passenger_count": 1}
              for index, p in enumerate([A, B, C])]
    route = {"route_id": "R1", "nodes": [0, 1, 2], "time_s": 9999, "distance_m": 99999,
             "vehicle_id": 1, "bus_type_name": "Bus", "bus_capacity": 42, "load": 2,
             "route_evidence": saved}
    original = deepcopy(route)
    path = tmp_path / f"{module_name}.html"
    module.render_map(points, [route], str(path))
    html = path.read_text(encoding="utf-8")
    assert "Route measurement needs review" in html
    assert "Duration: Not available" in html
    assert "Distance: Not available" in html
    assert "Duration: 12m" not in html
    assert html.count("L.polyline(") == 4
    assert route == original


def test_fleet_keeps_raw_gcj_for_amap_and_converts_only_osrm_requests(monkeypatch):
    routing = importlib.import_module("backend_service")._client_module("demand_routing")
    raw = routing._point_payload({"lat": A[0], "lng": A[1], "provider": "amap"})
    assert core._amap_route_point(raw) == A
    requests = []
    def metrics(origin, destinations):
        requests.append((origin, destinations))
        return [{"duration_s": 123, "distance_m": 456} for _ in destinations]
    monkeypatch.setattr(routing, "compute_osrm_metrics_from_origin", metrics)
    matrix, _ = routing._build_osrm_matrix([raw, {"lat": B[0], "lng": B[1]}])
    assert (requests[0][0]["lat"], requests[0][0]["lng"]) == amap.gcj02_to_wgs84(*A)
    assert matrix[0][1] == 123
    assert (raw["lat"], raw["lng"]) == A


def test_review_evidence_is_not_certified_by_final_gate(monkeypatch):
    monkeypatch.setattr(core, "infer_traffic_location", lambda _: ("CHINA", "Shanghai"))
    monkeypatch.setattr(core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(core, "_amap_route_segment_stats", leg)
    class Planner:
        AMAP_KEY = "test"
        MAX_ROUTE_DURATION_SECONDS = 7200
    points = [{"lat": p[0], "lng": p[1], "provider": "amap", **PRECISE_PICKUP} for p in [A, B]]
    route = {"nodes": [0, 1, 0], "time_s": 1000}
    scenario = {"routes": [route]}
    gate = core.attach_final_route_traffic_gate(Planner(), scenario, points, core.PlannerConfig(), [], "test")
    assert gate["status"] == "unavailable"
    assert route["route_evidence"]["status"] == "needs_review"
    assert route["final_route_traffic_gate"]["passes"] is None
