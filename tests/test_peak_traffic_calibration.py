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

    def test_attributed_traffic_profile_uses_route_similarity_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "pm_routes.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-09T15:40:00+08:00",
                        "local_date": "2026-06-09",
                        "period": "pm_peak",
                        "country": "China",
                        "city": "Shanghai",
                        "dry_run": False,
                        "route_count": 1,
                        "total_osrm_duration_s": 200.0,
                        "total_amap_duration_s": 320.0,
                        "routes": [
                            {
                                "route_id": "sample-r1",
                                "provider": "amap",
                                "stop_count": 2,
                                "osrm_duration_s": 200.0,
                                "amap_duration_s": 320.0,
                                "factor": 1.6,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
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
                {
                    "node_id": 2,
                    "country": "China",
                    "city": "Shanghai",
                    "address": "Stop B",
                    "lat": 31.2,
                    "lng": 121.2,
                },
            ]
            current_plan = {
                "service_direction": "From School",
                "stops": [
                    {"stop_id": "R1__0", **points[0]},
                    {"stop_id": "R1__1", **points[1]},
                    {"stop_id": "R1__2", **points[2]},
                ],
                "assignments": [
                    {"route_id": "R1", "stop_id": "R1__0", "stop_sequence": 1, "bus_type": "Bus"},
                    {"route_id": "R1", "stop_id": "R1__1", "stop_sequence": 2, "bus_type": "Bus"},
                    {"route_id": "R1", "stop_id": "R1__2", "stop_sequence": 3, "bus_type": "Bus"},
                ],
                "fleet": [{"bus_type": "Bus", "seat_count": 42}],
            }
            result = planner_core.resolve_attributed_traffic_profile(
                FakePlanner(),
                current_plan,
                points,
                [{"country": "China", "city": "Shanghai", "address": "School", "passenger_count": 0}],
                planner_core.PlannerConfig(
                    service_direction="From School",
                    traffic_coefficient_mode="attributed",
                ),
                "PM Peak",
                1.75,
                "Shanghai default",
                sample_dir=sample_dir,
                now=datetime(2026, 6, 9, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertTrue(result["succeeded"])
        self.assertEqual(result["mode"], "attributed")
        self.assertEqual(result["method"], "route_similarity")
        self.assertEqual(result["traffic_profile_name"], "PM Peak (Attributed)")
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.6)
        self.assertEqual(result["observed_route_sample_count"], 1)
        self.assertEqual(result["attributed_route_count"], 1)

    def test_attribution_context_reports_geo_ready_sample_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "pm_routes.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-09T15:40:00+08:00",
                        "local_date": "2026-06-09",
                        "period": "pm_peak",
                        "country": "China",
                        "city": "Shanghai",
                        "dry_run": False,
                        "route_count": 2,
                        "total_osrm_duration_s": 450.0,
                        "total_api_duration_s": 820.0,
                        "routes": [
                            {
                                "route_id": "geo-r1",
                                "stop_count": 2,
                                "osrm_duration_s": 200.0,
                                "amap_duration_s": 320.0,
                                "factor": 1.6,
                                "route_fingerprint": {
                                    "cell_count": 2,
                                    "cells": ["3100:12100", "3101:12101"],
                                },
                            },
                            {
                                "route_id": "scale-r2",
                                "stop_count": 3,
                                "osrm_duration_s": 250.0,
                                "amap_duration_s": 500.0,
                                "factor": 2.0,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            context = planner_core.build_traffic_attribution_context(
                [{"country": "China", "city": "Shanghai", "address": "School"}],
                planner_core.PlannerConfig(
                    service_direction="From School",
                    traffic_coefficient_mode="attributed",
                ),
                sample_dir=sample_dir,
                now=datetime(2026, 6, 9, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertTrue(context["succeeded"])
        self.assertEqual(context["observed_route_sample_count"], 2)
        self.assertEqual(context["geo_route_sample_count"], 1)
        self.assertEqual(context["scale_only_route_sample_count"], 1)
        self.assertAlmostEqual(float(context["geo_route_sample_ratio"]), 0.5)
        self.assertTrue(context["geo_ready"])

    def test_attributed_traffic_profile_is_disabled_in_legacy_mode(self) -> None:
        result = planner_core.resolve_attributed_traffic_profile(
            FakePlanner(),
            {},
            [],
            [{"country": "China", "city": "Shanghai", "address": "School", "passenger_count": 0}],
            planner_core.PlannerConfig(traffic_coefficient_mode="legacy"),
            "PM Peak",
            1.75,
            "Shanghai default",
        )

        self.assertFalse(result["enabled"])
        self.assertEqual(result["mode"], "legacy")
        self.assertEqual(result["reason"], "legacy_mode")

    def test_route_level_attribution_rewrites_optimized_route_duration(self) -> None:
        routes = [
            {
                "vehicle_id": 1,
                "bus_type_name": "Bus",
                "nodes": [0, 1, 2],
                "stop_count": 2,
                "raw_osrm_time_s": 200.0,
                "traffic_buffer_factor": 1.0,
                "traffic_adjusted_drive_time_s": 200,
                "stop_service_time_s": 60,
                "time_s": 260,
                "leg_details": [
                    {
                        "from_node": 0,
                        "to_node": 1,
                        "raw_osrm_duration_s": 100,
                        "traffic_adjusted_duration_s": 100,
                        "stop_service_s": 60,
                        "duration_s": 160,
                    },
                    {
                        "from_node": 1,
                        "to_node": 2,
                        "raw_osrm_duration_s": 100,
                        "traffic_adjusted_duration_s": 100,
                        "stop_service_s": 0,
                        "duration_s": 100,
                    },
                ],
            }
        ]
        estimates = planner_core.apply_attributed_traffic_to_scenario_routes(
            routes,
            {
                "enabled": True,
                "candidates": [
                    {
                        "route_id": "sample-r1",
                        "source_id": "fixture",
                        "factor": 1.6,
                        "osrm_duration_s": 200.0,
                        "stop_count": 2,
                    }
                ],
                "observed_route_sample_count": 1,
            },
            fallback_multiplier=1.75,
            scenario_label="Free optimization baseline",
        )

        self.assertEqual(len(estimates), 1)
        self.assertAlmostEqual(float(routes[0]["traffic_buffer_factor"]), 1.6)
        self.assertEqual(routes[0]["traffic_adjusted_drive_time_s"], 320)
        self.assertEqual(routes[0]["time_s"], 380)
        self.assertEqual(routes[0]["leg_details"][0]["traffic_adjusted_duration_s"], 160)
        self.assertEqual(routes[0]["leg_details"][0]["duration_s"], 220)
        self.assertEqual(routes[0]["traffic_time_source"], "Attributed route-level traffic samples")

    def test_route_traffic_fingerprint_uses_corridor_geometry(self) -> None:
        points = [
            {"node_id": 0, "lat": 31.0000, "lng": 121.0000, "is_depot": True},
            {"node_id": 1, "lat": 31.0100, "lng": 121.0100},
            {"node_id": 2, "lat": 31.0200, "lng": 121.0200},
        ]
        route = {
            "nodes": [0, 1, 2],
            "leg_details": [
                {"geometry": [[31.0000, 121.0000], [31.0050, 121.0050], [31.0100, 121.0100]]},
                {"geometry": [[31.0100, 121.0100], [31.0150, 121.0150], [31.0200, 121.0200]]},
            ],
        }

        fingerprint = planner_core.build_route_traffic_fingerprint(route, all_points=points)

        self.assertIsNotNone(fingerprint)
        assert fingerprint is not None
        self.assertGreaterEqual(fingerprint["cell_count"], 3)
        self.assertGreaterEqual(fingerprint["geometry_point_count"], 6)
        self.assertIn("center", fingerprint)
        self.assertIn("bbox", fingerprint)
        self.assertIsNotNone(fingerprint["school_bearing_sector"])

    def test_geo_route_similarity_prefers_same_corridor_sample(self) -> None:
        target_fingerprint = {
            "cells": ["3100:12100", "3101:12101", "3102:12102"],
            "corridor_cells": ["3100:12100", "3101:12101", "3102:12102"],
            "stop_cells": ["3100:12100", "3102:12102"],
            "center": {"lat": 31.01, "lng": 121.01},
            "bbox": {"min_lat": 31.0, "max_lat": 31.02, "min_lng": 121.0, "max_lng": 121.02},
            "bearing_sector": 2,
            "school_bearing_sector": 2,
        }
        near_fingerprint = {
            "cells": ["3100:12100", "3101:12101", "3102:12102"],
            "corridor_cells": ["3100:12100", "3101:12101", "3102:12102"],
            "stop_cells": ["3100:12100", "3102:12102"],
            "center": {"lat": 31.011, "lng": 121.011},
            "bbox": {"min_lat": 31.0, "max_lat": 31.021, "min_lng": 121.0, "max_lng": 121.021},
            "bearing_sector": 2,
            "school_bearing_sector": 2,
        }
        far_fingerprint = {
            "cells": ["3200:12200", "3201:12201", "3202:12202"],
            "corridor_cells": ["3200:12200", "3201:12201", "3202:12202"],
            "stop_cells": ["3200:12200", "3202:12202"],
            "center": {"lat": 32.01, "lng": 122.01},
            "bbox": {"min_lat": 32.0, "max_lat": 32.02, "min_lng": 122.0, "max_lng": 122.02},
            "bearing_sector": 10,
            "school_bearing_sector": 10,
        }

        estimate = planner_core._route_attributed_factor(
            {
                "route_id": "new",
                "osrm_duration_s": 1200.0,
                "stop_count": 5,
                "route_fingerprint": target_fingerprint,
            },
            [
                {
                    "route_id": "far",
                    "factor": 3.0,
                    "osrm_duration_s": 1200.0,
                    "stop_count": 5,
                    "route_fingerprint": far_fingerprint,
                },
                {
                    "route_id": "near",
                    "factor": 1.4,
                    "osrm_duration_s": 1200.0,
                    "stop_count": 5,
                    "route_fingerprint": near_fingerprint,
                },
            ],
        )

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate["method"], "geo_route_similarity")
        self.assertEqual(estimate["top_matches"][0]["route_id"], "near")
        self.assertLess(float(estimate["factor"]), 2.1)

    def test_poor_geo_match_does_not_fallback_to_scale_similarity(self) -> None:
        target_fingerprint = {
            "cells": ["3100:12100", "3101:12101"],
            "corridor_cells": ["3100:12100", "3101:12101"],
            "stop_cells": ["3100:12100"],
            "center": {"lat": 31.01, "lng": 121.01},
            "bbox": {"min_lat": 31.0, "max_lat": 31.02, "min_lng": 121.0, "max_lng": 121.02},
            "bearing_sector": 2,
            "school_bearing_sector": 2,
        }
        far_fingerprint = {
            "cells": ["3900:11600", "3901:11601"],
            "corridor_cells": ["3900:11600", "3901:11601"],
            "stop_cells": ["3900:11600"],
            "center": {"lat": 39.91, "lng": 116.41},
            "bbox": {"min_lat": 39.9, "max_lat": 39.92, "min_lng": 116.4, "max_lng": 116.42},
            "bearing_sector": 10,
            "school_bearing_sector": 10,
        }

        estimate = planner_core._route_attributed_factor(
            {
                "route_id": "new",
                "osrm_duration_s": 1200.0,
                "stop_count": 5,
                "route_fingerprint": target_fingerprint,
            },
            [
                {
                    "route_id": "far",
                    "factor": 3.0,
                    "osrm_duration_s": 1200.0,
                    "stop_count": 5,
                    "route_fingerprint": far_fingerprint,
                },
            ],
        )

        self.assertIsNone(estimate)

    def test_scale_similarity_remains_available_for_legacy_samples_without_geo(self) -> None:
        estimate = planner_core._route_attributed_factor(
            {
                "route_id": "new",
                "osrm_duration_s": 1200.0,
                "stop_count": 5,
            },
            [
                {
                    "route_id": "legacy",
                    "factor": 1.8,
                    "osrm_duration_s": 1250.0,
                    "stop_count": 5,
                },
            ],
        )

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate["method"], "route_similarity")
        self.assertEqual(estimate["quality_reason"], "scale_similarity_only")

    def test_traffic_route_estimate_summary_counts_methods_and_reasons(self) -> None:
        summary = planner_core._traffic_route_estimate_summary(
            [
                {"method": "geo_route_similarity", "quality_reason": "geo_threshold_passed"},
                {"method": "route_similarity", "quality_reason": "scale_similarity_only"},
                {"method": "fallback", "quality_reason": "no_similar_route_sample"},
            ]
        )

        self.assertEqual(summary["geo_attributed_route_count"], 1)
        self.assertEqual(summary["route_similarity_route_count"], 1)
        self.assertEqual(summary["fallback_route_count"], 1)
        self.assertEqual(summary["method_counts"]["geo_route_similarity"], 1)
        self.assertEqual(summary["quality_reason_counts"]["scale_similarity_only"], 1)

    def test_live_traffic_sample_matches_korea_weekday_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "seoul_mon.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-15T06:00:00+09:00",
                        "local_date": "2026-06-15",
                        "sample_date": "2026-06-15",
                        "sample_weekday": "mon",
                        "period": "am_peak",
                        "country": "South Korea",
                        "city": "Seoul",
                        "provider": "google_routes",
                        "dry_run": False,
                        "route_count": 23,
                        "total_osrm_duration_s": 1000.0,
                        "total_api_duration_s": 1600.0,
                    }
                ),
                encoding="utf-8",
            )
            (sample_dir / "seoul_tue.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-15T06:05:00+09:00",
                        "local_date": "2026-06-16",
                        "sample_date": "2026-06-16",
                        "sample_weekday": "tue",
                        "period": "am_peak",
                        "country": "South Korea",
                        "city": "Seoul",
                        "provider": "google_routes",
                        "dry_run": False,
                        "route_count": 23,
                        "total_osrm_duration_s": 1000.0,
                        "total_api_duration_s": 3000.0,
                    }
                ),
                encoding="utf-8",
            )
            result = planner_core.summarize_live_traffic_samples(
                service_direction="To School",
                input_records=[{"country": "South Korea", "city": "Seoul", "address": "School"}],
                sample_dir=sample_dir,
                now=datetime(2026, 6, 15, 8, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["period"], "am_peak")
        self.assertEqual(result["providers"], ["google_routes"])
        self.assertEqual(result["sample_weekday"], "mon")
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.6)

    def test_live_traffic_sample_keeps_korea_weekday_profile_available_on_weekends(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "seoul_next_mon.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-13T08:00:00+09:00",
                        "local_date": "2026-06-15",
                        "sample_date": "2026-06-15",
                        "sample_weekday": "mon",
                        "period": "pm_peak",
                        "country": "South Korea",
                        "city": "Seoul",
                        "provider": "google_routes",
                        "dry_run": False,
                        "route_count": 23,
                        "total_osrm_duration_s": 1000.0,
                        "total_api_duration_s": 1700.0,
                    }
                ),
                encoding="utf-8",
            )
            result = planner_core.summarize_live_traffic_samples(
                service_direction="From School",
                input_records=[{"country": "South Korea", "city": "Seoul", "address": "School"}],
                sample_dir=sample_dir,
                now=datetime(2026, 6, 13, 10, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["period"], "pm_peak")
        self.assertEqual(result["sample_weekday"], "mon")
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.7)

    def test_korea_live_traffic_samples_match_metro_profile_across_city_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "seoul_mon.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-15T06:00:00+09:00",
                        "local_date": "2026-06-15",
                        "sample_date": "2026-06-15",
                        "sample_weekday": "mon",
                        "period": "am_peak",
                        "country": "South Korea",
                        "city": "Seoul",
                        "provider": "kakao_navi",
                        "dry_run": False,
                        "route_count": 23,
                        "total_osrm_duration_s": 1000.0,
                        "total_api_duration_s": 1600.0,
                    }
                ),
                encoding="utf-8",
            )
            result = planner_core.summarize_live_traffic_samples(
                service_direction="To School",
                input_records=[{"country": "KR", "city": "Incheon", "address": "School"}],
                sample_dir=sample_dir,
                now=datetime(2026, 6, 15, 8, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["city"], "SEOUL METRO")
        self.assertEqual(result["providers"], ["kakao_navi"])
        self.assertAlmostEqual(float(result["traffic_time_multiplier"]), 1.6)

    def test_korea_route_attribution_context_uses_kakao_metro_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "seoul_pm_routes.json").write_text(
                json.dumps(
                    {
                        "measured_at": "2026-06-15T15:40:00+09:00",
                        "local_date": "2026-06-15",
                        "sample_date": "2026-06-15",
                        "sample_weekday": "mon",
                        "period": "pm_peak",
                        "country": "South Korea",
                        "city": "Seoul",
                        "provider": "kakao_navi",
                        "dry_run": False,
                        "route_count": 1,
                        "total_osrm_duration_s": 1000.0,
                        "total_api_duration_s": 1800.0,
                        "routes": [
                            {
                                "route_id": "sample-r1",
                                "provider": "kakao_navi",
                                "stop_count": 4,
                                "osrm_duration_s": 1000.0,
                                "api_duration_s": 1800.0,
                                "factor": 1.8,
                                "route_fingerprint": {
                                    "cell_count": 2,
                                    "cells": ["3750:12690", "3751:12691"],
                                    "corridor_cells": ["3750:12690", "3751:12691"],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            context = planner_core.build_traffic_attribution_context(
                [{"country": "South Korea", "city": "Gimpo", "address": "School"}],
                planner_core.PlannerConfig(
                    service_direction="From School",
                    traffic_coefficient_mode="attributed",
                ),
                sample_dir=sample_dir,
                now=datetime(2026, 6, 15, 16, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            )

        self.assertTrue(context["succeeded"])
        self.assertEqual(context["city"], "SEOUL METRO")
        self.assertEqual(context["observed_route_sample_count"], 1)
        self.assertEqual(context["geo_route_sample_count"], 1)
        self.assertEqual(context["candidates"][0]["provider"], "kakao_navi")


if __name__ == "__main__":
    unittest.main()
