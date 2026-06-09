from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import planner_core  # noqa: E402


class FakePlanner:
    OSRM_BASE_URL = "http://example-osrm"
    AMAP_KEY = "fake"

    def resolve_osrm_base_url(self, points):
        return "http://example-osrm"

    def osrm_driving_direction(self, origin, destination):
        return 1000, 100, []


class PeakTrafficCalibrationTests(unittest.TestCase):
    def test_weighted_average_factor(self) -> None:
        factor = planner_core._weighted_average_factor(
            [
                {"factor": 1.2, "weight_s": 100.0},
                {"factor": 1.8, "weight_s": 300.0},
            ]
        )
        self.assertAlmostEqual(float(factor or 0.0), 1.65)

    def test_sample_current_plan_edges_dedupes_and_caps(self) -> None:
        points = [
            {"node_id": 0},
            {"node_id": 1},
            {"node_id": 2},
            {"node_id": 3},
        ]
        sampled = planner_core._sample_current_plan_edges(
            {
                "R1": [points[0], points[1], points[2]],
                "R2": [points[0], points[1], points[3]],
            },
            max_edges=2,
        )
        self.assertEqual(len(sampled), 2)
        self.assertEqual([(a["node_id"], b["node_id"]) for _, a, b in sampled], [(0, 1), (1, 2)])

    def test_calibration_selects_pm_for_from_school(self) -> None:
        original_amap_helper = planner_core.amap_future_driving_duration_seconds
        original_periods = planner_core.peak_traffic_periods
        original_enabled = planner_core.AMAP_TRAFFIC_CALIBRATION_ENABLED

        def fake_periods():
            return [
                {"key": "am_peak", "label": "AM Peak", "firsttime": 1, "interval": 900, "count": 1},
                {"key": "pm_peak", "label": "PM Peak", "firsttime": 2, "interval": 900, "count": 1},
            ]

        def fake_amap_helper(planner, origin, destination, period, cache):
            return ([120.0] if period["key"] == "am_peak" else [150.0]), False

        planner_core.peak_traffic_periods = fake_periods  # type: ignore[assignment]
        planner_core.amap_future_driving_duration_seconds = fake_amap_helper  # type: ignore[assignment]
        planner_core.AMAP_TRAFFIC_CALIBRATION_ENABLED = True
        try:
            points = [
                {
                    "node_id": 0,
                    "country": "China",
                    "city": "Shanghai",
                    "address": "School",
                    "lat": 31.0,
                    "lng": 121.0,
                    "is_depot": True,
                },
                {
                    "node_id": 1,
                    "country": "China",
                    "city": "Shanghai",
                    "address": "Stop A",
                    "lat": 31.1,
                    "lng": 121.1,
                },
            ]
            current_plan = {
                "service_direction": "From School",
                "stops": [
                    {"stop_id": "R1__1", **points[0]},
                    {"stop_id": "R1__2", **points[1]},
                ],
                "assignments": [
                    {"route_id": "R1", "stop_id": "R1__1", "stop_sequence": 1, "bus_type": "Bus"},
                    {"route_id": "R1", "stop_id": "R1__2", "stop_sequence": 2, "bus_type": "Bus"},
                ],
                "fleet": [{"bus_type": "Bus", "seat_count": 42}],
            }
            result = planner_core.calibrate_peak_traffic_multiplier(
                FakePlanner(),
                current_plan,
                points,
                [{"country": "China", "city": "Shanghai", "address": "School", "passenger_count": 0}],
                planner_core.PlannerConfig(service_direction="From School"),
                "PM Peak",
                1.75,
                "Shanghai default",
            )
        finally:
            planner_core.amap_future_driving_duration_seconds = original_amap_helper  # type: ignore[assignment]
            planner_core.peak_traffic_periods = original_periods  # type: ignore[assignment]
            planner_core.AMAP_TRAFFIC_CALIBRATION_ENABLED = original_enabled

        self.assertTrue(result["succeeded"])
        self.assertEqual(result["selected_period"], "pm_peak")
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.5)
        route_stats = result["route_periods"]["pm_peak"]["R1"]
        self.assertAlmostEqual(float(route_stats["osrm_duration_s"]), 100.0)
        self.assertAlmostEqual(float(route_stats["amap_duration_s"]), 150.0)
        self.assertAlmostEqual(float(route_stats["factor"]), 1.5)
        self.assertEqual(route_stats["sampled_leg_count"], 1)
        self.assertEqual(route_stats["total_leg_count"], 1)
        self.assertTrue(route_stats["coverage_complete"])

    def test_live_traffic_sample_summarizes_recent_city_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "pm_live.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-09T15:40:00+08:00",
                        "local_date": "2026-06-09",
                        "period": "pm_peak",
                        "city": "Shanghai",
                        "dry_run": False,
                        "route_count": 21,
                        "total_osrm_duration_s": 1000.0,
                        "total_amap_duration_s": 1900.0,
                    }
                ),
                encoding="utf-8",
            )
            (sample_dir / "pm_dry_run.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-09T15:35:00+08:00",
                        "local_date": "2026-06-09",
                        "period": "pm_peak",
                        "city": "Shanghai",
                        "dry_run": True,
                        "route_count": 21,
                        "total_osrm_duration_s": 1000.0,
                        "total_amap_duration_s": 9999.0,
                    }
                ),
                encoding="utf-8",
            )
            result = planner_core.summarize_live_traffic_samples(
                service_direction="From School",
                input_records=[{"country": "China", "city": "Shanghai", "address": "School"}],
                sample_dir=sample_dir,
                now=datetime(2026, 6, 9, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["period"], "pm_peak")
        self.assertEqual(result["traffic_profile_name"], "PM Peak (Live)")
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.9)
        self.assertEqual(result["route_sample_count"], 21)


if __name__ == "__main__":
    unittest.main()
