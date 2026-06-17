from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_service  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
