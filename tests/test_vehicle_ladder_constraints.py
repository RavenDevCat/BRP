import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import planner_core  # noqa: E402
import BusingProblem as legacy_planner  # noqa: E402


class FakeRoutingSolve:
    def __init__(self, results, statuses):
        self.results = list(results)
        self.statuses = list(statuses)
        self.calls = 0

    def _solve(self):
        result = self.results[self.calls]
        self.calls += 1
        return result

    def SolveWithParameters(self, _search):
        return self._solve()

    def SolveFromAssignmentWithParameters(self, _seed, _search):
        return self._solve()

    def status(self):
        return self.statuses[self.calls - 1]


class VehicleLadderConstraintTests(unittest.TestCase):
    def test_timeout_retries_same_model_once_and_can_recover(self) -> None:
        routing = FakeRoutingSolve([None, object()], [4, 1])
        search = mock.Mock()
        search.time_limit.seconds = 10
        retry_search = mock.Mock()
        retry_search.time_limit.seconds = 20

        with mock.patch.object(
            legacy_planner,
            "build_guided_local_search_parameters",
            return_value=retry_search,
        ) as build_retry:
            solution, attempts = legacy_planner._solve_routing_model_with_retry(
                routing,
                search,
                None,
                first_solution_only=False,
                retry_unresolved=True,
            )

        self.assertIsNotNone(solution)
        self.assertEqual(routing.calls, 2)
        self.assertEqual([attempt["status_name"] for attempt in attempts], ["ROUTING_FAIL_TIMEOUT", "ROUTING_SUCCESS"])
        self.assertEqual(build_retry.call_args.kwargs["time_limit_seconds"], 20)

    def test_non_exact_timeout_is_reported_without_retry(self) -> None:
        routing = FakeRoutingSolve([None], [4])
        search = mock.Mock()
        search.time_limit.seconds = 10

        with mock.patch.object(legacy_planner, "build_guided_local_search_parameters") as build_retry:
            solution, attempts = legacy_planner._solve_routing_model_with_retry(
                routing,
                search,
                None,
                first_solution_only=False,
                retry_unresolved=False,
            )

        self.assertIsNone(solution)
        self.assertEqual(routing.calls, 1)
        self.assertEqual(attempts[0]["status_name"], "ROUTING_FAIL_TIMEOUT")
        build_retry.assert_not_called()

    def test_proven_infeasible_does_not_retry(self) -> None:
        routing = FakeRoutingSolve([None], [6])
        search = mock.Mock()
        search.time_limit.seconds = 10

        with mock.patch.object(legacy_planner, "build_guided_local_search_parameters") as build_retry:
            solution, attempts = legacy_planner._solve_routing_model_with_retry(
                routing,
                search,
                None,
                first_solution_only=False,
                retry_unresolved=True,
            )

        self.assertIsNone(solution)
        build_retry.assert_not_called()
        with self.assertRaises(legacy_planner.NoFeasibleRouteError) as raised:
            legacy_planner._raise_solver_outcome("failed", routing, attempts)
        self.assertEqual(raised.exception.status_name, "ROUTING_INFEASIBLE")
        self.assertEqual(len(raised.exception.attempts), 1)

    def test_repeated_timeout_is_unresolved_and_invalid_model_is_not_retried(self) -> None:
        search = mock.Mock()
        search.time_limit.seconds = 10
        retry_search = mock.Mock()
        retry_search.time_limit.seconds = 20
        timed_out = FakeRoutingSolve([None, None], [4, 4])

        with mock.patch.object(
            legacy_planner,
            "build_guided_local_search_parameters",
            return_value=retry_search,
        ):
            solution, attempts = legacy_planner._solve_routing_model_with_retry(
                timed_out,
                search,
                None,
                first_solution_only=False,
                retry_unresolved=True,
            )

        self.assertIsNone(solution)
        with self.assertRaises(legacy_planner.SolverUnresolvedError) as raised:
            legacy_planner._raise_solver_outcome("failed", timed_out, attempts)
        self.assertEqual(len(raised.exception.attempts), 2)

        invalid = FakeRoutingSolve([None], [5])
        with mock.patch.object(legacy_planner, "build_guided_local_search_parameters") as build_retry:
            solution, attempts = legacy_planner._solve_routing_model_with_retry(
                invalid,
                search,
                None,
                first_solution_only=False,
                retry_unresolved=True,
            )
        self.assertIsNone(solution)
        build_retry.assert_not_called()
        with self.assertRaises(legacy_planner.InvalidSolverModelError):
            legacy_planner._raise_solver_outcome("failed", invalid, attempts)

    def test_invalid_express_model_is_not_hidden_by_regular_pool_fallback(self) -> None:
        points = [
            {"address": "school", "passenger_count": 0},
            {"address": "remote", "passenger_count": 1},
            {"address": "inner", "passenger_count": 1},
        ]
        matrix = [[0, 1, 1], [1, 0, 1], [1, 1, 0]]
        fleet = [
            {"name": "small", "capacity": 10},
            {"name": "large", "capacity": 20},
        ]

        with (
            mock.patch.object(legacy_planner, "build_vehicle_fleet", return_value=fleet),
            mock.patch.object(legacy_planner, "compute_depot_distances", return_value={1: 20.0, 2: 1.0}),
            mock.patch.object(legacy_planner, "NODE_TIME_LOWER_BOUNDS", {}),
            mock.patch.object(legacy_planner, "NODE_TIME_UPPER_BOUNDS", {}),
            mock.patch.object(legacy_planner, "NODE_TIME_SOFT_UPPER_BOUNDS", {}),
            mock.patch.object(
                legacy_planner,
                "solve_routes_for_fleet",
                side_effect=legacy_planner.InvalidSolverModelError("invalid express model"),
            ) as solve,
            self.assertRaises(legacy_planner.InvalidSolverModelError),
        ):
            legacy_planner.solve_routes(points, matrix, matrix)

        solve.assert_called_once()

    def test_retired_constrained_improvement_path_is_removed(self) -> None:
        for name in (
            "_select_constrained_improvement_moves",
            "_annotate_constrained_move_packages",
            "_summarize_constrained_move_packages",
            "_enrich_constrained_package_summaries",
            "build_constrained_improvement_current_plan",
        ):
            self.assertFalse(hasattr(planner_core, name), name)

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

    def test_hard_time_bounds_preserve_the_outer_vehicle_cap(self) -> None:
        fleet = [
            {"name": "bus", "capacity": 10, "vehicle_id": index}
            for index in range(10)
        ]
        captured: dict[str, int] = {}
        points = [
            {"address": "school", "passenger_count": 0},
            {"address": "stop-a", "passenger_count": 1},
            {"address": "stop-b", "passenger_count": 1},
        ]
        matrix = [
            [0, 60, 60],
            [60, 0, 60],
            [60, 60, 0],
        ]

        def fake_solve_routes_for_fleet(
            _points,
            _time_matrix,
            _distance_matrix,
            active_fleet,
            *_bounds,
        ):
            captured["fleet_size"] = len(active_fleet)
            return []

        with (
            mock.patch.object(legacy_planner, "build_vehicle_fleet", return_value=fleet),
            mock.patch.object(
                legacy_planner,
                "compute_depot_distances",
                return_value={1: 0.0, 2: 0.0},
            ),
            mock.patch.object(
                legacy_planner,
                "solve_routes_for_fleet",
                side_effect=fake_solve_routes_for_fleet,
            ),
            mock.patch.object(legacy_planner, "NODE_TIME_LOWER_BOUNDS", {1: 0, 2: 0}),
            mock.patch.object(legacy_planner, "NODE_TIME_UPPER_BOUNDS", {1: 600, 2: 600}),
            mock.patch.object(legacy_planner, "NODE_TIME_SOFT_UPPER_BOUNDS", {}),
        ):
            legacy_planner.solve_routes(points, matrix, matrix)

        self.assertEqual(captured["fleet_size"], 10)

    def test_vehicle_ladder_confirms_lower_bound_with_an_exact_solve(self) -> None:
        class FakePlanner:
            _BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(route_stop_limit=10)
            NoFeasibleRouteError = legacy_planner.NoFeasibleRouteError

        points = [{"is_depot": True, "passenger_count": 0}] + [
            {"passenger_count": 1} for _ in range(21)
        ]

        with mock.patch.object(
            planner_core,
            "_compute_scenario_without_render",
            side_effect=legacy_planner.NoFeasibleRouteError("no exact one-vehicle solution"),
        ) as compute:
            result = planner_core._solve_vehicle_ladder_scenario(
                FakePlanner(),
                points,
                "Strict Plan",
                current_route_count=5,
                minimum_vehicle_reduction=4,
                bus_type_configs=[{"name": "bus", "capacity": 30, "max_count": 5}],
            )

        self.assertNotIn("enabled", result)
        self.assertEqual(result["scenario_status"], "infeasible")
        self.assertEqual(result["constraint_search_outcome"]["status"], "infeasible")
        self.assertEqual(result["constraint_search_outcome"]["allowed_max_vehicle_count"], 1)
        self.assertEqual(result["constraint_search_outcome"]["theoretical_min_vehicle_count"], 3)
        self.assertEqual(result["vehicle_ladder_search"]["attempts"][0]["status"], "infeasible")
        self.assertEqual(compute.call_args.kwargs["forced_vehicle_count"], 1)

    def test_vehicle_ladder_preserves_unresolved_targets_without_calling_them_infeasible(self) -> None:
        class FakePlanner:
            _BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(route_stop_limit=10)
            NoFeasibleRouteError = legacy_planner.NoFeasibleRouteError
            SolverUnresolvedError = legacy_planner.SolverUnresolvedError

        points = [{"is_depot": True, "passenger_count": 0}] + [
            {"passenger_count": 1} for _ in range(10)
        ]
        unresolved = legacy_planner.SolverUnresolvedError(
            "bounded search timed out",
            status_code=4,
            status_name="ROUTING_FAIL_TIMEOUT",
            attempts=[{"attempt": 1, "status_code": 4}],
        )

        with (
            mock.patch.object(planner_core, "_minimum_vehicle_count_for_hard_constraints", return_value=2),
            mock.patch.object(planner_core, "_compute_scenario_without_render", side_effect=unresolved),
        ):
            result = planner_core._solve_vehicle_ladder_scenario(
                FakePlanner(),
                points,
                "Strict Plan",
                current_route_count=3,
                minimum_vehicle_reduction=0,
                bus_type_configs=[{"name": "bus", "capacity": 30, "max_count": 3}],
            )

        self.assertEqual(result["scenario_status"], "unresolved")
        self.assertEqual(result["constraint_search_outcome"]["status"], "unresolved")
        self.assertFalse(result["constraint_search_outcome"]["search_complete"])
        self.assertEqual(result["constraint_search_outcome"]["unresolved_vehicle_caps"], [3, 2])
        self.assertTrue(all(item["status"] == "unresolved" for item in result["vehicle_ladder_search"]["attempts"]))

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
                target = int(kwargs["reduced_vehicle_limit"])
                self.assertEqual(kwargs["forced_vehicle_count"], target)
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

    def test_vehicle_ladder_does_not_claim_minimality_below_an_unresolved_target(self) -> None:
        class FakePlanner:
            SolverUnresolvedError = legacy_planner.SolverUnresolvedError

        def fake_compute(*_args, **kwargs):
            target = int(kwargs["reduced_vehicle_limit"])
            if target == 2:
                raise legacy_planner.SolverUnresolvedError(
                    "two-vehicle search timed out",
                    status_code=4,
                    status_name="ROUTING_FAIL_TIMEOUT",
                )
            return {
                "bus_count": target,
                "routes": [{} for _ in range(target)],
                "traffic_gate": {"status": "passed"},
                "feasibility_report": {"status": "passed", "failure_reasons": []},
            }

        with (
            mock.patch.object(planner_core, "_compute_scenario_without_render", side_effect=fake_compute),
            mock.patch.object(planner_core, "_minimum_vehicle_count_for_hard_constraints", return_value=2),
        ):
            result = planner_core._solve_vehicle_ladder_scenario(
                FakePlanner(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=3,
                minimum_vehicle_reduction=0,
            )

        self.assertEqual(result["scenario_status"], "passed")
        self.assertEqual(result["bus_count"], 3)
        self.assertFalse(result["constraint_search_outcome"]["search_complete"])
        self.assertEqual(result["constraint_search_outcome"]["unresolved_vehicle_caps"], [2])

    def test_vehicle_ladder_continues_after_first_failed_exact_target(self) -> None:
        calls: list[int] = []
        original_compute = planner_core._compute_scenario_without_render
        original_minimum = planner_core._minimum_vehicle_count_for_hard_constraints
        try:
            def fake_compute(*args, **kwargs):
                target = int(kwargs["reduced_vehicle_limit"])
                self.assertEqual(kwargs["forced_vehicle_count"], target)
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

    def test_vehicle_ladder_rejects_actual_count_below_exact_target(self) -> None:
        calls: list[int] = []
        original_compute = planner_core._compute_scenario_without_render
        original_minimum = planner_core._minimum_vehicle_count_for_hard_constraints
        try:
            def fake_compute(*args, **kwargs):
                target = int(kwargs["reduced_vehicle_limit"])
                calls.append(target)
                actual = 15
                return {
                    "bus_count": actual,
                    "routes": [{} for _ in range(actual)],
                    "traffic_gate": {"status": "passed"},
                    "feasibility_report": {"status": "passed", "failure_reasons": []},
                }

            planner_core._compute_scenario_without_render = fake_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = lambda *_args: 19
            with self.assertRaisesRegex(
                RuntimeError,
                "required exactly 20 vehicle.*returned 15",
            ):
                planner_core._solve_vehicle_ladder_scenario(
                    object(),
                    [{"is_depot": True}, {"is_depot": False}],
                    "test",
                    current_route_count=22,
                    minimum_vehicle_reduction=2,
                )
        finally:
            planner_core._compute_scenario_without_render = original_compute
            planner_core._minimum_vehicle_count_for_hard_constraints = original_minimum

        self.assertEqual(calls, [20])

    def test_vehicle_ladder_observes_contract_shadow_without_changing_result(self) -> None:
        points = [
            {
                "node_id": 0,
                "is_depot": True,
                "address": "School",
                "lat": 31.2,
                "lng": 121.4,
                "passenger_count": 0,
            },
            {
                "node_id": 1,
                "address": "Stop",
                "lat": 31.21,
                "lng": 121.41,
                "passenger_count": 4,
            },
        ]
        bus_configs = [{"name": "Bus", "capacity": 10, "max_count": 2}]

        class Planner:
            BUS_TYPE_CONFIGS = bus_configs
            _BRP_ACTIVE_CONFIG = planner_core.PlannerConfig(
                service_direction="To School",
                max_route_duration_minutes=60,
            )

        candidate = {
            "points": points,
            "bus_count": 1,
            "routes": [
                {
                    "route_id": "Opt Bus 1",
                    "nodes": [1, 0],
                    "load": 4,
                    "bus_capacity": 10,
                    "bus_type_name": "Bus",
                    "time_s": 600,
                    "distance_m": 4000,
                }
            ],
            "traffic_gate": {"status": "passed"},
            "feasibility_report": {
                "status": "passed",
                "failure_reasons": [],
                "hard_constraints": {},
            },
        }
        observed = []
        with (
            mock.patch.object(
                planner_core,
                "_compute_scenario_without_render",
                return_value=candidate,
            ),
            mock.patch.object(
                planner_core,
                "_minimum_vehicle_count_for_hard_constraints",
                return_value=1,
            ),
            mock.patch.object(
                planner_core,
                "observe_planning_shadow",
                side_effect=lambda shadow, **_kwargs: observed.append(shadow),
            ) as observer,
        ):
            result = planner_core._solve_vehicle_ladder_scenario(
                Planner(),
                points,
                "test",
                current_route_count=2,
                minimum_vehicle_reduction=1,
                bus_type_configs=bus_configs,
            )

        observer.assert_called_once()
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].parity_passed)
        self.assertTrue(observed[0].constraints_passed)
        self.assertNotIn("planning_contract_shadow", result)
        self.assertNotIn("contract_shadow", result)
        self.assertEqual(result["bus_count"], 1)

    def test_vehicle_ladder_does_not_hide_technical_failures(self) -> None:
        with (
            mock.patch.object(
                planner_core,
                "_compute_scenario_without_render",
                side_effect=RuntimeError("provider unavailable"),
            ),
            mock.patch.object(
                planner_core,
                "_minimum_vehicle_count_for_hard_constraints",
                return_value=20,
            ),
            self.assertRaisesRegex(RuntimeError, "provider unavailable"),
        ):
            planner_core._solve_vehicle_ladder_scenario(
                object(),
                [{"is_depot": True}, {"is_depot": False}],
                "test",
                current_route_count=22,
                minimum_vehicle_reduction=2,
            )


if __name__ == "__main__":
    unittest.main()
