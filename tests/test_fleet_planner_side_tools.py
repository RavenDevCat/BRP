from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "client"))
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import demand_routing  # noqa: E402


class FleetPlannerSideToolTests(unittest.TestCase):
    def test_osrm_matrix_keeps_raw_duration_and_distance(self) -> None:
        original = demand_routing.compute_osrm_metrics_from_origin

        def fake_metrics(origin, destinations):
            return [
                {"duration_s": 600.0 + index * 60.0, "distance_m": 1000.0 + index * 100.0}
                for index, _destination in enumerate(destinations)
            ]

        try:
            demand_routing.compute_osrm_metrics_from_origin = fake_metrics
            points = [
                {"lat": 1.0, "lng": 1.0},
                {"lat": 1.1, "lng": 1.1},
            ]
            durations, distances = demand_routing._build_osrm_matrix(points)
        finally:
            demand_routing.compute_osrm_metrics_from_origin = original

        self.assertEqual(durations[0][0], 0.0)
        self.assertEqual(durations[0][1], 600.0)
        self.assertEqual(distances[0][1], 1000.0)

    def test_route_preview_map_data_includes_estimated_stop_times(self) -> None:
        route_preview = {
            "summary": {
                "service_direction": "to_school",
                "traffic_profile_name": "AM Peak (Live)",
            },
            "routes": [
                {
                    "cluster_id": "G01",
                    "duration_s": 1800.0,
                    "distance_m": 5000.0,
                    "selected_vehicle": {"display_name": "25-seat mini bus", "student_capacity": 24},
                    "ordered_points": demand_routing._annotate_ordered_points_with_schedule(
                        [
                            {"lat": 13.7, "lng": 100.5, "address": "Pickup", "student_count": 3},
                            {"lat": 13.8, "lng": 100.6, "address": "School", "student_count": 0},
                        ],
                        [{"duration_s": 1800.0, "distance_m": 5000.0, "geometry": [(13.7, 100.5), (13.8, 100.6)]}],
                        route_duration_s=1800.0,
                        service_direction="to_school",
                    ),
                    "leg_details": [
                        {"duration_s": 1800.0, "distance_m": 5000.0, "geometry": [(13.7, 100.5), (13.8, 100.6)]}
                    ],
                }
            ],
        }
        map_data = demand_routing.build_route_preview_map_data(route_preview)
        pickup = next(stop for stop in map_data["stops"] if not stop["is_depot"])
        school = next(stop for stop in map_data["stops"] if stop["is_depot"])

        self.assertEqual(pickup["scheduled_time_label"], "07:29")
        self.assertEqual(school["scheduled_time_label"], "08:00")
        self.assertEqual(map_data["traffic_profile_name"], "AM Peak (Live)")


class FleetPlannerTrafficContextTests(unittest.TestCase):
    def test_history_record_without_map_data_is_hydrated_for_interactive_map(self) -> None:
        import backend_service  # noqa: E402

        record = {
            "run_id": "legacy",
            "global_plan_result": {
                "summary": {
                    "service_direction": "to_school",
                    "traffic_profile_name": "AM Peak",
                },
                "routes": [
                    {
                        "cluster_id": "G01",
                        "duration_s": 1800.0,
                        "distance_m": 5000.0,
                        "selected_vehicle": {
                            "display_name": "25-seat mini bus",
                            "student_capacity": 24,
                        },
                        "ordered_points": demand_routing._annotate_ordered_points_with_schedule(
                            [
                                {
                                    "lat": 13.7,
                                    "lng": 100.5,
                                    "address": "Pickup",
                                    "student_count": 3,
                                },
                                {
                                    "lat": 13.8,
                                    "lng": 100.6,
                                    "address": "School",
                                    "student_count": 0,
                                },
                            ],
                            [
                                {
                                    "duration_s": 1800.0,
                                    "distance_m": 5000.0,
                                    "geometry": [(13.7, 100.5), (13.8, 100.6)],
                                }
                            ],
                            route_duration_s=1800.0,
                            service_direction="to_school",
                        ),
                        "leg_details": [
                            {
                                "duration_s": 1800.0,
                                "distance_m": 5000.0,
                                "geometry": [(13.7, 100.5), (13.8, 100.6)],
                            }
                        ],
                    }
                ],
                "map_html": "<html></html>",
            },
        }

        hydrated = backend_service._hydrate_fleet_planner_history_record(record)

        self.assertNotIn("map_data", record["global_plan_result"])
        map_data = hydrated["global_plan_result"]["map_data"]
        self.assertEqual(len(map_data["routes"]), 1)
        self.assertEqual(len(map_data["stops"]), 2)
        self.assertEqual(map_data["scenario_name"], "Optimized Plan")

    def test_fleet_context_uses_unscaled_osrm_for_every_market(self) -> None:
        import backend_service  # noqa: E402

        cn_context = backend_service._fleet_traffic_context(
            {"school": {"country": "China", "city": "Shanghai", "address": "School"}},
            service_direction="to_school",
            market="CN",
        )
        kr_context = backend_service._fleet_traffic_context(
            {"school": {"country": "South Korea", "city": "Seoul", "address": "School"}},
            service_direction="to_school",
            market="KR",
        )
        self.assertEqual(cn_context["traffic_profile_name"], "AM Peak")
        self.assertEqual(kr_context["traffic_profile_name"], "AM Peak")
        self.assertEqual(
            cn_context["traffic_profile_context"],
            "Unscaled OSRM candidate time; direct provider validation is authoritative.",
        )
        self.assertEqual(cn_context, kr_context)
        self.assertEqual(set(cn_context), {"traffic_profile_name", "traffic_profile_context"})


if __name__ == "__main__":
    unittest.main()
