import importlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
planner_core = importlib.import_module("planner_core")


def test_am_arrival_gate_replans_once(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS", 2)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS", 0)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_MIN_TARGET_MINUTES", 10)
    monkeypatch.setattr(planner_core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 0)

    def fake_amap_route_stats(planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {
            "duration_s": 9000 if planner.last_bus_count < 2 else 1200,
            "distance_m": 1234,
            "source": "fake_amap",
        }

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        BUS_TYPE_CONFIGS = [{"name": "bus", "capacity": 99, "max_count": 3}]
        NODE_TIME_UPPER_BOUNDS = {}
        MIN_SOLVER_VEHICLE_COUNT = 0
        MAX_ROUTE_DURATION_SECONDS = 3600
        OSRM_BASE_URL = ""
        TRAFFIC_TIME_MULTIPLIER = 1.0
        INPUT_STOPS = [{"address": "Shanghai", "passenger_count": 1}]

        def __init__(self):
            self.solve_count = 0
            self.last_bus_count = 0
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
            self.last_bus_count = max(1, int(self.MIN_SOLVER_VEHICLE_COUNT or 0))
            return [
                {
                    "route_id": f"Bus {index + 1}",
                    "nodes": [0, 1],
                    "time_s": float(self.MAX_ROUTE_DURATION_SECONDS),
                    "stop_service_time_s": 0,
                }
                for index in range(self.last_bus_count)
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
    assert result["bus_count"] == 2
    assert result["traffic_gate"]["status"] == "passed"
    assert result["feasibility_report"]["status"] == "passed"
    assert result["feasibility_report"]["hard_constraints"]["fleet"]["recommended_min_active_vehicle_count"] == 2
    assert len(result["traffic_replan_attempts"]) == 1
    assert result["traffic_replan_attempts"][0]["action"] == "increase_active_vehicles"
    assert result["traffic_replan_attempts"][0]["feasibility_status"] == "failed"
    assert result["traffic_replan_attempts"][0]["failure_reasons"] == ["arrival_window"]
    assert result["traffic_replan_attempts"][0]["failed_route_ids"] == ["Bus 1"]
    assert result["traffic_replan_attempts"][0]["to_min_solver_vehicle_count"] == 2
    assert result["traffic_replan_attempts"][0]["to_route_duration_minutes"] == 60
    assert result["traffic_replan_attempts"][0]["checked_route_count"] == 1
    assert result["traffic_replan_attempts"][0]["unavailable_route_count"] == 0
    assert result["traffic_replan_attempts"][0]["api_calls"] == 1
    assert result["traffic_replan_attempts"][0]["cache_hits"] == 0
    assert result["traffic_gate"]["replan_attempts"][0]["api_calls"] == 1


def test_pm_route_duration_gate_tightens_route_target_before_saving(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS", 2)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS", 1)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_REPLAN_ATTEMPTS", 1)
    monkeypatch.setattr(planner_core, "PM_ROUTE_GATE_GRACE_MINUTES", 0)

    def fake_amap_route_stats(planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {
            "duration_s": 3300 if planner.MAX_ROUTE_DURATION_SECONDS < 3600 else 5000,
            "distance_m": 1234,
            "source": "fake_amap",
        }

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        BUS_TYPE_CONFIGS = [{"name": "bus", "capacity": 99, "max_count": 20}]
        NODE_TIME_UPPER_BOUNDS = {}
        MIN_SOLVER_VEHICLE_COUNT = 0
        MAX_ROUTE_DURATION_SECONDS = 3600
        OSRM_BASE_URL = ""
        TRAFFIC_TIME_MULTIPLIER = 1.0
        INPUT_STOPS = [{"address": "Shanghai", "passenger_count": 1}]

        def __init__(self):
            self.solve_count = 0
            self.last_bus_count = 0
            self._BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(
                service_direction="From School",
                from_school_departure_time="15:40",
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
            max_count = sum(int(item.get("max_count", 0) or 0) for item in self.BUS_TYPE_CONFIGS) or 20
            self.last_bus_count = min(max_count, max(14, int(self.MIN_SOLVER_VEHICLE_COUNT or 0)))
            return [
                {
                    "route_id": f"Bus {index + 1}",
                    "nodes": [0, 1],
                    "time_s": float(self.MAX_ROUTE_DURATION_SECONDS),
                    "stop_service_time_s": 0,
                }
                for index in range(self.last_bus_count)
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
    result = planner_core._compute_scenario_without_render(planner, points, "pm-smoke")

    assert result["bus_count"] == 13
    assert result["traffic_gate"]["status"] == "passed"
    assert result["traffic_gate"]["gate_type"] == "route_duration"
    assert result["traffic_gate"]["traffic_policy"]["status"] == "ready"
    assert result["traffic_gate"]["target_duration_minutes"] == 60
    assert result["traffic_gate"]["solver_target_duration_minutes"] < 60
    assert result["feasibility_report"]["status"] == "passed"
    assert result["traffic_replan_attempts"][0]["action"] == "tighten_route_target"
    assert result["traffic_replan_attempts"][0]["to_min_solver_vehicle_count"] == 0
    assert result["traffic_replan_attempts"][0]["to_route_duration_minutes"] < 60
    assert result["traffic_vehicle_search_attempts"][0]["target_bus_count"] == 13
    assert result["traffic_vehicle_search_attempts"][0]["status"] == "passed"


def test_vehicle_floor_error_falls_back_to_route_target_search(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ENABLED", True)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_REPLAN_ATTEMPTS", 4)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VEHICLE_SEARCH_ATTEMPTS", 0)
    monkeypatch.setattr(planner_core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 0)

    def fake_amap_route_stats(planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {
            "duration_s": 1200 if planner.MAX_ROUTE_DURATION_SECONDS < 3600 else 9000,
            "distance_m": 1234,
            "source": "fake_amap",
        }

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        BUS_TYPE_CONFIGS = [{"name": "bus", "capacity": 99, "max_count": 20}]
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
            if int(self.MIN_SOLVER_VEHICLE_COUNT or 0) >= 2:
                raise RuntimeError("forced vehicle-floor infeasible")
            return [
                {
                    "route_id": f"Bus {index + 1}",
                    "nodes": [0, 1],
                    "time_s": float(self.MAX_ROUTE_DURATION_SECONDS),
                    "stop_service_time_s": 0,
                }
                for index in range(1)
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
    result = planner_core._compute_scenario_without_render(planner, points, "am-fallback-smoke")

    assert result["bus_count"] == 1
    assert result["traffic_gate"]["status"] == "passed"
    assert result["traffic_gate"]["solver_target_duration_minutes"] < 60
    assert result["traffic_replan_attempts"][0]["action"] == "increase_active_vehicles"
    assert result["traffic_replan_attempts"][0]["error"] == "forced vehicle-floor infeasible"
    assert result["traffic_replan_attempts"][1]["action"] == "tighten_route_target_after_vehicle_error"


def test_time_constraint_uses_reduced_limit_without_current_vehicle_floor(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", False)

    class FakePlanner:
        AMAP_KEY = "fake"
        BUS_TYPE_CONFIGS = [{"name": "bus", "capacity": 99, "max_count": 21}]
        NODE_TIME_UPPER_BOUNDS = {}
        MIN_SOLVER_VEHICLE_COUNT = 0
        MAX_ROUTE_DURATION_SECONDS = 3600
        OSRM_BASE_URL = ""
        TRAFFIC_TIME_MULTIPLIER = 1.0
        INPUT_STOPS = [{"address": "Shanghai", "passenger_count": 1}]

        def __init__(self):
            self.min_counts_seen = []
            self.fleet_counts_seen = []
            self._BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(service_direction="To School")

        def log(self, _message):
            return None

        def build_vehicle_fleet(self):
            fleet = []
            for item in self.BUS_TYPE_CONFIGS:
                for _ in range(int(item.get("max_count", 0) or 0)):
                    fleet.append({"name": item["name"], "capacity": item["capacity"]})
            self.fleet_counts_seen.append(len(fleet))
            return fleet

        def solver_capacity_for_vehicle(self, item):
            return int(item.get("capacity", 0) or 0)

        def _minimum_vehicle_count_for_demand(self, _demand, _fleet):
            return 1

        def route_stop_limit(self):
            return 10

        def split_oversized_demand_points(self, points, _max_capacity):
            return points

        def resolve_osrm_base_url(self, _points):
            return "fake-osrm"

        def build_osrm_full_matrix(self, points):
            return [[0 for _ in points] for _ in points], [[0 for _ in points] for _ in points]

        def solve_routes(self, _points, _solve_time, _solve_distance):
            self.min_counts_seen.append(int(self.MIN_SOLVER_VEHICLE_COUNT or 0))
            return [{"route_id": "Bus 1", "nodes": [0, 1], "time_s": 1200, "stop_service_time_s": 0}]

        def enrich_routes_with_actual_driving(self, _points, _routes):
            return None

        def annotate_and_price_routes(self, _points, _routes):
            return None

        def build_scenario_result(self, _points, routes, _html):
            return {"routes": routes, "bus_count": len(routes)}

    planner = FakePlanner()
    metadata = {"current_route_count": 21}
    result = planner_core._compute_scenario_without_render(
        planner,
        [
            {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
            {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
        ],
        "15-minute smoke",
        reduced_vehicle_limit=20,
        node_time_upper_bounds_builder=lambda _points: {1: 3600},
        time_constraint_metadata=metadata,
    )

    assert planner.fleet_counts_seen[0] == 20
    assert planner.min_counts_seen == [0]
    assert result["time_constraint"]["min_solver_vehicle_count"] == 0
    assert result["time_constraint"]["reduced_vehicle_limit"] == 20
    assert metadata["min_solver_vehicle_count"] == 0


def test_am_arrival_gate_fails_routes_outside_default_six_thirty_to_eight_window(monkeypatch):
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
    assert gate["traffic_policy"]["provider"] == "amap"
    assert gate["traffic_policy"]["status"] == "ready"
    assert round(gate["max_time_window_overrun_minutes"]) == 60
    route_gate = result["routes"][0]["final_route_traffic_gate"]
    assert route_gate["verified_departure_label"] == "06:30"
    assert route_gate["verified_arrival_label"] == "09:00"
    reverse_check = result["arrival_reverse_check"]
    assert reverse_check["target_arrival_label"] == "08:00"
    assert reverse_check["earliest_departure_label"] == "06:30"
    assert reverse_check["warning_route_count"] == 1
    assert reverse_check["warning_route_ids"] == ["Bus 1"]
    assert reverse_check["earliest_required_departure_label"] == "05:30"
    assert round(reverse_check["max_departure_window_overrun_minutes"]) == 60
    route_reverse_check = result["routes"][0]["arrival_reverse_check"]
    assert route_reverse_check["required_departure_label"] == "05:30"
    assert route_reverse_check["before_earliest_departure"] is True

    report = planner_core.build_route_feasibility_report(
        result,
        gate,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        current_min_active_vehicle_count=1,
        max_vehicle_count=3,
    )
    assert report["status"] == "failed"
    assert report["failure_reasons"] == ["arrival_window"]
    assert report["hard_constraints"]["fleet"]["recommended_min_active_vehicle_count"] == 2

    over_limit_report = planner_core.build_route_feasibility_report(
        {"routes": [], "bus_count": 4},
        {"status": "passed", "gate_type": "arrival_window"},
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        current_min_active_vehicle_count=0,
        max_vehicle_count=3,
    )
    assert over_limit_report["status"] == "failed"
    assert "vehicle_savings_target" in over_limit_report["failure_reasons"]
    assert over_limit_report["hard_constraints"]["fleet"]["status"] == "over_vehicle_limit"

    single_rider_report = planner_core.build_route_feasibility_report(
        {"routes": [{"route_id": "Bus 1", "load": 1, "bus_capacity": 18}], "bus_count": 1},
        {"status": "passed", "gate_type": "arrival_window"},
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        current_min_active_vehicle_count=0,
        max_vehicle_count=3,
    )
    assert single_rider_report["status"] == "failed"
    assert "single_rider_route" in single_rider_report["failure_reasons"]
    assert single_rider_report["hard_constraints"]["single_rider_route"]["failed_route_ids"] == ["Bus 1"]


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


def test_final_route_traffic_policy_skips_unsupported_locations(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("THAILAND", "Bangkok"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)

    class FakePlanner:
        AMAP_KEY = "fake"

    scenario = {
        "routes": [
            {"route_id": "Bus 1", "nodes": [0, 1], "time_s": 1200, "stop_service_time_s": 0},
        ]
    }
    points = [
        {"is_depot": True, "provider": "google", "lat": 37.5, "lng": 127.0},
        {"provider": "google", "lat": 37.6, "lng": 127.1},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Bangkok"}],
        "bk-smoke",
    )

    assert gate["status"] == "not_applicable"
    assert gate["provider"] == "none"
    assert gate["traffic_policy"]["status"] == "not_applicable"


def test_korea_final_route_traffic_policy_requires_kakao_key(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("SOUTH KOREA", "Seoul Metro"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "KAKAO_NAVI_API_KEY", "")

    class FakePlanner:
        AMAP_KEY = ""

    scenario = {
        "routes": [
            {"route_id": "Bus 1", "nodes": [0, 1], "time_s": 1200, "stop_service_time_s": 0},
        ]
    }
    points = [
        {"is_depot": True, "provider": "google", "lat": 37.5, "lng": 127.0},
        {"provider": "google", "lat": 37.6, "lng": 127.1},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Seoul"}],
        "kr-smoke",
    )

    assert gate["status"] == "unavailable"
    assert gate["provider"] == "kakao_navi"
    assert gate["reason"] == "missing_kakao_navi_key"
    assert gate["traffic_policy"]["status"] == "unavailable"


def test_korea_final_route_gate_uses_kakao_future_departure_and_chunks(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("SOUTH KOREA", "Seoul Metro"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "KAKAO_NAVI_API_KEY", "fake")
    monkeypatch.setattr(planner_core, "KAKAO_NAVI_MAX_WAYPOINTS", 1)
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_MAX_CALLS", 10)
    monkeypatch.setattr(planner_core, "load_json_object", lambda _path: {})
    monkeypatch.setattr(planner_core, "save_json_object", lambda *_args, **_kwargs: None)

    tz = ZoneInfo("Asia/Seoul")

    def fake_next_service_datetime(minutes, country):
        assert country == "SOUTH KOREA"
        total = int(round(float(minutes))) % (24 * 60)
        return datetime(2026, 6, 29, total // 60, total % 60, tzinfo=tz)

    departures = []

    def fake_kakao_segment(points, departure_time):
        departures.append((len(points), departure_time))
        return {"duration_s": 600, "distance_m": 1000}

    monkeypatch.setattr(planner_core, "_next_service_datetime", fake_next_service_datetime)
    monkeypatch.setattr(planner_core, "_kakao_route_segment_stats", fake_kakao_segment)

    class FakePlanner:
        AMAP_KEY = ""

    scenario = {
        "routes": [
            {
                "route_id": "Bus 1",
                "nodes": [0, 1, 2, 3],
                "time_s": 1800,
                "stop_service_time_s": 0,
            },
        ]
    }
    points = [
        {"is_depot": True, "provider": "google", "lat": 37.50, "lng": 127.00},
        {"provider": "google", "lat": 37.51, "lng": 127.01},
        {"provider": "google", "lat": 37.52, "lng": 127.02},
        {"provider": "google", "lat": 37.53, "lng": 127.03},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Seoul"}],
        "kr-smoke",
    )

    assert gate["status"] == "passed"
    assert gate["provider"] == "kakao_navi"
    assert gate["api_calls"] == 2
    assert gate["cache_hits"] == 0
    route_gate = scenario["routes"][0]["final_route_traffic_gate"]
    assert route_gate["verified_source"] == "kakao_navi_final_route"
    assert route_gate["provider_segment_count"] == 2
    assert route_gate["provider_departure_time"] == "2026-06-29T07:30:00+09:00"
    assert route_gate["verified_total_duration_s"] == 1200
    assert route_gate["verified_departure_label"] == "07:40"
    assert [item[0] for item in departures] == [3, 2]
    assert [item[1].strftime("%H:%M") for item in departures] == ["07:30", "07:40"]


def test_failed_free_baseline_can_recover_from_passing_time_constrained_result():
    failed_free = {
        "bus_count": 15,
        "traffic_gate": {"status": "failed", "failed_route_count": 3},
        "feasibility_report": {"status": "failed", "failure_reasons": ["arrival_window"]},
    }
    passing_time_constrained = {
        "bus_count": 18,
        "traffic_gate": {"status": "passed", "failed_route_count": 0},
        "feasibility_report": {"status": "passed", "failure_reasons": []},
        "time_constraint": {"enabled": True},
    }
    recovered = planner_core._recover_free_baseline_from_time_constrained(
        failed_free,
        passing_time_constrained,
        planner_core.PlannerConfig(),
        [{"name": "bus", "capacity": 42, "max_count": 20}],
    )

    assert recovered["bus_count"] == 18
    assert recovered["baseline_name"] == "free_optimization_baseline"
    assert recovered["traffic_recovery"]["source"] == "time_constrained_optimization"
    assert recovered["time_constraint"]["used_as_free_baseline_recovery"] is True

    already_passing = {
        "bus_count": 16,
        "traffic_gate": {"status": "passed"},
        "feasibility_report": {"status": "passed"},
    }
    unchanged = planner_core._recover_free_baseline_from_time_constrained(
        already_passing,
        passing_time_constrained,
        planner_core.PlannerConfig(),
        [{"name": "bus", "capacity": 42, "max_count": 20}],
    )
    assert unchanged is already_passing


def test_pm_final_route_gate_caps_duration_at_two_hour_window(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "PM_ROUTE_GATE_GRACE_MINUTES", 0)
    monkeypatch.setattr(planner_core, "PM_MAX_ROUTE_WINDOW_MINUTES", 120)

    def fake_amap_route_stats(_planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {"duration_s": 125 * 60, "distance_m": 1234, "source": "fake_amap"}

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        MAX_ROUTE_DURATION_SECONDS = 133 * 60

    scenario = {
        "routes": [
            {"route_id": "Bus 1", "nodes": [0, 1], "time_s": 60 * 60, "stop_service_time_s": 0},
        ]
    }
    points = [
        {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
        {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
    ]

    gate = planner_core.attach_final_route_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="From School", from_school_departure_time="15:40"),
        [{"address": "Shanghai"}],
        "pm-window-smoke",
    )

    assert gate["status"] == "failed"
    assert gate["target_duration_minutes"] == 120
    assert round(gate["max_route_duration_overrun_minutes"]) == 5


def test_current_plan_scenario_reuses_final_route_gate(monkeypatch):
    monkeypatch.setattr(planner_core, "infer_traffic_location", lambda _records: ("CHINA", "Shanghai"))
    monkeypatch.setattr(planner_core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(planner_core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 0)

    def fake_amap_route_stats(_planner, _points, _cache, state):
        state["api_calls"] = int(state.get("api_calls", 0)) + 1
        return {"duration_s": 55 * 60, "distance_m": 1234, "source": "fake_amap"}

    monkeypatch.setattr(planner_core, "_amap_route_stats", fake_amap_route_stats)

    class FakePlanner:
        AMAP_KEY = "fake"
        MAX_ROUTE_DURATION_SECONDS = 60 * 60

    scenario = {
        "enabled": True,
        "routes": [
            {"route_id": "1-toschool", "nodes": [0, 1], "time_s": 55 * 60, "stop_service_time_s": 60},
        ],
    }
    points = [
        {"is_depot": True, "provider": "amap", "lat": 31.1, "lng": 121.1, "adcode": "310000"},
        {"provider": "amap", "lat": 31.2, "lng": 121.2, "adcode": "310000"},
    ]

    gate = planner_core.attach_current_plan_traffic_gate(
        FakePlanner(),
        scenario,
        points,
        planner_core.PlannerConfig(service_direction="To School", to_school_arrival_time="08:00"),
        [{"address": "Shanghai"}],
    )

    assert gate is scenario["traffic_gate"]
    assert gate["scenario"] == "Current Plan"
    assert gate["status"] == "passed"
    assert scenario["traffic_feasible"] is True
    assert scenario["routes"][0]["final_route_traffic_gate"]["scenario"] == "Current Plan"
