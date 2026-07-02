import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
planner_core = importlib.import_module("planner_core")


class FakePlanner:
    def __init__(self):
        self.logged = []

    def log(self, message):
        self.logged.append(message)

    def build_scenario_result(self, points, routes, output_html):
        return {
            "points": points,
            "routes": routes,
            "output_html": output_html,
            "bus_count": len(routes),
            "service_stop_count": max(0, len(points) - 1),
            "bus_mix": {},
        }


def test_exception_preserving_freezes_current_failure_and_remaps_remainder(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "failed current stop"},
        {"node_id": 2, "address": "remaining a"},
        {"node_id": 3, "address": "remaining b"},
    ]
    current = {
        "enabled": True,
        "bus_count": 3,
        "routes": [
            {
                "route_id": "R12",
                "bus_type_name": "30-fbus",
                "bus_capacity": 30,
                "nodes": [1, 0],
                "load": 15,
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 14.6,
                },
            },
            {
                "route_id": "R1",
                "bus_type_name": "30-fbus",
                "bus_capacity": 30,
                "nodes": [2, 3, 0],
                "load": 20,
                "final_route_traffic_gate": {"status": "passed"},
            },
        ],
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 14.6,
        },
    }
    captured = {}

    def fake_compute(_planner, subset_points, *_args, bus_type_configs=None, reduced_vehicle_limit=None, **_kwargs):
        captured["subset_points"] = subset_points
        captured["bus_type_configs"] = bus_type_configs
        captured["reduced_vehicle_limit"] = reduced_vehicle_limit
        return {
            "points": subset_points,
            "routes": [
                {
                    "route_id": "Bus 1",
                    "bus_type_name": "30-fbus",
                    "bus_capacity": 30,
                    "nodes": [1, 2, 0],
                    "load": 20,
                    "final_route_traffic_gate": {"status": "passed"},
                }
            ],
        }

    def fake_gate(_planner, scenario, *_args):
        scenario["traffic_gate"] = {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 14.6,
        }
        for route in scenario["routes"]:
            if route["route_id"] == "R12":
                route["final_route_traffic_gate"] = {
                    "status": "failed",
                    "time_window_overrun_minutes": 14.6,
                }
            else:
                route["final_route_traffic_gate"] = {"status": "passed"}
        return scenario["traffic_gate"]

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)
    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)

    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(service_direction="To School"),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 2}],
        2,
        standard_scenarios=[
            {"traffic_gate": {"status": "failed"}},
            {"enabled": False},
        ],
    )

    assert result["exception_preserving"]["accepted"] is True
    assert result["exception_preserving"]["frozen_route_ids"] == ["R12"]
    assert captured["reduced_vehicle_limit"] == 1
    assert captured["bus_type_configs"][0]["max_count"] == 1
    assert [point["address"] for point in captured["subset_points"]] == ["school", "remaining a", "remaining b"]
    assert result["routes"][0]["route_id"] == "R12"
    assert result["routes"][1]["route_id"] == "Opt Bus 1"
    assert result["routes"][1]["nodes"] == [2, 3, 0]


def test_exception_preserving_skips_when_standard_scenario_passed():
    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        [{"node_id": 0, "is_depot": True}],
        {"enabled": True, "routes": []},
        planner_core.PlannerConfig(),
        [],
        [],
        None,
        standard_scenarios=[{"traffic_gate": {"status": "passed"}}],
    )

    assert result["enabled"] is False
    assert "standard scenario" in result["skipped_reason"]


def test_ep15min_passes_time_constraints_into_exception_remainder(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "failed current stop"},
        {"node_id": 2, "address": "remaining stop"},
    ]
    current = {
        "enabled": True,
        "bus_count": 3,
        "routes": [
            {
                "route_id": "R12",
                "nodes": [1, 0],
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 12,
                },
            },
            {"route_id": "R1", "nodes": [2, 0], "final_route_traffic_gate": {"status": "passed"}},
        ],
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 12,
        },
    }
    captured = {}

    def fake_compute(_planner, subset_points, *_args, node_time_upper_bounds_builder=None, time_constraint_metadata=None, **_kwargs):
        bounds = node_time_upper_bounds_builder(subset_points)
        captured["bounds"] = bounds
        captured["time_constraint_metadata"] = time_constraint_metadata
        return {
            "points": subset_points,
            "routes": [{"route_id": "Bus 1", "nodes": [1, 0], "final_route_traffic_gate": {"status": "passed"}}],
            "time_constraint": {"enabled": True, "bounded_solver_stop_count": len(bounds)},
        }

    def fake_gate(_planner, scenario, *_args):
        scenario["traffic_gate"] = {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 12,
        }
        for route in scenario["routes"]:
            route["final_route_traffic_gate"] = (
                {"status": "failed", "time_window_overrun_minutes": 12}
                if route["route_id"] == "R12"
                else {"status": "passed"}
            )
        return scenario["traffic_gate"]

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)
    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)

    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 2}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
        node_time_upper_bounds_builder=lambda solver_points: {1: 900} if len(solver_points) > 1 else {},
        time_constraint_metadata={"enabled": True, "source": "exception_preserving_remainder"},
        baseline_name="ep15min_optimization",
        scenario_label="EP 15-minute optimization",
    )

    assert result["baseline_name"] == "ep15min_optimization"
    assert result["exception_preserving"]["accepted"] is True
    assert captured["bounds"] == {1: 900}
    assert captured["time_constraint_metadata"]["source"] == "exception_preserving_remainder"
    assert result["time_constraint"]["applies_to"] == "exception_preserving_remainder"
    assert result["time_constraint"]["bounded_solver_stop_count"] == 1


def test_ep15min_relaxes_remainder_vehicle_limit_after_saving_attempt_fails(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "frozen failed stop"},
        {"node_id": 2, "address": "remaining a"},
        {"node_id": 3, "address": "remaining b"},
    ]
    current = {
        "enabled": True,
        "bus_count": 3,
        "routes": [
            {
                "route_id": "R12",
                "nodes": [1, 0],
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 12,
                },
            },
            {"route_id": "R1", "nodes": [2, 0], "final_route_traffic_gate": {"status": "passed"}},
            {"route_id": "R2", "nodes": [3, 0], "final_route_traffic_gate": {"status": "passed"}},
        ],
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 12,
        },
    }
    captured = {"limits": []}

    def fake_compute(_planner, subset_points, *_args, reduced_vehicle_limit=None, **_kwargs):
        captured["limits"].append(reduced_vehicle_limit)
        captured["subset_addresses"] = [point["address"] for point in subset_points]
        if reduced_vehicle_limit == 1:
            raise RuntimeError("no one-vehicle EP15 remainder")
        return {
            "points": subset_points,
            "routes": [{"route_id": "Bus 1", "nodes": [1, 2, 0], "final_route_traffic_gate": {"status": "passed"}}],
            "time_constraint": {"enabled": True, "bounded_solver_stop_count": 2},
        }

    def fake_gate(_planner, scenario, *_args):
        scenario["traffic_gate"] = {
            "status": "passed",
            "failed_route_count": 0,
            "failed_route_ids": [],
            "max_time_window_overrun_minutes": 0,
        }
        for route in scenario["routes"]:
            route["final_route_traffic_gate"] = {"status": "passed"}
        return scenario["traffic_gate"]

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)
    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)

    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 3}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
        node_time_upper_bounds_builder=lambda solver_points: {1: 900, 2: 900},
        time_constraint_metadata={"enabled": True, "source": "exception_preserving_remainder"},
        baseline_name="ep15min_optimization",
        scenario_label="EP 15-minute optimization",
        allow_vehicle_limit_fallback=True,
    )

    assert captured["limits"] == [1, 2]
    assert captured["subset_addresses"] == ["school", "remaining a", "remaining b"]
    assert result["exception_preserving"]["accepted"] is True
    assert result["exception_preserving"]["vehicle_limit_relaxed"] is True
    assert result["exception_preserving"]["remaining_vehicle_limit"] == 2


def test_ep15min_skip_reason_names_unfrozen_remainder_limits(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "frozen failed stop"},
        {"node_id": 2, "address": "remaining stop"},
    ]
    current = {
        "enabled": True,
        "bus_count": 3,
        "routes": [
            {
                "route_id": "R12",
                "nodes": [1, 0],
                "final_route_traffic_gate": {
                    "status": "failed",
                    "time_window_overrun_minutes": 12,
                },
            },
            {"route_id": "R1", "nodes": [2, 0], "final_route_traffic_gate": {"status": "passed"}},
        ],
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 12,
        },
    }

    def fake_compute(*_args, **_kwargs):
        raise RuntimeError("remainder infeasible")

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)

    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(),
        [],
        [{"name": "30-fbus", "capacity": 30, "max_count": 3}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
        node_time_upper_bounds_builder=lambda solver_points: {1: 900},
        time_constraint_metadata={"enabled": True, "source": "exception_preserving_remainder"},
        baseline_name="ep15min_optimization",
        scenario_label="EP 15-minute optimization",
        allow_vehicle_limit_fallback=True,
    )

    assert result["enabled"] is False
    assert "unfrozen remainder" in result["skipped_reason"]
    assert "1, 2" in result["skipped_reason"]
