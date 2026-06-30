from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "client"))

import client_runtime as runtime  # noqa: E402


class ChinaGeocodeValidationTests(unittest.TestCase):
    def test_china_city_alias_uses_amap_adcode(self) -> None:
        self.assertEqual(runtime._amap_city_param("China", "Shanghai"), "310000")
        self.assertEqual(runtime._amap_city_param("China", "上海"), "310000")
        self.assertEqual(runtime._amap_city_param("China", "Suzhou"), "320500")

    def test_shanghai_rejects_suzhou_adcode_candidate(self) -> None:
        self.assertFalse(
            runtime.is_plausible_geocode_result(
                "China",
                "Shanghai",
                31.175160,
                120.906297,
                "江苏省苏州市昆山市长寿路898号",
                "320583",
            )
        )
        self.assertTrue(
            runtime.is_plausible_geocode_result(
                "China",
                "Shanghai",
                31.234415,
                121.429718,
                "上海市普陀区长寿路898号",
                "310107",
            )
        )

    def test_amap_geocode_uses_first_plausible_candidate(self) -> None:
        original_request_json = runtime.amap_request_json

        def fake_request_json(endpoint: str, params: dict[str, object], limiter: object) -> dict[str, object]:
            self.assertEqual(endpoint, "/v3/geocode/geo")
            self.assertEqual(params.get("city"), "310000")
            return {
                "geocodes": [
                    {
                        "formatted_address": "江苏省苏州市昆山市长寿路898号",
                        "location": "120.906297,31.175160",
                        "adcode": "320583",
                    },
                    {
                        "formatted_address": "上海市普陀区长寿路898号",
                        "location": "121.429718,31.234415",
                        "adcode": "310107",
                    },
                ]
            }

        try:
            runtime.amap_request_json = fake_request_json  # type: ignore[assignment]
            point = runtime.amap_geocode_query("China", "Shanghai", "长寿路898号")
        finally:
            runtime.amap_request_json = original_request_json  # type: ignore[assignment]

        self.assertEqual(point["formatted_address"], "上海市普陀区长寿路898号")
        self.assertEqual(point["adcode"], "310107")
        self.assertEqual(point["requested_city_param"], "310000")

    def test_shanghai_rejects_city_level_fallback(self) -> None:
        self.assertFalse(
            runtime.is_plausible_geocode_result(
                "China",
                "Shanghai",
                31.230525,
                121.473667,
                "上海市",
                "310000",
            )
        )
        self.assertTrue(
            runtime.is_plausible_geocode_result(
                "China",
                "Shanghai",
                31.196,
                121.317,
                "上海市松江区九杜路1001号",
                "310117",
            )
        )

    def test_amap_geocode_falls_back_to_poi_after_city_level_result(self) -> None:
        original_request_json = runtime.amap_request_json
        calls: list[str] = []

        def fake_request_json(endpoint: str, params: dict[str, object], limiter: object) -> dict[str, object]:
            calls.append(endpoint)
            if endpoint == "/v3/geocode/geo":
                return {
                    "geocodes": [
                        {
                            "formatted_address": "上海市",
                            "location": "121.473667,31.230525",
                            "adcode": "310000",
                        }
                    ]
                }
            self.assertEqual(endpoint, "/v3/place/text")
            self.assertEqual(params.get("city"), "310000")
            self.assertEqual(params.get("citylimit"), "true")
            return {
                "pois": [
                    {
                        "pname": "上海市",
                        "cityname": "上海市",
                        "adname": "松江区",
                        "address": "九杜路附近",
                        "name": "TODTOWN天荟悦麓",
                        "location": "121.319000,31.135000",
                        "adcode": "310117",
                    }
                ]
            }

        try:
            runtime.amap_request_json = fake_request_json  # type: ignore[assignment]
            point = runtime.amap_geocode_query("China", "Shanghai", "TODTOWN天荟悦麓")
        finally:
            runtime.amap_request_json = original_request_json  # type: ignore[assignment]

        self.assertIn("/v3/place/text", calls)
        self.assertEqual(point["adcode"], "310117")
        self.assertIn("松江区", point["formatted_address"])

    def test_far_from_school_warning_is_review_not_reject(self) -> None:
        school = {"lat": 31.2300, "lng": 121.4300, "address": "School"}
        point = {
            "lat": 31.175160,
            "lng": 120.906297,
            "address": "长寿路898号",
            "city": "Shanghai",
            "country": "China",
            "formatted_address": "上海市普陀区长寿路898号",
            "provider": "amap",
            "source_excel_rows": "12",
        }

        warning = runtime.build_school_distance_review_warning(school, point, threshold_km=20)

        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertEqual(warning["status"], "needs_review")
        self.assertEqual(warning["accepted"], "true")
        self.assertIn("distance_to_school_km", warning)


if __name__ == "__main__":
    unittest.main()
