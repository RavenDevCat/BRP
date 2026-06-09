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

    def test_amap_geocode_uses_reference_area_context_for_ambiguous_china_stop(self) -> None:
        original_request_json = runtime.amap_request_json
        calls: list[str] = []

        def fake_request_json(endpoint: str, params: dict[str, object], limiter: object) -> dict[str, object]:
            self.assertEqual(endpoint, "/v3/geocode/geo")
            self.assertEqual(params.get("city"), "310000")
            query = str(params.get("address") or "")
            calls.append(query)
            if query.startswith("闵行区 "):
                return {
                    "geocodes": [
                        {
                            "formatted_address": "上海市闵行区繁安路",
                            "location": "121.391883,31.067734",
                            "adcode": "310112",
                        }
                    ]
                }
            return {
                "geocodes": [
                    {
                        "formatted_address": "上海市浦东新区瑞建路",
                        "location": "121.599076,31.098642",
                        "adcode": "310115",
                    }
                ]
            }

        try:
            runtime.amap_request_json = fake_request_json  # type: ignore[assignment]
            point = runtime.amap_geocode_query(
                "China",
                "Shanghai",
                "繁安路瑞建路路口",
                {
                    "area_terms": ["闵行区"],
                    "reference_lat": 31.049744,
                    "reference_lng": 121.336559,
                },
            )
        finally:
            runtime.amap_request_json = original_request_json  # type: ignore[assignment]

        self.assertEqual(calls[0], "闵行区 繁安路瑞建路路口")
        self.assertEqual(point["formatted_address"], "上海市闵行区繁安路")
        self.assertEqual(point["adcode"], "310112")
        self.assertEqual(point["geocode_context_applied"], True)
        self.assertEqual(point["geocode_context_terms"], "闵行区")

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
