import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))


def _simple_matrix(size: int) -> list[list[int]]:
    matrix = [[0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i != j:
                matrix[i][j] = 60
    return matrix


def _distance_matrix_with_remote_stop(size: int, remote_index: int = 1) -> list[list[int]]:
    matrix = _simple_matrix(size)
    for i in range(size):
        if i == remote_index:
            continue
        matrix[remote_index][i] = 9000
        matrix[i][remote_index] = 9000
    return matrix


def _point(index: int, passengers: int = 1) -> dict:
    lat = 31.2 + index * 0.001
    lng = 121.4 + index * 0.001
    if index == 1:
        lat = 31.5
        lng = 121.7
    return {
        "address": "School" if index == 0 else f"Stop {index}",
        "lat": lat,
        "lng": lng,
        "plot_lat": lat,
        "plot_lng": lng,
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

    def test_solver_uses_comfort_capacity_when_enabled(self) -> None:
        self._setattr("VEHICLE_FIXED_COST", {"Large Bus": 10000})
        points = [_point(0)] + [_point(index, 20) for index in range(1, 5)]
        matrix = _simple_matrix(len(points))

        self.assertEqual(self.planner._minimum_vehicle_count_for_demand(80, self.planner.build_vehicle_fleet()), 3)

        routes = self.planner.solve_routes(points, matrix, matrix)

        self.assertEqual(len(routes), 4)
        self.assertEqual(sorted(route["load"] for route in routes), [20, 20, 20, 20])
        self.assertTrue(all(route["bus_capacity"] == 42 for route in routes))
        self.assertTrue(all(route["comfort_capacity"] == 35 for route in routes))
        self.assertTrue(all(route["load"] <= route["comfort_capacity"] for route in routes))

    def test_stop_over_comfort_capacity_is_split_when_comfort_enabled(self) -> None:
        points = [_point(0), _point(1, 38)]

        max_batch_size = max(self.planner.solver_capacity_for_vehicle(item) for item in self.planner.build_vehicle_fleet())
        expanded = self.planner.split_oversized_demand_points(points, max_batch_size)
        matrix = _simple_matrix(len(expanded))
        routes = self.planner.solve_routes(expanded, matrix, matrix)

        self.assertEqual([point["passenger_count"] for point in expanded[1:]], [19, 19])
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(route["load"] <= route["comfort_capacity"] for route in routes))

    def test_min_solver_vehicle_count_forces_active_routes(self) -> None:
        self._setattr("BUS_TYPE_CONFIGS", [{"name": "Large Bus", "capacity": 42, "max_count": 4}])
        self._setattr("MIN_SOLVER_VEHICLE_COUNT", 3)
        points = [_point(0)] + [_point(index, 4) for index in range(1, 7)]
        matrix = _simple_matrix(len(points))

        routes = self.planner.solve_routes(points, matrix, matrix)

        self.assertGreaterEqual(len(routes), 3)
        self.assertTrue(all(route["load"] >= 2 for route in routes))

    def test_solver_rejects_single_rider_trivial_route(self) -> None:
        points = [_point(0), _point(1, 1)]
        matrix = _simple_matrix(len(points))

        with self.assertRaisesRegex(RuntimeError, "single-rider"):
            self.planner.solve_routes(points, matrix, matrix)

    def test_solver_rejects_single_rider_active_routes(self) -> None:
        self._setattr("MAX_STOPS_PER_ROUTE", 1)
        points = [_point(0), _point(1, 1), _point(2, 1)]
        matrix = _simple_matrix(len(points))

        with self.assertRaisesRegex(RuntimeError, "single-rider"):
            self.planner.solve_routes(points, matrix, matrix)

    def test_stop_over_physical_capacity_is_split_into_vehicle_batches(self) -> None:
        points = [_point(0), _point(1, 43)]

        expanded = self.planner.split_oversized_demand_points(points, 42)
        matrix = _simple_matrix(len(expanded))
        routes = self.planner.solve_routes(expanded, matrix, matrix)

        self.assertEqual([point["passenger_count"] for point in expanded[1:]], [22, 21])
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(route["load"] <= route["bus_capacity"] for route in routes))
        self.assertTrue(all(route["comfort_capacity"] == 35 for route in routes))
        self.assertEqual(
            {expanded[node]["demand_batch_count"] for route in routes for node in route["nodes"] if node},
            {2},
        )

    def test_oversized_split_uses_target_load_ratio(self) -> None:
        self.assertEqual(self.planner.balanced_demand_batch_sizes(38, 42), [38])
        self.assertEqual(self.planner.balanced_demand_batch_sizes(43, 42), [22, 21])
        self.assertEqual(self.planner.balanced_demand_batch_sizes(60, 42), [30, 30])
        self.assertEqual(self.planner.balanced_demand_batch_sizes(100, 42), [25, 25, 25, 25])

    def test_oversized_split_caps_extra_batches(self) -> None:
        self._setattr("DEMAND_SPLIT_MAX_EXTRA_BATCHES", 1)

        self.assertEqual(self.planner.balanced_demand_batch_sizes(140, 42), [28, 28, 28, 28, 28])

    def test_express_reservation_does_not_starve_regular_fleet_capacity(self) -> None:
        self._setattr("BUS_TYPE_CONFIGS", [{"name": "Compact", "capacity": 10, "max_count": 4}])
        self._setattr("VEHICLE_FIXED_COST", {"Compact": 0})
        self._setattr("MIN_LOAD_TARGET", {"Compact": 0})
        self._setattr("MIN_LOAD_PENALTY", {"Compact": 0})
        self._setattr("COMFORT_LOAD_FACTOR", 1.0)
        self._setattr("RESERVED_EXPRESS_BUSES", 1)
        self._setattr("EXPRESS_THRESHOLD_KM", 15.0)
        self._setattr("EXPRESS_SKIP_INNER_KM", 8.0)
        points = [_point(0)] + [_point(index) for index in range(1, 33)]
        time_matrix = _simple_matrix(len(points))
        distance_matrix = _distance_matrix_with_remote_stop(len(points))

        routes = self.planner.solve_routes(points, time_matrix, distance_matrix)

        served_nodes = {node for route in routes for node in route["nodes"] if node}
        self.assertEqual(served_nodes, set(range(1, len(points))))
        self.assertLessEqual(max(route["load"] for route in routes), 10)


if __name__ == "__main__":
    unittest.main()
