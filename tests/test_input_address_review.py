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
                "lng": 120.80,
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


if __name__ == "__main__":
    unittest.main()
