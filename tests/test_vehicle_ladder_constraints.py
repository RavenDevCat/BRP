import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import planner_core  # noqa: E402
import BusingProblem as legacy_planner  # noqa: E402


class VehicleLadderConstraintTests(unittest.TestCase):
    def test_solver_route_duration_dimension_uses_the_hard_user_limit(self) -> None:
        captured: dict[str, int] = {}

        class CapturedDimension(Exception):
            pass

        class FakeRouting:
            def RegisterTransitCallback(self, callback):
                del callback
                return 1

            def SetArcCostEvaluatorOfAllVehicles(self, callback_index):
                del callback_index

            def AddDimension(self, callback_index, slack, capacity, start_at_zero, name):
                del callback_index, slack, start_at_zero, name
                captured["capacity"] = capacity
                raise CapturedDimension

        points = [
            {"address": "school", "passenger_count": 0},
            {"address": "stop", "passenger_count": 1},
        ]
        matrix = [[0, 80], [0, 0]]
        fleet = [{"name": "bus", "capacity": 10}]

        with (
            mock.patch.object(legacy_planner, "MAX_ROUTE_DURATION_SECONDS", 60),
            mock.patch.object(legacy_planner.pywrapcp, "RoutingIndexManager", return_value=object()),
            mock.patch.object(legacy_planner.pywrapcp, "RoutingModel", return_value=FakeRouting()),
            self.assertRaises(CapturedDimension),
        ):
            legacy_planner.solve_routes_for_fleet(points, matrix, matrix, fleet)

        self.assertEqual(captured["capacity"], 60)

    def test_vehicle_ladder_reports_proven_infeasibility_before_solving(self) -> None:
        class FakePlanner:
            _BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(route_stop_limit=10)

        points = [{"is_depot": True, "passenger_count": 0}] + [
            {"passenger_count": 1} for _ in range(21)
        ]

        result = planner_core._solve_vehicle_ladder_scenario(
            FakePlanner(),
            points,
            "Strict Plan",
            current_route_count=5,
            minimum_vehicle_reduction=4,
            bus_type_configs=[{"name": "bus", "capacity": 30, "max_count": 5}],
        )

        self.assertFalse(result["enabled"])
        self.assertEqual(result["constraint_search_outcome"]["status"], "provably_infeasible")
        self.assertEqual(result["constraint_search_outcome"]["allowed_max_vehicle_count"], 1)
        self.assertEqual(result["constraint_search_outcome"]["theoretical_min_vehicle_count"], 3)
        self.assertEqual(result["vehicle_ladder_search"]["attempts"], [])

    def test_feasibility_report_covers_all_five_user_hard_constraints(self) -> None:
        scenario = {
            "bus_count": 3,
            "routes": [
                {"route_id": "A", "load": 11, "bus_capacity": 10, "nodes": [1, 0]},
                {"route_id": "B", "load": 9, "bus_capacity": 10, "nodes": [2, 0]},
                {"route_id": "C", "load": 2, "bus_capacity": 10, "nodes": [3, 4, 5, 0]},
            ],
            "time_constraint": {
                "enabled": True,
                "mode": "hard",
                "strict_satisfied": False,
                "bounded_solver_stop_count": 4,
                "expected_solver_stop_count": 5,
            },
        }
        gate = {
            "status": "failed",
            "gate_type": "arrival_window",
            "failed_route_count": 1,
            "failed_route_ids": ["C"],
        }
        config = planner_core.PlannerConfig(
            comfort_load_factor=0.85,
            route_stop_limit=2,
            time_impact_limit_minutes=15,
        )

        scenario["feasibility_report"] = planner_core.build_route_feasibility_report(
            scenario,
            gate,
            config,
            max_vehicle_count=3,
        )
        planner_core._apply_vehicle_saving_target(
            scenario,
            current_route_count=5,
            minimum_vehicle_reduction=3,
        )

        self.assertEqual(
            set(scenario["feasibility_report"]["failure_reasons"]),
            {
                "physical_capacity",
                "comfort_capacity",
                "stop_limit",
                "time_impact",
                "arrival_window",
                "vehicle_savings_target",
                "fleet_limit",
            },
        )

    def test_saving_target_failure_overrides_passed_traffic_gate(self) -> None:
        result = {
            "bus_count": 21,
            "traffic_gate": {"status": "passed"},
            "feasibility_report": {"status": "passed", "failure_reasons": [], "hard_constraints": {}},
        }

        updated = planner_core._apply_vehicle_saving_target(
            result,
            current_route_count=22,
            minimum_vehicle_reduction=2,
        )

        self.assertEqual(updated["vehicle_saving_target"]["status"], "failed")
        self.assertEqual(updated["feasibility_report"]["status"], "failed")
        self.assertIn("vehicle_savings_target", updated["feasibility_report"]["failure_reasons"])

    def test_saving_target_passes_when_required_saving_is_met(self) -> None:
        result = {
            "bus_count": 19,
            "traffic_gate": {"status": "passed"},
            "feasibility_report": {"status": "passed", "failure_reasons": [], "hard_constraints": {}},
        }

        updated = planner_core._apply_vehicle_saving_target(
            result,
            current_route_count=22,
            minimum_vehicle_reduction=2,
        )

        self.assertEqual(updated["vehicle_saving_target"]["status"], "passed")
        self.assertEqual(updated["feasibility_report"]["status"], "passed")

    def test_saving_target_is_not_applicable_without_routes(self) -> None:
        result = {
            "enabled": False,
            "bus_count": 0,
            "traffic_gate": {},
        }

        updated = planner_core._apply_vehicle_saving_target(
            result,
            current_route_count=22,
            minimum_vehicle_reduction=0,
        )

        self.assertEqual(updated["vehicle_saving_target"]["status"], "not_applicable")
        self.assertEqual(updated["vehicle_saving_target"]["saved_route_count"], 0)

    def test_vehicle_ladder_returns_deepest_feasible_target(self) -> None:
        calls: list[int] = []
        original_compute = planner_core._compute_scenario_without_render
        original_minimum = planner_core._minimum_vehicle_count_for_hard_constraints
        try:
            def fake_compute(*args, **kwargs):
                self.assertFalse(kwargs["enable_vehicle_search"])
                target = int(kwargs["reduced_vehicle_limit"])
                self.assertNotIn("forced_vehicle_count", kwargs)
                calls.append(target)
                passed = target >= 19
                return {
                    "bus_count": target,
                    "routes": [{} for _ in range(target)],
                    "traffic_gate": {"status": "passed" if passed else "failed", "failed_route_count": 1 if not passed else 0},
                    "feasibility_report": {
                        "status": "passed" if passed else "failed",
                        "failure_reasons": [] if passed else ["arrival_window"],
                        "hard_constraints": {},
                    },
                }

            planner_core._compute_scenario_without_render = fake_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = lambda *_args: 18
            result = planner_core._solve_vehicle_ladder_scenario(
                object(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=22,
                minimum_vehicle_reduction=2,
            )
        finally:
            planner_core._compute_scenario_without_render = original_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = original_minimum

        self.assertEqual(calls, [20, 19, 18])
        self.assertEqual(result["bus_count"], 19)
        self.assertEqual(result["vehicle_saving_target"]["status"], "passed")
        self.assertEqual(len(result["vehicle_ladder_search"]["attempts"]), 3)

    def test_vehicle_ladder_continues_after_first_failed_exact_count(self) -> None:
        calls: list[int] = []
        original_compute = planner_core._compute_scenario_without_render
        original_minimum = planner_core._minimum_vehicle_count_for_hard_constraints
        try:
            def fake_compute(*args, **kwargs):
                self.assertFalse(kwargs["enable_vehicle_search"])
                target = int(kwargs["reduced_vehicle_limit"])
                self.assertNotIn("forced_vehicle_count", kwargs)
                calls.append(target)
                if target == 20:
                    return {
                        "bus_count": 20,
                        "routes": [{} for _ in range(20)],
                        "traffic_gate": {"status": "failed"},
                        "feasibility_report": {"status": "failed", "failure_reasons": ["arrival_window"]},
                    }
                return {
                    "bus_count": 19,
                    "routes": [{} for _ in range(19)],
                    "traffic_gate": {"status": "passed"},
                    "feasibility_report": {"status": "passed", "failure_reasons": []},
                }

            planner_core._compute_scenario_without_render = fake_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = lambda *_args: 19
            result = planner_core._solve_vehicle_ladder_scenario(
                object(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=22,
                minimum_vehicle_reduction=2,
            )
        finally:
            planner_core._compute_scenario_without_render = original_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = original_minimum

        self.assertEqual(calls, [20, 19])
        self.assertEqual(result["bus_count"], 19)
        self.assertEqual(result["feasibility_report"]["status"], "passed")
        self.assertEqual(result["constraint_search_outcome"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
