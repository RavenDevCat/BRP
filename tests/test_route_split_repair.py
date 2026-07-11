import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
planner_core = importlib.import_module("planner_core")


class FakePlanner:
    BUS_TYPE_CONFIGS = [
        {"name": "mid", "capacity": 20, "max_count": 1},
        {"name": "small", "capacity": 10, "max_count": 1},
    ]
    NODE_TIME_LOWER_BOUNDS = {1: 0, 2: 0, 3: 0, 4: 0}
    NODE_TIME_UPPER_BOUNDS = {1: 100, 2: 100, 3: 100, 4: 100}
    MIN_ACTIVE_ROUTE_PASSENGERS = 2

    def __init__(self):
        self.logs = []

    def build_vehicle_fleet(self):
        return [
            {"name": "mid", "capacity": 20, "comfort_capacity": 20},
            {"name": "small", "capacity": 10, "comfort_capacity": 10},
        ]

    def solver_capacity_for_vehicle(self, vehicle):
        return int(vehicle.get("comfort_capacity") or vehicle.get("capacity") or 0)

    def enrich_routes_with_actual_driving(self, _points, routes):
        for route in routes:
            route["time_s"] = 100
            route["distance_m"] = 1000
            route["stop_service_time_s"] = max(0, len(route["nodes"]) - 2) * 60
            route["leg_details"] = []

    def annotate_and_price_routes(self, _points, routes):
        for route in routes:
            route["operating_cost"] = 0
            route["chargeable_revenue"] = 0
            route["profit_loss"] = 0

    def build_scenario_result(self, points, routes, _html):
        return {
            "points": points,
            "routes": routes,
            "bus_count": len(routes),
            "service_stop_count": len(points) - 1,
        }

    def log(self, message):
        self.logs.append(message)


def test_failed_route_is_split_with_spare_vehicle_and_rechecked(monkeypatch):
    def fake_gate(_planner, scenario, _points, _config, _records, _label):
        failed_ids = []
        for index, route in enumerate(scenario["routes"], start=1):
            route_id = str(route.get("route_id") or f"Bus {index}")
            passed = len([node for node in route["nodes"] if node]) <= 2
            route["final_route_traffic_gate"] = {
                "status": "passed" if passed else "failed",
                "verified_total_duration_s": 1200 if passed else 6000,
                "time_window_overrun_minutes": 0 if passed else 10,
            }
            if not passed:
                failed_ids.append(route_id)
        gate = {
            "status": "passed" if not failed_ids else "failed",
            "failed_route_count": len(failed_ids),
            "failed_route_ids": failed_ids,
            "max_time_window_overrun_minutes": 0 if not failed_ids else 10,
        }
        scenario["traffic_gate"] = gate
        return gate

    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)
    points = [
        {"is_depot": True, "passenger_count": 0},
        {"passenger_count": 3},
        {"passenger_count": 3},
        {"passenger_count": 3},
        {"passenger_count": 3},
    ]
    result = {
        "routes": [
            {
                "route_id": "Bus 1",
                "vehicle_id": 1,
                "bus_type_name": "mid",
                "bus_capacity": 20,
                "comfort_capacity": 20,
                "load": 12,
                "nodes": [1, 2, 3, 4, 0],
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 10,
                },
            }
        ],
        "bus_count": 1,
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["Bus 1"],
        },
        "time_constraint": {"strict_satisfied": True},
    }

    repaired = planner_core._repair_failed_routes_with_spare_vehicles(
        FakePlanner(),
        result,
        points,
        [[0, 1, 1, 1, 1] for _ in points],
        planner_core.PlannerConfig(service_direction="To School"),
        [],
        "test",
        2,
    )

    assert repaired["bus_count"] == 2
    assert repaired["traffic_gate"]["status"] == "passed"
    assert sorted(route["load"] for route in repaired["routes"]) == [6, 6]
    assert {route["bus_type_name"] for route in repaired["routes"]} == {"mid", "small"}
    assert repaired["traffic_split_repair"]["accepted"] is True
    assert repaired["traffic_split_repair"]["final_failed_route_count"] == 0


def test_split_repair_does_not_exceed_vehicle_limit(monkeypatch):
    result = {
        "routes": [{"route_id": "Bus 1", "nodes": [1, 2, 0], "bus_type_name": "mid"}],
        "bus_count": 1,
        "traffic_gate": {"status": "failed", "failed_route_ids": ["Bus 1"]},
    }
    planner = FakePlanner()
    repaired = planner_core._repair_failed_routes_with_spare_vehicles(
        planner,
        result,
        [{"is_depot": True}, {"passenger_count": 3}, {"passenger_count": 3}],
        [[0, 1, 1], [1, 0, 1], [1, 1, 0]],
        planner_core.PlannerConfig(),
        [],
        "test",
        1,
    )

    assert repaired is result


def test_split_repair_ignores_frozen_failure_and_repairs_remainder(monkeypatch):
    def fake_gate(_planner, scenario, _points, _config, _records, _label):
        failed_ids = []
        for index, route in enumerate(scenario["routes"], start=1):
            route_id = str(route.get("route_id") or f"Bus {index}")
            passed = (
                route.get("exception_role") != "frozen_current"
                and len([node for node in route["nodes"] if node]) <= 2
            )
            route["final_route_traffic_gate"] = {
                "status": "passed" if passed else "failed",
                "verified_total_duration_s": 1200 if passed else 6000,
                "time_window_overrun_minutes": 0 if passed else 10,
            }
            if not passed:
                failed_ids.append(route_id)
        gate = {
            "status": "passed" if not failed_ids else "failed",
            "failed_route_count": len(failed_ids),
            "failed_route_ids": failed_ids,
            "max_time_window_overrun_minutes": 0 if not failed_ids else 10,
        }
        scenario["traffic_gate"] = gate
        return gate

    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)
    points = [
        {"is_depot": True, "passenger_count": 0},
        {"passenger_count": 2},
        {"passenger_count": 3},
        {"passenger_count": 3},
        {"passenger_count": 3},
        {"passenger_count": 3},
    ]
    result = {
        "routes": [
            {
                "route_id": "R12",
                "exception_role": "frozen_current",
                "bus_type_name": "mid",
                "bus_capacity": 20,
                "comfort_capacity": 20,
                "load": 2,
                "nodes": [1, 0],
            },
            {
                "route_id": "Opt Bus 1",
                "exception_role": "optimized_remainder",
                "bus_type_name": "mid",
                "bus_capacity": 20,
                "comfort_capacity": 20,
                "load": 12,
                "nodes": [2, 3, 4, 5, 0],
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 10,
                },
            },
        ],
        "bus_count": 2,
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 2,
            "failed_route_ids": ["R12", "Opt Bus 1"],
        },
    }

    repaired = planner_core._repair_failed_routes_with_spare_vehicles(
        FakePlanner(),
        result,
        points,
        [[0, 1, 1, 1, 1, 1] for _ in points],
        planner_core.PlannerConfig(service_direction="To School"),
        [],
        "protected test",
        3,
        ignored_failed_route_ids={"R12"},
        available_bus_type_configs=[
            {"name": "mid", "capacity": 20, "max_count": 2},
            {"name": "small", "capacity": 10, "max_count": 1},
        ],
    )

    assert repaired["bus_count"] == 3
    assert repaired["traffic_gate"]["failed_route_ids"] == ["R12"]
    assert repaired["traffic_split_repair"]["accepted"] is True
    assert repaired["traffic_split_repair"]["final_actionable_failed_route_count"] == 0
