import importlib
import unittest


def _point(index: int) -> dict:
    return {
        "address": "School" if index == 0 else f"Stop {index}",
        "plot_lat": 31.2 + index * 0.001,
        "plot_lng": 121.4 + index * 0.001,
        "passenger_count": 1,
        "is_depot": index == 0,
    }


def _route() -> dict:
    return {
        "vehicle_id": 1,
        "bus_type_name": "Large Bus",
        "load": 2,
        "comfort_capacity": 35,
        "bus_capacity": 42,
        "stop_count": 1,
        "max_stops": 10,
        "time_s": 600,
        "distance_m": 1200,
        "nodes": [0, 1],
    }


class MapSummaryRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = importlib.import_module("BusingProblem")

    def test_nearby_cluster_summary_renders_private_access_rows(self) -> None:
        html = self.planner.build_map_summary_html(
            [_point(0), _point(1)],
            [_route()],
            outlying_private_access_rows=[
                {
                    "address": "Clustered Rider",
                    "pickup_address": "Stop 1",
                    "private_access_type": "clustered_rider",
                    "private_drive_time_s": 300,
                    "private_drive_distance_m": 800,
                }
            ],
        )

        self.assertIn("Nearby Cluster Centers", html)
        self.assertIn("Clustered Rider", html)

    def test_private_drive_summary_renders_route_private_stops(self) -> None:
        html = self.planner.build_map_summary_html(
            [_point(0), _point(1)],
            [_route()],
            outlying_private_access_rows=[
                {
                    "address": "Private Rider",
                    "pickup_route_id": "Bus 1",
                    "private_access_type": "private_drive_stop",
                    "private_drive_time_s": 300,
                    "private_drive_distance_m": 800,
                }
            ],
        )

        self.assertIn("Private drive stops", html)
        self.assertIn("Private Rider", html)
        self.assertNotIn("Nearby Cluster Centers", html)


if __name__ == "__main__":
    unittest.main()
