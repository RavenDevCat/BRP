from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ortools.constraint_solver import pywrapcp, routing_enums_pb2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps"))

from ortools_route_core import (  # noqa: E402
    add_capacity_dimension,
    add_route_time_dimension,
    add_stop_count_dimension,
    build_guided_local_search_parameters,
    register_matrix_transit,
)


class OrtoolsRouteCoreTests(unittest.TestCase):
    @staticmethod
    def _solve(*, capacity: int, max_stops: int):
        matrix = [
            [0, 60, 60],
            [60, 0, 60],
            [60, 60, 0],
        ]
        manager = pywrapcp.RoutingIndexManager(3, 1, [0], [0])
        routing = pywrapcp.RoutingModel(manager)
        transit_index = register_matrix_transit(
            routing,
            manager,
            matrix,
            zero_cost_to_nodes={0},
        )
        add_route_time_dimension(routing, transit_index, 600)
        add_capacity_dimension(
            routing,
            manager,
            [0, 1, 1],
            [capacity],
            name="Load",
        )
        add_stop_count_dimension(
            routing,
            manager,
            {1, 2},
            max_stops,
        )
        search = build_guided_local_search_parameters(
            pywrapcp,
            routing_enums_pb2,
            first_solution_strategy=routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
            time_limit_seconds=1,
        )
        return routing.SolveWithParameters(search)

    def test_shared_dimensions_preserve_capacity_and_stop_limits(self) -> None:
        self.assertIsNotNone(self._solve(capacity=2, max_stops=2))
        self.assertIsNone(self._solve(capacity=1, max_stops=2))
        self.assertIsNone(self._solve(capacity=2, max_stops=1))


if __name__ == "__main__":
    unittest.main()
