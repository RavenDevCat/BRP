import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
planner_core = importlib.import_module("planner_core")


def test_am_arrival_gate_replans_once(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS", 2)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_MIN_TARGET_MINUTES", 10)
    monkeypatch.setattr(planner_core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 0)

    def fake_amap_route_stats(planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {
            "duration_s": 9000 if planner.solve_count == 1 else 1200,
            "distance_m": 1234,
            "source": "fake_amap",
        }

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        BUS_TYPE_CONFIGS = []
        NODE_TIME_UPPER_BOUNDS = {}
        MIN_SOLVER_VEHICLE_COUNT = 0
        MAX_ROUTE_DURATION_SECONDS = 3600
        OSRM_BASE_URL = ""
        TRAFFIC_TIME_MULTIPLIER = 1.0
        INPUT_STOPS = [{"address": "Shanghai", "passenger_count": 1}]

        def __init__(self):
            self.solve_count = 0
            self._BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(
                service_direction="To School",
                to_school_arrival_time="08:00",
            )

        def log(self, _message):
            return None

        def build_vehicle_fleet(self):
            return []

        def resolve_osrm_base_url(self, _points):
            return "fake-osrm"

        def build_osrm_full_matrix(self, points):
            return [[0 for _ in points] for _ in points], [[0 for _ in points] for _ in points]

        def solve_routes(self, _points, _solve_time, _solve_distance):
            self.solve_count += 1
            return [
                {
                    "route_id": "Bus 1",
                    "nodes": [0, 1],
                    "time_s": float(self.MAX_ROUTE_DURATION_SECONDS),
                    "stop_service_time_s": 0,
                }
            ]

        def enrich_routes_with_actual_driving(self, _points, _routes):
            return None

        def annotate_and_price_routes(self, _points, _routes):
            return None

        def build_scenario_result(self, _points, routes, _html):
            return {"routes": routes, "bus_count": len(routes)}

    planner = FakePlanner()
    points = [
        {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
        {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
    ]
    result = planner_core._compute_scenario_without_render(planner, points, "smoke")

    assert planner.solve_count == 2
    assert result["traffic_gate"]["status"] == "passed"
    assert len(result["traffic_replan_attempts"]) == 1
    assert result["traffic_replan_attempts"][0]["failed_route_ids"] == ["Bus 1"]


def test_am_arrival_gate_fails_routes_outside_six_to_eight_window(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 0)
    monkeypatch.setattr(
        planner_core,
        "_amap_route_stats",
        lambda _planner, _points, _cache, _state: {
            "duration_s": 9000,
            "distance_m": 1234,
            "source": "fake_amap",
        },
    )

    class FakePlanner:
        AMAP_KEY = "fake"

    result = {
        "routes": [
            {
                "route_id": "Bus 1",
                "nodes": [0, 1],
                "time_s": 3600,
                "stop_service_time_s": 0,
            }
        ]
    }
    points = [
        {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
        {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        result,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Shanghai"}],
        "smoke",
    )

    assert gate["status"] == "failed"
    assert gate["failed_route_ids"] == ["Bus 1"]
    assert round(gate["max_time_window_overrun_minutes"]) == 30
    route_gate = result["routes"][0]["final_route_traffic_gate"]
    assert route_gate["verified_departure_label"] == "06:00"
    assert route_gate["verified_arrival_label"] == "08:30"


def test_am_arrival_gate_does_not_pass_with_unchecked_routes(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)

    calls = {"count": 0}

    def fake_amap_route_stats(_planner, _points, _cache, _state):
        calls["count"] += 1
        if calls["count"] == 2:
            return None
        return {"duration_s": 1200, "distance_m": 1234, "source": "fake_amap"}

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"

    scenario = {
        "routes": [
            {"route_id": "Bus 1", "nodes": [0, 1], "time_s": 1200, "stop_service_time_s": 0},
            {"route_id": "Bus 2", "nodes": [0, 2], "time_s": 1200, "stop_service_time_s": 0},
        ]
    }
    points = [
        {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
        {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
        {"provider": "amap", "lat": 31.3, "lng": 121.3, "adcode": "310000"},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Shanghai"}],
        "smoke",
    )

    assert gate["status"] == "unavailable"
    assert gate["checked_route_count"] == 1
    assert gate["unavailable_route_count"] == 1
    assert scenario["traffic_feasible"] is False
