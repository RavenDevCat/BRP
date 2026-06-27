import importlib
import json
import tempfile
import unittest
from pathlib import Path


class AMapDisplayCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = importlib.import_module("backend_service")

    def test_incomplete_cached_geometry_is_refreshed_for_duration(self) -> None:
        old_path = self.service.AMAP_DISPLAY_GEOMETRY_CACHE_PATH
        old_fetch = self.service._fetch_amap_display_geometry

        points = [
            {
                "lat": 31.2,
                "lng": 121.4,
                "provider": "amap",
                "adcode": "310000",
            },
            {
                "lat": 31.21,
                "lng": 121.41,
                "provider": "amap",
                "adcode": "310000",
            },
        ]
        request_points = [
            self.service._amap_request_coordinates_for_point(point)
            for point in points
        ]
        request_points = [point for point in request_points if point is not None]

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "amap_display_geometry.json"
            cache_key = self.service._amap_display_cache_key(request_points)
            cache_path.write_text(
                json.dumps(
                    {
                        cache_key: {
                            "created_at": "2026-06-17T06:19:03+00:00",
                            "point_count": 2,
                            "geometry": [[121.4, 31.2], [121.41, 31.21]],
                        }
                    }
                ),
                encoding="utf-8",
            )

            calls: list[list[tuple[float, float]]] = []

            def fake_fetch(
                requested_points: list[tuple[float, float]]
            ) -> dict[str, object]:
                calls.append(requested_points)
                return {
                    "geometry": [[121.4001, 31.2001], [121.4101, 31.2101]],
                    "duration_s": 321,
                    "distance_m": 654,
                }

            self.service.AMAP_DISPLAY_GEOMETRY_CACHE_PATH = cache_path
            self.service._fetch_amap_display_geometry = fake_fetch
            try:
                geometry, source, message, duration_s, distance_m = (
                    self.service._amap_display_geometry_for_route(points, [0, 1])
                )
            finally:
                self.service.AMAP_DISPLAY_GEOMETRY_CACHE_PATH = old_path
                self.service._fetch_amap_display_geometry = old_fetch

            saved_cache = json.loads(cache_path.read_text(encoding="utf-8"))

            self.assertEqual(calls, [request_points])
            self.assertEqual(source, "amap_cn")
            self.assertEqual(message, "")
            self.assertEqual(geometry, [[121.4001, 31.2001], [121.4101, 31.2101]])
            self.assertEqual(duration_s, 321)
            self.assertEqual(distance_m, 654)
            self.assertEqual(saved_cache[cache_key]["duration_s"], 321)
            self.assertEqual(saved_cache[cache_key]["distance_m"], 654)


if __name__ == "__main__":
    unittest.main()
