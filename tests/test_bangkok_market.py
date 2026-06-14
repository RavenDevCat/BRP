from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "client"))

import client_runtime as runtime  # noqa: E402
import distance_tool  # noqa: E402

sys.path.insert(0, str(ROOT / "apps" / "backend"))
from planner_core import resolve_traffic_profile  # noqa: E402


class BangkokMarketTests(unittest.TestCase):
    def test_client_runtime_uses_google_and_bangkok_cache_keys(self) -> None:
        self.assertTrue(runtime.is_bangkok_market_country("ประเทศไทย"))
        self.assertEqual(runtime.determine_currency_code([{"country": "Thailand"}]), "THB")
        self.assertEqual(runtime.expected_geocode_provider("Thailand", "Sukhumvit 31"), "google")
        self.assertEqual(runtime.google_country_code("TH"), "TH")
        self.assertEqual(runtime.canonical_cache_country("TH"), "Bangkok")
        self.assertEqual(runtime.canonical_cache_city("Thailand", "กรุงเทพมหานคร"), "Bangkok")

    def test_bangkok_plausibility_matches_market_bbox(self) -> None:
        self.assertTrue(
            runtime.is_plausible_geocode_result(
                "Thailand",
                "Bangkok",
                13.7563,
                100.5018,
                "Bangkok, Thailand",
            )
        )
        self.assertFalse(
            runtime.is_plausible_geocode_result(
                "Thailand",
                "Bangkok",
                35.6812,
                139.7671,
                "Tokyo, Japan",
            )
        )

    def test_distance_tool_resolves_bangkok_osrm_endpoint(self) -> None:
        endpoint = distance_tool.resolve_osrm_base_url(
            [
                {
                    "country": "Thailand",
                    "city": "Bangkok",
                    "lat": 13.7563,
                    "lng": 100.5018,
                }
            ]
        )
        self.assertEqual(endpoint, "http://127.0.0.1:5007")

    def test_bangkok_traffic_profile_defaults_to_static_all_day_multiplier(self) -> None:
        for profile_name in ("Off-Peak", "AM Peak", "PM Peak"):
            resolved_name, multiplier, context = resolve_traffic_profile(
                profile_name,
                [{"country": "Bangkok", "city": "Bangkok"}],
            )
            self.assertEqual(resolved_name, profile_name)
            self.assertEqual(multiplier, 1.75)
            self.assertEqual(context, "Bangkok default")

    def test_legacy_thailand_bangkok_traffic_profile_uses_bangkok_default(self) -> None:
        resolved_name, multiplier, context = resolve_traffic_profile(
            "AM Peak",
            [{"country": "Thailand", "city": "Bangkok"}],
        )

        self.assertEqual(resolved_name, "AM Peak")
        self.assertEqual(multiplier, 1.75)
        self.assertEqual(context, "Bangkok default")


class ThailandRouteAuditRuntimeTests(unittest.TestCase):
    def test_backend_runtime_routes_bangkok_to_google_and_osrm(self) -> None:
        sys.path.insert(0, str(ROOT / "apps" / "backend"))
        import BusingProblem as planner  # noqa: E402

        self.assertTrue(planner.is_bangkok_market_country("TH"))
        self.assertEqual(planner.determine_currency_code([{"country": "Thailand"}]), "THB")
        self.assertEqual(planner._canonical_country("ประเทศไทย"), "THAILAND")
        self.assertEqual(planner._canonical_city("กรุงเทพมหานคร"), "BANGKOK")
        self.assertEqual(planner.geocode_provider_order("Thailand"), ["google"])
        self.assertEqual(planner.geocode_provider_order("Bangkok"), ["google"])
        self.assertEqual(
            planner.resolve_osrm_base_url(
                [
                    {"country": "Thailand", "city": "Bangkok", "address": "School", "is_depot": False}
                ]
            ),
            "http://127.0.0.1:5007",
        )
        self.assertEqual(
            planner.resolve_osrm_base_url(
                [
                    {"country": "Bangkok", "city": "Bangkok", "address": "School", "is_depot": False}
                ]
            ),
            "http://127.0.0.1:5007",
        )


if __name__ == "__main__":
    unittest.main()
