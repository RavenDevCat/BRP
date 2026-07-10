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

    def test_map_payload_loads_and_saves_display_cache_once(self) -> None:
        old_load = self.service._load_amap_display_cache_unlocked
        old_save = self.service._save_amap_display_cache_unlocked
        old_fetch = self.service._fetch_amap_display_geometry
        old_should_use = self.service._should_use_amap_display_geometry
        calls = {"load": 0, "save": 0, "fetch": 0}
        saved: dict[str, object] = {}

        def fake_load() -> dict[str, object]:
            calls["load"] += 1
            return dict(saved)

        def fake_save(cache: dict[str, object]) -> None:
            calls["save"] += 1
            saved.clear()
            saved.update(cache)

        def fake_fetch(_points: list[tuple[float, float]]) -> dict[str, object]:
            calls["fetch"] += 1
            return {
                "geometry": [[121.4, 31.2], [121.41, 31.21]],
                "duration_s": 300,
                "distance_m": 500,
            }

        self.service._load_amap_display_cache_unlocked = fake_load
        self.service._save_amap_display_cache_unlocked = fake_save
        self.service._fetch_amap_display_geometry = fake_fetch
        self.service._should_use_amap_display_geometry = lambda *_args: True
        try:
            payload, error = self.service._build_job_map_payload(
                {
                    "job_id": "cache-smoke",
                    "result": {
                        "structured_results": {
                            "current_plan": {
                                "points": [
                                    {"lat": 31.2, "lng": 121.4, "provider": "amap", "adcode": "310000", "is_depot": True},
                                    {"lat": 31.21, "lng": 121.41, "provider": "amap", "adcode": "310000"},
                                    {"lat": 31.22, "lng": 121.42, "provider": "amap", "adcode": "310000"},
                                ],
                                "routes": [
                                    {"route_id": "A", "nodes": [0, 1], "time_s": 300, "distance_m": 500},
                                    {"route_id": "B", "nodes": [0, 2], "time_s": 300, "distance_m": 500},
                                ],
                            }
                        }
                    },
                },
                "current_plan",
                "current_plan",
                attach_impact=False,
            )
        finally:
            self.service._load_amap_display_cache_unlocked = old_load
            self.service._save_amap_display_cache_unlocked = old_save
            self.service._fetch_amap_display_geometry = old_fetch
            self.service._should_use_amap_display_geometry = old_should_use

        self.assertIsNone(error)
        self.assertEqual(len(payload["routes"]), 2)
        self.assertEqual(calls, {"load": 2, "save": 1, "fetch": 2})


if __name__ == "__main__":
    unittest.main()
