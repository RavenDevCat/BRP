from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
import route_evidence as evidence
import planner_core as core
from amap_driving import gcj02_to_wgs84

A, B, C = (31.20, 121.40), (31.202, 121.402), (31.201, 121.401)


def native_path(points=(A, B, C), durations=(180, 360), distances=(400, 200)):
    steps = [{"step_distance": str(distances[i]), "cost": {"duration": str(durations[i])},
              "polyline": ";".join(f"{p[1]},{p[0]}" for p in points[i:i+2]),
              "navi": {"assistant_action": "\u5230\u8fbe\u9014\u7ecf\u5730" if i < len(points)-2 else "\u5230\u8fbe\u76ee\u7684\u5730"}}
             for i in range(len(points)-1)]
    return {"distance": str(sum(distances)), "cost": {"duration": str(sum(durations))}, "steps": steps}


class Planner:
    AMAP_KEY = "test"
    AMAP_ROUTING_LIMITER = object()
    MAX_ROUTE_DURATION_SECONDS = 7200

    def __init__(self, path=None):
        self.path = path if path is not None else native_path()
        self.calls = []

    def amap_request_json(self, endpoint, params, limiter):
        assert endpoint == "/v5/direction/driving"
        assert params["waypoints"] == f"{B[1]:.6f},{B[0]:.6f}"
        self.calls.append(deepcopy(params))
        return {"route": {"paths": [deepcopy(self.path)]}}


def adjacent(_planner, points):
    return {"distance_m": 400 if points[0] == A else 200, "duration_s": 100,
            "geometry": [list(reversed(gcj02_to_wgs84(*p))) for p in points]}


def measure(planner, cache=None, state=None):
    return evidence.measure_amap_route(planner, [A, B, C], cache if cache is not None else {},
        state if state is not None else {"api_call_limit": 3}, fetch_leg=adjacent)


def test_native_boundaries_keep_exact_uneven_leg_times_and_navigation():
    result = evidence.fetch_amap_itinerary(Planner(), [A, B, C])
    assert result["segmentation"] == "native_navigation_boundaries"
    assert [leg["duration_s"] for leg in result["waypoint_legs"]] == [180, 360]
    assert [leg["distance_m"] for leg in result["waypoint_legs"]] == [400, 200]
    assert result["waypoint_legs"][0]["native_step_end"] == 0
    assert result["waypoint_legs"][1]["native_steps"] == [native_path()["steps"][1]]


@pytest.mark.parametrize("mutation", ["no_markers", "extra_marker", "destination_missing", "missing_cost",
    "negative", "nan", "wrong_time_total", "wrong_distance_total", "missing_geometry", "zero_leg"])
def test_incomplete_native_boundaries_never_fabricate_segment_times(mutation):
    path = native_path()
    if mutation == "no_markers":
        path["steps"][0]["navi"] = {}
    elif mutation == "extra_marker":
        path["steps"].insert(0, deepcopy(path["steps"][0]))
    elif mutation == "destination_missing":
        path["steps"][-1]["navi"] = {}
    elif mutation == "missing_cost":
        path["steps"][0].pop("cost")
    elif mutation in {"negative", "nan"}:
        path["steps"][0]["cost"]["duration"] = -1 if mutation == "negative" else "nan"
    elif mutation == "wrong_time_total":
        path["cost"]["duration"] = "999"
    elif mutation == "wrong_distance_total":
        path["distance"] = "999"
    elif mutation == "missing_geometry":
        path["steps"][0].pop("polyline")
    elif mutation == "zero_leg":
        path["steps"][0]["step_distance"] = "0"
    result = measure(Planner(path))
    assert result["source"] == "amap_adjacent_legs"
    assert result["status"] == "needs_review"
    assert result["leg_durations_s"] == [100, 100]
    assert result["continuous_measurement"]["chunks"][0]["reason"] == "native_boundaries_unavailable"


@pytest.mark.parametrize("durations,distances", [((180, 360), (400, 200)), ((30, 60), (350, 180))])
def test_continuous_recovery_uses_native_response_whether_longer_or_shorter(durations, distances):
    planner, state = Planner(native_path(durations=durations, distances=distances)), {"api_call_limit": 3}
    result = measure(planner, state=state)
    assert result["source"] == "amap_continuous_waypoint_legs"
    assert result["status"] == "verified"
    assert result["leg_durations_s"] == list(durations)
    assert result["distance_m"] == sum(distances)
    assert result["duration_s"] == sum(durations)
    assert "adjacent_comparison" not in result
    assert result["geometry_segments"] == [leg["geometry"] for leg in result["legs"]]
    assert state["api_calls"] == 1
    assert len(planner.calls) == 1


def test_native_recovery_keeps_independent_detour_warning():
    result = measure(Planner(native_path(distances=(400, 2400))))
    assert result["source"] == "amap_continuous_waypoint_legs"
    assert result["status"] == "verified"
    assert result["issues"] == []
    assert {i["code"] for i in result["warnings"]} == {"large_direct_detour_needs_review"}


def test_single_vertex_one_metre_arrival_keeps_native_cost_without_interpolation():
    path = native_path()
    arrival = deepcopy(path["steps"][0])
    arrival.update(step_distance="1", polyline=f"{B[1]},{B[0]}", cost={"duration": "1"})
    path["steps"][0]["navi"] = {}
    path["steps"].insert(1, arrival)
    path["distance"], path["cost"]["duration"] = "601", "541"
    legs = evidence._native_waypoint_legs(path, [A, B, C])
    assert [leg["duration_s"] for leg in legs] == [181, 360]
    assert legs[0]["native_step_end"] == 1


def test_provider_step_geometry_gap_cannot_be_bridged_to_make_verified_leg():
    path = native_path()
    first = deepcopy(path["steps"][0])
    first["navi"] = {}
    first["polyline"] = "121.4,31.2;121.4005,31.2005"
    path["steps"].insert(0, first)
    path["distance"], path["cost"]["duration"] = "1000", "720"
    with pytest.raises(ValueError, match="geometry"):
        evidence._native_waypoint_legs(path, [A, B, C])


def test_recovery_cache_does_not_change_direct_pair_cache_or_saved_evidence():
    planner, cache = Planner(), {}
    result = measure(planner, cache=cache)
    saved = deepcopy(result)
    again = measure(planner, cache=cache, state={"api_call_limit": 1})
    assert again["duration_s"] == 540
    assert len(planner.calls) == 1
    pair = evidence.measure_amap_route(planner, [B, C], cache, {"api_call_limit": 1}, fetch_leg=adjacent)
    assert pair["duration_s"] == 100
    assert result == saved


def test_recovery_respects_budget_and_preserves_unknown_status():
    planner = Planner()
    result = measure(planner, state={"api_call_limit": 0})
    assert planner.calls == []
    assert result["status"] == "unavailable"
    assert result["continuous_measurement"]["chunks"][0]["reason"] == "provider_call_budget_exhausted"


def test_recovery_does_not_drop_waypoints_to_fit_provider_limit():
    points = [A, B] * 10
    def forbidden(*args):
        raise AssertionError("Do not request a truncated route")
    legs, issues, recovery = evidence._recover_continuous_legs(object(), points, {}, {"api_call_limit": 100}, [], forbidden)
    assert not legs and not issues
    assert recovery["reason"] == "waypoint_limit"


def test_gate_student_ride_and_map_share_native_segments_with_zero_rider_waypoint(monkeypatch):
    monkeypatch.setattr(core, "infer_traffic_location", lambda _: ("CHINA", "Shanghai"))
    monkeypatch.setattr(core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(core, "_amap_route_segment_stats", adjacent)
    quality = __import__("amap_geocode_quality").GEOCODE_QUALITY_VERSION
    points = [{"lat": p[0], "lng": p[1], "provider": "amap", "geocode_quality_version": quality,
               "geocode_level": "\u5174\u8da3\u70b9", "is_depot": i == 0,
               "passenger_count": 4 if i == 1 else 0} for i, p in enumerate([C, A, B])]
    route = {"route_id": "R1", "nodes": [1, 2, 0], "time_s": 320, "distance_m": 600,
             "stop_service_time_s": 120, "leg_details": [{"duration_s": 100, "distance_m": 400},
                                                           {"duration_s": 100, "distance_m": 200}]}
    scenario = {"routes": [route]}
    config = core.PlannerConfig(service_direction="To School", stop_service_minutes=1)
    core.attach_final_route_traffic_gate(Planner(), scenario, points, config, [], "test")
    assert route["nodes"] == [1, 2, 0]
    assert route["route_evidence"]["source"] == "amap_continuous_waypoint_legs"
    assert route["final_route_traffic_gate"]["verified_total_duration_s"] == 660
    rows, unavailable = core._scenario_time_impact_rows(scenario, points, config)
    assert unavailable == 0
    assert [row["offset_s"] for row in rows] == [-660, -420]
    import backend_service as service
    job = {"job_id": "test", "result": {"service_direction": "To School", "structured_results": {
        "current_plan": {"points": points, "routes": [route]}}}}
    payload, error = service._build_job_map_payload(job, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    assert payload["routes"][0]["duration_s"] == 660
    assert payload["routes"][0]["geometry_segments"] == route["route_evidence"]["geometry_segments"]
    assert [stop["cumulative_duration_s"] for stop in payload["stops"]] == [0, 180, 540]


def test_continuous_is_primary_even_without_an_obvious_turnaround():
    points = (A, B, (31.204, 121.404))
    planner = Planner(native_path(points=points))
    state = {"api_call_limit": 1}
    def forbidden(*args):
        raise AssertionError("A complete native itinerary must not request independent edges")
    result = evidence.measure_amap_route(planner, list(points), {}, state, fetch_leg=forbidden)
    assert result["source"] == "amap_continuous_waypoint_legs"
    assert result["status"] == "verified"
    assert result["duration_s"] == 540
    assert state["api_calls"] == 1


class ChunkPlanner:
    AMAP_ROUTING_LIMITER = object()

    def __init__(self, mutate_overlap=False):
        self.calls = []
        self.mutate_overlap = mutate_overlap

    def amap_request_json(self, endpoint, params, limiter):
        self.calls.append(params)
        points = [tuple(reversed(tuple(map(float, value.split(",")))))
                  for value in [params["origin"], *params["waypoints"].split(";"), params["destination"]]]
        assert len(points) <= 18
        path = native_path(points, (60,)*(len(points)-1), (300,)*(len(points)-1))
        if self.mutate_overlap and len(self.calls) == 2:
            a, b = points[:2]
            path["steps"][0]["polyline"] = f"{a[1]},{a[0]};{a[1]+.003},{a[0]};{b[1]},{b[0]}"
        return {"route": {"paths": [path]}}


@pytest.mark.parametrize("point_count", [18, 19, 33, 34])
def test_long_routes_preserve_every_stop_and_charge_overlap_only_once(point_count):
    points = [(31.2+i*.002, 121.4) for i in range(point_count)]
    planner, cache, state = ChunkPlanner(), {}, {"api_call_limit": 3}
    def forbidden(*args):
        raise AssertionError("Native chunks should not need pair requests")
    result = evidence.measure_amap_route(planner, points, cache, state, fetch_leg=forbidden)
    assert result["status"] == "verified"
    assert len(result["legs"]) == point_count-1
    assert result["duration_s"] == (point_count-1)*60
    assert result["distance_m"] == (point_count-1)*300
    assert [leg["leg_index"] for leg in result["legs"]] == list(range(point_count-1))
    assert [leg["origin"] for leg in result["legs"]] == [list(p) for p in points[:-1]]
    assert len(planner.calls) == (point_count-4)//15+1
    original = deepcopy(result)
    again = evidence.measure_amap_route(planner, points, cache, {"api_call_limit": 0}, fetch_leg=forbidden)
    assert again["duration_s"] == result["duration_s"]
    assert result == original


def test_long_route_cannot_silently_reset_approach_at_chunk_boundary():
    points = [(31.2+i*.002, 121.4) for i in range(19)]
    planner = ChunkPlanner(mutate_overlap=True)
    result = evidence.measure_amap_route(planner, points, {}, {"api_call_limit": 2})
    assert result["status"] == "unavailable"
    assert result["continuous_measurement"]["chunks"][-1]["reason"] == "continuous_chunk_join_unverified"
    assert len(planner.calls) == 2


def test_missing_native_proof_keeps_all_independent_edges_diagnostic_not_verified():
    path = native_path(points=(A, B, (31.204, 121.404)))
    path["steps"][0]["navi"] = {}
    result = evidence.measure_amap_route(Planner(path), [A, B, (31.204, 121.404)], {},
        {"api_call_limit": 3}, fetch_leg=adjacent)
    assert result["complete"] is True
    assert result["point_count"] == 3
    assert result["status"] == "needs_review"
    assert any(issue["code"] == "continuous_itinerary_unverified" for issue in result["issues"])
