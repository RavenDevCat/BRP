import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import planner_core  # noqa: E402


class VehicleLadderConstraintTests(unittest.TestCase):
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
        try:
            def fake_compute(*args, **kwargs):
                self.assertNotIn("forced_vehicle_count", kwargs)
                self.assertFalse(kwargs["enable_vehicle_search"])
                target = int(kwargs["reduced_vehicle_limit"])
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
            result = planner_core._solve_vehicle_ladder_scenario(
                object(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=22,
                minimum_vehicle_reduction=2,
            )
        finally:
            planner_core._compute_scenario_without_render = original_compute

        self.assertEqual(calls, [20, 19, 18])
        self.assertEqual(result["bus_count"], 19)
        self.assertEqual(result["vehicle_saving_target"]["status"], "passed")
        self.assertEqual(len(result["vehicle_ladder_search"]["attempts"]), 3)

    def test_vehicle_ladder_stops_after_first_passing_fallback(self) -> None:
        calls: list[int] = []
        original_compute = planner_core._compute_scenario_without_render
        try:
            def fake_compute(*args, **kwargs):
                self.assertFalse(kwargs["enable_vehicle_search"])
                target = int(kwargs["reduced_vehicle_limit"])
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
            result = planner_core._solve_vehicle_ladder_scenario(
                object(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=22,
                minimum_vehicle_reduction=2,
            )
        finally:
            planner_core._compute_scenario_without_render = original_compute

        self.assertEqual(calls, [20, 21])
        self.assertEqual(result["bus_count"], 19)


if __name__ == "__main__":
    unittest.main()
