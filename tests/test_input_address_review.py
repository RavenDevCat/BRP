from __future__ import annotations

import math
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import planner_core  # noqa: E402


class FakePlanner:
    @staticmethod
    def haversine_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        radius_km = 6371.0088
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
        )
        return 2 * radius_km * math.asin(math.sqrt(a))


class InputAddressReviewTests(unittest.TestCase):
    def test_far_school_distance_is_review_not_reject(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.23, "lng": 121.43},
            {
                "node_id": 1,
                "address": "Far accepted stop",
                "lat": 31.24,
                "lng": 120.86,
                "city": "Shanghai",
                "country": "China",
                "formatted_address": "上海市远距离站点",
            },
        ]

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="To School",
        )

        self.assertEqual(review["summary"]["warning_count"], 1)
        warning = review["warnings"][0]
        self.assertEqual(warning["type"], "school_distance")
        self.assertEqual(warning["status"], "needs_review")
        self.assertTrue(warning["accepted"])

    def test_route_context_detour_warning_uses_current_plan_sequence(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.0, "lng": 121.0},
            {"node_id": 1, "address": "Previous stop", "lat": 31.0, "lng": 121.1},
            {"node_id": 2, "address": "Suspicious stop", "lat": 31.5, "lng": 121.5},
            {"node_id": 3, "address": "Next stop", "lat": 31.0, "lng": 121.2},
        ]
        distance_matrix = [
            [0, 1000, 50000, 2000],
            [1000, 0, 25000, 2000],
            [50000, 25000, 0, 25000],
            [2000, 2000, 25000, 0],
        ]

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="From School",
            current_plan_assessment={"route_summaries": [{"route_id": "R1", "matched_node_ids": [0, 1, 2, 3]}]},
            current_plan_distance_matrix=distance_matrix,
        )

        route_warnings = [item for item in review["warnings"] if item["type"] == "route_context_detour"]
        self.assertEqual(len(route_warnings), 1)
        self.assertEqual(route_warnings[0]["route_id"], "R1")
        self.assertEqual(route_warnings[0]["address"], "Suspicious stop")

    def test_route_context_detour_prefers_final_route_leg_distances(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.0, "lng": 121.0},
            {"node_id": 1, "address": "Previous stop", "lat": 31.0, "lng": 121.1},
            {"node_id": 2, "address": "Corrected stop", "lat": 31.0, "lng": 121.15},
            {"node_id": 3, "address": "Next stop", "lat": 31.0, "lng": 121.2},
        ]
        stale_distance_matrix = [
            [0, 1000, 50000, 2000],
            [1000, 0, 25000, 2000],
            [50000, 25000, 0, 25000],
            [2000, 2000, 25000, 0],
        ]

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="From School",
            current_plan_assessment={"route_summaries": [{"route_id": "R1", "matched_node_ids": [0, 1, 2, 3]}]},
            current_plan_distance_matrix=stale_distance_matrix,
            current_plan_routes=[
                {
                    "route_id": "R1",
                    "leg_details": [
                        {"from_node": 0, "to_node": 1, "distance_m": 1000},
                        {"from_node": 1, "to_node": 2, "distance_m": 1100},
                        {"from_node": 2, "to_node": 3, "distance_m": 1200},
                    ],
                }
            ],
        )

        route_warnings = [item for item in review["warnings"] if item["type"] == "route_context_detour"]
        self.assertEqual(route_warnings, [])

    def test_region_mismatch_warning_for_accepted_china_point(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.23, "lng": 121.43},
            {
                "node_id": 1,
                "address": "Ambiguous accepted stop",
                "lat": 31.31,
                "lng": 120.62,
                "country": "China",
                "city": "Shanghai",
                "formatted_address": "江苏省苏州市工业园区星湖街",
                "adcode": "320571",
            },
        ]

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="To School",
        )

        region_warnings = [item for item in review["warnings"] if item["type"] == "region_mismatch"]
        self.assertEqual(len(region_warnings), 1)
        self.assertEqual(region_warnings[0]["expected_city"], "Shanghai")
        self.assertIn("320571", region_warnings[0]["reason"])

    def test_route_context_corridor_and_reverse_direction_warnings(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.0, "lng": 121.0},
            {"node_id": 1, "address": "Previous stop", "lat": 31.0, "lng": 121.0},
            {"node_id": 2, "address": "Backwards stop", "lat": 31.0, "lng": 120.78},
            {"node_id": 3, "address": "Next stop", "lat": 31.0, "lng": 121.08},
        ]
        distance_matrix = [
            [0, 1000, 25000, 8000],
            [1000, 0, 25000, 8000],
            [25000, 25000, 0, 32000],
            [8000, 8000, 32000, 0],
        ]

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="To School",
            current_plan_assessment={"route_summaries": [{"route_id": "R1", "matched_node_ids": [0, 1, 2, 3]}]},
            current_plan_distance_matrix=distance_matrix,
        )

        warning_types = {item["type"] for item in review["warnings"]}
        self.assertIn("route_context_reverse_direction", warning_types)
        self.assertGreaterEqual(review["summary"]["route_context_warning_count"], 1)

    def test_route_context_isolated_warning_uses_route_spacing(self) -> None:
        points = [
            {"node_id": 0, "is_depot": True, "address": "School", "lat": 31.00, "lng": 121.00},
            {"node_id": 1, "address": "Stop 1", "lat": 31.00, "lng": 121.01},
            {"node_id": 2, "address": "Stop 2", "lat": 31.00, "lng": 121.02},
            {"node_id": 3, "address": "Isolated stop", "lat": 31.35, "lng": 121.35},
            {"node_id": 4, "address": "Stop 4", "lat": 31.00, "lng": 121.03},
            {"node_id": 5, "address": "Stop 5", "lat": 31.00, "lng": 121.04},
            {"node_id": 6, "address": "Stop 6", "lat": 31.00, "lng": 121.05},
        ]
        size = len(points)
        distance_matrix = [[1000 for _ in range(size)] for _ in range(size)]
        for index in range(size):
            distance_matrix[index][index] = 0
        distance_matrix[2][3] = distance_matrix[3][2] = 45000
        distance_matrix[3][4] = distance_matrix[4][3] = 45000
        distance_matrix[2][4] = distance_matrix[4][2] = 1000

        review = planner_core.build_input_address_review(
            FakePlanner(),
            points,
            service_direction="From School",
            current_plan_assessment={"route_summaries": [{"route_id": "R2", "matched_node_ids": [0, 1, 2, 3, 4, 5, 6]}]},
            current_plan_distance_matrix=distance_matrix,
        )

        isolated = [item for item in review["warnings"] if item["type"] == "route_context_isolated"]
        self.assertEqual(len(isolated), 1)
        self.assertEqual(isolated[0]["address"], "Isolated stop")



if __name__ == "__main__":
    unittest.main()
