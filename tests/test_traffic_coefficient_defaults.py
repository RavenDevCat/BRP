from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_service  # noqa: E402
import BusingProblem  # noqa: E402


class TrafficCoefficientDefaultTests(unittest.TestCase):
    def test_planner_config_payload_normalizes_traffic_coefficient_mode(self) -> None:
        attributed = backend_service._planner_config_payload(
            {"traffic_coefficient_mode": "ATTRIBUTED"}
        )
        invalid = backend_service._planner_config_payload(
            {"traffic_coefficient_mode": "surprise"}
        )

        self.assertEqual(attributed["traffic_coefficient_mode"], "attributed")
        self.assertEqual(invalid["traffic_coefficient_mode"], "legacy")

    def test_workbook_preview_suggested_config_preserves_attributed_mode(self) -> None:
        suggested = backend_service._suggest_planner_config_from_current_plan(
            {
                "service_direction": "To School",
                "fleet": [
                    {"bus_type": "Large", "seat_count": 42, "vehicle_count": 4},
                    {"bus_type": "Mini", "seat_count": 18, "vehicle_count": 2},
                ],
            },
            {
                "traffic_coefficient_mode": "attributed",
                "traffic_profile_name": "AM Peak",
                "include_subway_aggregation_scenario": True,
                "include_nearby_aggregation_scenario": False,
            },
        )

        self.assertEqual(suggested["traffic_coefficient_mode"], "attributed")
        self.assertEqual(suggested["traffic_profile_name"], "AM Peak")
        self.assertEqual(suggested["service_direction"], "To School")
        self.assertTrue(suggested["include_subway_aggregation_scenario"])
        self.assertFalse(suggested["include_nearby_aggregation_scenario"])

    def test_deployment_features_exposes_configured_default_mode(self) -> None:
        original = backend_service.DEFAULT_TRAFFIC_COEFFICIENT_MODE
        try:
            backend_service.DEFAULT_TRAFFIC_COEFFICIENT_MODE = "attributed"
            payload = backend_service._deployment_features_payload()
        finally:
            backend_service.DEFAULT_TRAFFIC_COEFFICIENT_MODE = original

        self.assertEqual(payload["default_traffic_coefficient_mode"], "attributed")

    def test_solver_route_budget_uses_raw_osrm_time(self) -> None:
        original_multiplier = BusingProblem.TRAFFIC_TIME_MULTIPLIER
        try:
            BusingProblem.TRAFFIC_TIME_MULTIPLIER = 2.0
            display_matrix, _distance_matrix = BusingProblem.seed_edge_metrics(
                [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 0.01}]
            )
            raw_matrix = BusingProblem.solver_route_budget_time_matrix(display_matrix)
        finally:
            BusingProblem.TRAFFIC_TIME_MULTIPLIER = original_multiplier

        self.assertLess(raw_matrix[0][1], display_matrix[0][1])

    def test_workbook_direction_and_auto_budget_helpers(self) -> None:
        self.assertEqual(
            backend_service._infer_service_direction_from_label("DEMH-To School.xlsx"),
            "To School",
        )
        self.assertEqual(
            backend_service._infer_service_direction_from_label("DEMH-From School.xlsx"),
            "From School",
        )

        class FakePlanner:
            OSRM_BASE_URL = "original"
            RAW_SOLVER_TIME_MATRIX = [
                [0, 60, 600],
                [60, 0, 1200],
                [600, 1200, 0],
            ]

            def resolve_osrm_base_url(self, _points):
                return "fake-osrm"

            def build_osrm_full_matrix(self, _points):
                self.OSRM_BASE_URL = "used-fake-osrm"
                return self.RAW_SOLVER_TIME_MATRIX, self.RAW_SOLVER_TIME_MATRIX

        current_plan = {
            "stops": [
                {"route_id": "r1", "stop_sequence": 1, "country": "CN", "city": "Shanghai", "address": "school"},
                {"route_id": "r1", "stop_sequence": 2, "country": "CN", "city": "Shanghai", "address": "a"},
                {"route_id": "r2", "stop_sequence": 1, "country": "CN", "city": "Shanghai", "address": "school"},
                {"route_id": "r2", "stop_sequence": 2, "country": "CN", "city": "Shanghai", "address": "b"},
            ]
        }
        prepared_payload = {
            "original_points": [
                {"node_id": 0, "country": "CN", "city": "Shanghai", "address": "school"},
                {"node_id": 1, "country": "CN", "city": "Shanghai", "address": "a"},
                {"node_id": 2, "country": "CN", "city": "Shanghai", "address": "b"},
            ]
        }

        original_loader = backend_service.load_legacy_planner
        fake_planner = FakePlanner()
        try:
            backend_service.load_legacy_planner = lambda: fake_planner
            budget_minutes = backend_service._auto_current_plan_route_budget_minutes(
                current_plan,
                prepared_payload,
            )
            budget_details = backend_service._auto_current_plan_route_budget_details(
                current_plan,
                prepared_payload,
            )
        finally:
            backend_service.load_legacy_planner = original_loader

        self.assertEqual(budget_minutes, 10)
        self.assertEqual(fake_planner.OSRM_BASE_URL, "original")
        self.assertEqual(budget_details["status"], "ready")
        self.assertEqual(budget_details["minutes"], 10)
        self.assertEqual(budget_details["longest_route_id"], "r2")
        self.assertEqual(budget_details["measured_route_count"], 2)
        self.assertEqual(budget_details["amap_route_status"], "unavailable")
        self.assertEqual(budget_details["amap_route_reason"], "missing_amap_key")

    def test_auto_budget_includes_amap_longest_route_note(self) -> None:
        class FakePlanner:
            AMAP_KEY = "fake"
            OSRM_BASE_URL = "original"
            RAW_SOLVER_TIME_MATRIX = [
                [0, 60, 780],
                [60, 0, 1200],
                [780, 1200, 0],
            ]

            def resolve_osrm_base_url(self, _points):
                return "fake-osrm"

            def build_osrm_full_matrix(self, _points):
                return self.RAW_SOLVER_TIME_MATRIX, self.RAW_SOLVER_TIME_MATRIX

        current_plan = {
            "stops": [
                {"route_id": "r1", "stop_sequence": 1, "country": "CN", "city": "Shanghai", "address": "school"},
                {"route_id": "r1", "stop_sequence": 2, "country": "CN", "city": "Shanghai", "address": "a"},
                {"route_id": "r2", "stop_sequence": 1, "country": "CN", "city": "Shanghai", "address": "school"},
                {"route_id": "r2", "stop_sequence": 2, "country": "CN", "city": "Shanghai", "address": "b"},
            ]
        }
        prepared_payload = {
            "original_points": [
                {"node_id": 0, "country": "CN", "city": "Shanghai", "address": "school", "lat": 31.2, "lng": 121.4},
                {"node_id": 1, "country": "CN", "city": "Shanghai", "address": "a", "lat": 31.21, "lng": 121.41},
                {"node_id": 2, "country": "CN", "city": "Shanghai", "address": "b", "lat": 31.3, "lng": 121.5},
            ]
        }

        def fake_amap_stats(_planner, request_points, _cache, state):
            self.assertEqual(request_points, [(31.2, 121.4), (31.3, 121.5)])
            state["api_calls"] = 1
            return {
                "duration_s": 900,
                "distance_m": 12345,
                "source": "amap_final_route",
            }

        original_loader = backend_service.load_legacy_planner
        original_amap_stats = backend_service._amap_route_stats
        original_load_json = backend_service.load_json_object
        original_save_json = backend_service.save_json_object
        try:
            backend_service.load_legacy_planner = lambda: FakePlanner()
            backend_service._amap_route_stats = fake_amap_stats
            backend_service.load_json_object = lambda _path: {}
            backend_service.save_json_object = lambda *_args, **_kwargs: None
            budget_details = backend_service._auto_current_plan_route_budget_details(
                current_plan,
                prepared_payload,
            )
        finally:
            backend_service.load_legacy_planner = original_loader
            backend_service._amap_route_stats = original_amap_stats
            backend_service.load_json_object = original_load_json
            backend_service.save_json_object = original_save_json

        self.assertEqual(budget_details["status"], "ready")
        self.assertEqual(budget_details["minutes"], 13)
        self.assertEqual(budget_details["longest_route_id"], "r2")
        self.assertEqual(budget_details["amap_route_status"], "ready")
        self.assertEqual(budget_details["amap_route_duration_minutes"], 15.0)
        self.assertEqual(budget_details["amap_route_distance_km"], 12.3)
        self.assertEqual(budget_details["amap_route_api_calls"], 1)


if __name__ == "__main__":
    unittest.main()
