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


def test_exception_subset_time_constraints_keep_original_node_ids():
    points = [
        {"node_id": 0, "is_depot": True},
        {"node_id": 1},
        {"node_id": 2},
        {"node_id": 3},
    ]
    assessment_time = [
        [0, 100, 0, 0],
        [0, 0, 100, 0],
        [0, 0, 0, 100],
        [0, 0, 0, 0],
    ]
    lower_builder, upper_builder, metadata = planner_core._build_time_acceptance_constraint_builder(
        {
            "route_count": 1,
            "route_summaries": [{"matched_node_ids": [0, 1, 2, 3]}],
        },
        points,
        assessment_time,
        "From School",
        1,
    )
    subset_points, _mapping = planner_core._build_exception_subset_points(points, {1})

    assert lower_builder is None
    assert upper_builder(subset_points) == {1: 260, 2: 360}
    assert metadata["impact_direction"] == "adverse_only"


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
        planner_core.PlannerConfig(service_direction="To School", minimum_vehicle_reduction=1),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 2}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "passed"}}],
    )

    assert result["exception_preserving"]["accepted"] is True
    assert result["exception_preserving"]["frozen_route_ids"] == ["R12"]
    assert captured["reduced_vehicle_limit"] == 1
    assert captured["bus_type_configs"][0]["max_count"] == 1
    assert [point["address"] for point in captured["subset_points"]] == ["school", "remaining a", "remaining b"]
    assert result["routes"][0]["route_id"] == "R12"
    assert result["routes"][1]["route_id"] == "Opt Bus 1"
    assert result["routes"][1]["nodes"] == [2, 3, 0]


def test_exception_preserving_runs_independently_without_current_failures(monkeypatch):
    points = [
        {"node_id": 0, "is_depot": True},
        {"node_id": 1, "address": "stop"},
    ]
    current = {
        "enabled": True,
        "bus_count": 1,
        "routes": [
            {
                "route_id": "KR1",
                "nodes": [1, 0],
                "final_route_traffic_gate": {"status": "passed"},
            }
        ],
        "traffic_gate": {"status": "passed", "failed_route_count": 0, "failed_route_ids": []},
    }

    def fake_compute(_planner, subset_points, *_args, **_kwargs):
        return {"points": subset_points, "routes": [{"route_id": "Bus 1", "nodes": [1, 0]}]}

    def fake_gate(_planner, scenario, *_args):
        scenario["traffic_gate"] = {"status": "passed", "failed_route_count": 0, "failed_route_ids": []}
        for route in scenario["routes"]:
            route["final_route_traffic_gate"] = {"status": "passed"}
        return scenario["traffic_gate"]

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)
    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)
    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(minimum_vehicle_reduction=0),
        [{"country": "South Korea", "city": "Seoul"}],
        [{"name": "bus", "capacity": 30, "max_count": 1}],
        None,
        standard_scenarios=[{"traffic_gate": {"status": "passed"}}],
    )

    assert result.get("enabled") is not False
    assert result["traffic_gate"]["status"] == "passed"
    assert result["exception_preserving"]["frozen_route_count"] == 0


def test_protected_plan_freezes_current_comfort_and_stop_limit_violations(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "comfort violation"},
        {"node_id": 2, "address": "stop limit a"},
        {"node_id": 3, "address": "stop limit b"},
        {"node_id": 4, "address": "stop limit c"},
        {"node_id": 5, "address": "remaining"},
    ]
    current = {
        "enabled": True,
        "bus_count": 3,
        "routes": [
            {
                "route_id": "R-comfort",
                "bus_type_name": "bus",
                "bus_capacity": 10,
                "load": 9,
                "nodes": [1, 0],
                "final_route_traffic_gate": {"status": "passed"},
            },
            {
                "route_id": "R-stops",
                "bus_type_name": "bus",
                "bus_capacity": 10,
                "load": 3,
                "nodes": [2, 3, 4, 0],
                "final_route_traffic_gate": {"status": "passed"},
            },
            {
                "route_id": "R-good",
                "bus_type_name": "bus",
                "bus_capacity": 10,
                "load": 1,
                "nodes": [5, 0],
                "final_route_traffic_gate": {"status": "passed"},
            },
        ],
        "traffic_gate": {"status": "passed", "failed_route_count": 0, "failed_route_ids": []},
    }

    def fake_compute(_planner, subset_points, *_args, **_kwargs):
        return {
            "points": subset_points,
            "routes": [
                {
                    "route_id": "Bus 1",
                    "bus_type_name": "bus",
                    "bus_capacity": 10,
                    "comfort_capacity": 8,
                    "load": 1,
                    "nodes": [1, 0],
                    "final_route_traffic_gate": {"status": "passed"},
                }
            ],
        }

    def fake_gate(_planner, scenario, *_args):
        scenario["traffic_gate"] = {"status": "passed", "failed_route_count": 0, "failed_route_ids": []}
        for route in scenario["routes"]:
            route["final_route_traffic_gate"] = {"status": "passed", "verified_total_duration_s": 60}
        return scenario["traffic_gate"]

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)
    monkeypatch.setattr(planner_core, "attach_final_route_traffic_gate", fake_gate)

    result = planner_core.build_exception_preserving_scenario(
        FakePlanner(),
        points,
        current,
        planner_core.PlannerConfig(
            comfort_load_factor=0.85,
            route_stop_limit=2,
            minimum_vehicle_reduction=0,
        ),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "bus", "capacity": 10, "max_count": 3}],
        3,
        standard_scenarios=[],
    )

    assert result["exception_preserving"]["accepted"] is True
    assert result["exception_preserving"]["frozen_route_ids"] == ["R-comfort", "R-stops"]
    assert result["exception_preserving"]["candidate_remainder_failure_summary"]["failed_route_count"] == 0


def test_protected_plan_passes_time_constraints_into_remainder(monkeypatch):
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
                "time_constraint": {
                    "enabled": True,
                    "mode": "hard",
                    "strict_satisfied": True,
                    "bounded_solver_stop_count": len(bounds),
                    "expected_solver_stop_count": len(bounds),
                },
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
        planner_core.PlannerConfig(minimum_vehicle_reduction=1),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 2}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
        node_time_upper_bounds_builder=lambda solver_points: {1: 900} if len(solver_points) > 1 else {},
        time_constraint_metadata={"enabled": True, "source": "exception_preserving_remainder"},
        baseline_name="exception_preserving_optimization",
        scenario_label="Protected Plan",
    )

    assert result["baseline_name"] == "exception_preserving_optimization"
    assert result["exception_preserving"]["accepted"] is True
    assert captured["bounds"] == {1: 900}
    assert captured["time_constraint_metadata"]["source"] == "exception_preserving_remainder"
    assert result["time_constraint"]["applies_to"] == "exception_preserving_remainder"
    assert result["time_constraint"]["bounded_solver_stop_count"] == 1


def test_protected_plan_does_not_relax_vehicle_saving_limit(monkeypatch):
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
            "time_constraint": {
                "enabled": True,
                "strict_satisfied": True,
                "bounded_solver_stop_count": 2,
            },
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
        planner_core.PlannerConfig(minimum_vehicle_reduction=1),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 3}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
        node_time_upper_bounds_builder=lambda solver_points: {1: 900, 2: 900},
        time_constraint_metadata={"enabled": True, "source": "exception_preserving_remainder"},
        baseline_name="exception_preserving_optimization",
        scenario_label="Protected Plan",
    )

    assert captured["limits"] == [1]
    assert captured["subset_addresses"] == ["school", "remaining a", "remaining b"]
    assert result["enabled"] is False
    assert result["exception_preserving"]["accepted"] is False


def test_exception_preserving_acceptance_requires_minimum_vehicle_saving(monkeypatch):
    points = [
        {"node_id": 0, "address": "school", "is_depot": True},
        {"node_id": 1, "address": "frozen failed stop"},
        {"node_id": 2, "address": "remaining a"},
        {"node_id": 3, "address": "remaining b"},
    ]
    current = {
        "enabled": True,
        "bus_count": 4,
        "routes": [
            {
                "route_id": "R12",
                "nodes": [1, 0],
                "final_route_traffic_gate": {"status": "failed", "time_window_overrun_minutes": 12},
            },
            {"route_id": "R1", "nodes": [2, 0], "final_route_traffic_gate": {"status": "passed"}},
            {"route_id": "R2", "nodes": [3, 0], "final_route_traffic_gate": {"status": "passed"}},
            {"route_id": "R3", "nodes": [0], "final_route_traffic_gate": {"status": "passed"}},
        ],
        "traffic_gate": {
            "status": "failed",
            "failed_route_count": 1,
            "failed_route_ids": ["R12"],
            "max_time_window_overrun_minutes": 12,
        },
    }

    def fake_compute(_planner, subset_points, *_args, **_kwargs):
        return {
            "points": subset_points,
            "routes": [
                {"route_id": "Bus 1", "nodes": [1, 0], "final_route_traffic_gate": {"status": "passed"}},
                {"route_id": "Bus 2", "nodes": [2, 0], "final_route_traffic_gate": {"status": "passed"}},
            ],
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
        planner_core.PlannerConfig(minimum_vehicle_reduction=2),
        [{"address": "Shanghai", "passenger_count": 1}],
        [{"name": "30-fbus", "capacity": 30, "max_count": 4}],
        2,
        standard_scenarios=[{"traffic_gate": {"status": "failed"}}],
    )

    assert result["bus_count"] == 3
    assert result["exception_preserving"]["accepted"] is False
    assert result["vehicle_saving_target"]["status"] == "failed"


def test_protected_skip_reason_names_unfrozen_remainder_limit(monkeypatch):
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
        baseline_name="exception_preserving_optimization",
        scenario_label="Protected Plan",
    )

    assert result["enabled"] is False
    assert "unfrozen remainder" in result["skipped_reason"]
    assert "1" in result["skipped_reason"]
    assert result["constraint_search_outcome"]["status"] == "provably_infeasible"
