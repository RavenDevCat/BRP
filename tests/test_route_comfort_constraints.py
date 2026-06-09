import importlib
import unittest


def _simple_matrix(size: int) -> list[list[int]]:
    matrix = [[0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i != j:
                matrix[i][j] = 60
    return matrix


def _point(index: int, passengers: int = 1) -> dict:
    return {
        "address": "School" if index == 0 else f"Stop {index}",
        "lat": 31.2 + index * 0.001,
        "lng": 121.4 + index * 0.001,
        "plot_lat": 31.2 + index * 0.001,
        "plot_lng": 121.4 + index * 0.001,
        "passenger_count": passengers,
        "is_depot": index == 0,
    }


class RouteComfortConstraintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = importlib.import_module("BusingProblem")
        self._originals: list[tuple[str, object]] = []
        self._configure_solver()

    def tearDown(self) -> None:
        for name, value in reversed(self._originals):
            setattr(self.planner, name, value)

    def _setattr(self, name: str, value: object) -> None:
        self._originals.append((name, getattr(self.planner, name)))
        setattr(self.planner, name, value)

    def _configure_solver(self) -> None:
        self._setattr("BUS_TYPE_CONFIGS", [{"name": "Large Bus", "capacity": 42, "max_count": 8}])
        self._setattr("VEHICLE_FIXED_COST", {"Large Bus": 0})
        self._setattr("MIN_LOAD_TARGET", {"Large Bus": 0})
        self._setattr("MIN_LOAD_PENALTY", {"Large Bus": 0})
        self._setattr("MAX_STOPS_PER_ROUTE", 10)
        self._setattr("COMFORT_LOAD_FACTOR", 0.85)
        self._setattr("DEMAND_SPLIT_TARGET_LOAD_RATIO", 0.70)
        self._setattr("DEMAND_SPLIT_MIN_BATCH_SIZE", 8)
        self._setattr("DEMAND_SPLIT_MAX_EXTRA_BATCHES", 2)
        self._setattr("SERVICE_DIRECTION", "From School")
        self._setattr("RESERVED_EXPRESS_BUSES", 0)
        self._setattr("EXPRESS_THRESHOLD_KM", 9999.0)
        self._setattr("MAX_ROUTE_DURATION_SECONDS", 3600)
        self._setattr("ROUTE_DURATION_GRACE_SECONDS", 0)

    def test_solver_caps_routes_at_ten_service_stops(self) -> None:
        points = [_point(0)] + [_point(index) for index in range(1, 23)]
        matrix = _simple_matrix(len(points))

        routes = self.planner.solve_routes(points, matrix, matrix)

        self.assertGreaterEqual(len(routes), 3)
        self.assertLessEqual(max(route["stop_count"] for route in routes), 10)
        self.assertTrue(all(route["max_stops"] == 10 for route in routes))

    def test_solver_uses_comfort_capacity_not_physical_capacity(self) -> None:
        points = [_point(0), _point(1, 30), _point(2, 30), _point(3, 30)]
        matrix = _simple_matrix(len(points))

        routes = self.planner.solve_routes(points, matrix, matrix)

        self.assertGreaterEqual(len(routes), 3)
        self.assertTrue(all(route["comfort_capacity"] == 35 for route in routes))
        self.assertTrue(all(route["load"] <= route["comfort_capacity"] for route in routes))

    def test_oversized_stop_is_split_into_multiple_vehicle_batches(self) -> None:
        points = [_point(0), _point(1, 38)]

        expanded = self.planner.split_oversized_demand_points(points, 35)
        matrix = _simple_matrix(len(expanded))
        routes = self.planner.solve_routes(expanded, matrix, matrix)

        self.assertEqual([point["passenger_count"] for point in expanded[1:]], [19, 19])
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(route["load"] <= route["comfort_capacity"] for route in routes))
        self.assertEqual(
            {expanded[node]["demand_batch_count"] for route in routes for node in route["nodes"] if node},
            {2},
        )

    def test_oversized_split_uses_target_load_ratio(self) -> None:
        self.assertEqual(self.planner.balanced_demand_batch_sizes(38, 35), [19, 19])
        self.assertEqual(self.planner.balanced_demand_batch_sizes(60, 35), [20, 20, 20])
        self.assertEqual(self.planner.balanced_demand_batch_sizes(100, 35), [25, 25, 25, 25])

    def test_oversized_split_caps_extra_batches(self) -> None:
        self._setattr("DEMAND_SPLIT_MAX_EXTRA_BATCHES", 1)

        self.assertEqual(self.planner.balanced_demand_batch_sizes(140, 35), [28, 28, 28, 28, 28])


if __name__ == "__main__":
    unittest.main()
