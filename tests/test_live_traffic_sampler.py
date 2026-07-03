from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import live_traffic_sampler  # noqa: E402
from quota_store_sqlite import SqliteQuotaStore  # noqa: E402
from runtime_store_sqlite import SqliteRuntimeStore  # noqa: E402


class LiveTrafficSamplerTests(unittest.TestCase):
    def test_balanced_kakao_chunks_respect_waypoint_limit(self) -> None:
        points = [{"node_id": index, "lat": 37.0 + index, "lng": 127.0 + index} for index in range(8)]

        chunks = live_traffic_sampler._balanced_route_point_chunks(points, max_intermediates=5)

        self.assertEqual([[point["node_id"] for point in chunk] for chunk in chunks], [[0, 1, 2, 3, 4], [4, 5, 6, 7]])
        self.assertTrue(all(len(chunk[1:-1]) <= 5 for chunk in chunks))

    def test_kakao_dry_run_counts_balanced_chunks(self) -> None:
        points = [{"node_id": index, "lat": 37.0 + index, "lng": 127.0 + index} for index in range(8)]
        args = argparse.Namespace(kakao_navi_max_waypoints=5, kakao_navi_inter_segment_dwell_seconds=0)

        result = live_traffic_sampler._dry_run_provider_result(
            live_traffic_sampler.KAKAO_NAVI_PROVIDER,
            {"distance_m": 7000},
            points,
            700,
            args,
        )

        self.assertEqual(result["api_call_count"], 2)
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(result["api_duration_s"], 700)
        self.assertEqual(result["provider"], live_traffic_sampler.KAKAO_NAVI_PROVIDER)

    def test_universal_budget_counts_amap_one_call_per_route(self) -> None:
        args = argparse.Namespace(
            max_api_calls_per_run=2,
            google_routes_max_intermediates=25,
            kakao_navi_max_waypoints=5,
        )
        candidates = [
            ({}, "r1", 2, 100.0, {}, [{"node_id": 0}, {"node_id": 1}]),
            ({}, "r2", 2, 100.0, {}, [{"node_id": 0}, {"node_id": 1}]),
            ({}, "r3", 2, 100.0, {}, [{"node_id": 0}, {"node_id": 1}]),
        ]

        estimated = live_traffic_sampler._estimated_api_call_count("amap", candidates, args)

        self.assertEqual(estimated, 3)
        with self.assertRaisesRegex(RuntimeError, "above per-run cap 2"):
            live_traffic_sampler._enforce_api_call_budget("amap", estimated, args)

    def test_universal_budget_can_be_disabled(self) -> None:
        args = argparse.Namespace(max_api_calls_per_run=0)

        live_traffic_sampler._enforce_api_call_budget("amap", 5000, args)

    def test_google_routes_usage_migrates_legacy_json_to_sqlite(self) -> None:
        now = datetime(2026, 6, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_path = Path(tmpdir) / "google_routes_usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "2026-06": {
                            "attempted": 2,
                            "succeeded": 1,
                            "provider": live_traffic_sampler.GOOGLE_ROUTES_PROVIDER,
                            "sku_estimate": "routes_compute_routes_pro",
                        },
                        "2026-06-21": {"attempted": 1},
                    }
                ),
                encoding="utf-8",
            )
            quota_db_path = Path(tmpdir) / "quota.sqlite"
            args = argparse.Namespace(
                dry_run=False,
                google_routes_usage_path=usage_path,
                google_routes_monthly_safety_cap=5,
                google_routes_daily_cap=5,
                quota_db_path=quota_db_path,
            )

            live_traffic_sampler._reserve_google_routes_usage(args, 2, now=now)
            live_traffic_sampler._mark_google_routes_usage_result(args, now=now, succeeded=True)

            store = SqliteQuotaStore(quota_db_path)
            month_usage = store.get_usage(
                live_traffic_sampler.GOOGLE_ROUTES_PROVIDER,
                live_traffic_sampler.GOOGLE_ROUTES_USAGE_COUNTER,
                "month",
                "2026-06",
            )
            day_usage = store.get_usage(
                live_traffic_sampler.GOOGLE_ROUTES_PROVIDER,
                live_traffic_sampler.GOOGLE_ROUTES_USAGE_COUNTER,
                "day",
                "2026-06-21",
            )
            self.assertEqual(month_usage["attempted"], 4)
            self.assertEqual(month_usage["succeeded"], 2)
            self.assertEqual(day_usage["attempted"], 3)
            self.assertEqual(day_usage["succeeded"], 1)

            with self.assertRaisesRegex(RuntimeError, "Google Routes monthly safety cap would be exceeded"):
                live_traffic_sampler._reserve_google_routes_usage(args, 2, now=now)

    def test_kakao_navi_usage_migrates_legacy_json_to_sqlite(self) -> None:
        now = datetime(2026, 6, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_path = Path(tmpdir) / "kakao_navi_usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "2026-06": {
                            "attempted": 3,
                            "failed": 1,
                            "provider": live_traffic_sampler.KAKAO_NAVI_PROVIDER,
                            "sku_estimate": "kakao_navi_future_directions",
                        },
                        "2026-06-21": {"attempted": 1},
                    }
                ),
                encoding="utf-8",
            )
            quota_db_path = Path(tmpdir) / "quota.sqlite"
            args = argparse.Namespace(
                dry_run=False,
                kakao_navi_usage_path=usage_path,
                kakao_navi_monthly_safety_cap=6,
                kakao_navi_daily_cap=6,
                quota_db_path=quota_db_path,
            )

            live_traffic_sampler._reserve_kakao_navi_usage(args, 2, now=now)
            live_traffic_sampler._mark_kakao_navi_usage_result(args, now=now, succeeded=False)

            store = SqliteQuotaStore(quota_db_path)
            month_usage = store.get_usage(
                live_traffic_sampler.KAKAO_NAVI_PROVIDER,
                live_traffic_sampler.KAKAO_NAVI_USAGE_COUNTER,
                "month",
                "2026-06",
            )
            day_usage = store.get_usage(
                live_traffic_sampler.KAKAO_NAVI_PROVIDER,
                live_traffic_sampler.KAKAO_NAVI_USAGE_COUNTER,
                "day",
                "2026-06-21",
            )
            self.assertEqual(month_usage["attempted"], 5)
            self.assertEqual(month_usage["failed"], 2)
            self.assertEqual(day_usage["attempted"], 3)
            self.assertEqual(day_usage["failed"], 1)

    def test_raw_osrm_seconds_accepts_current_route_audit_fields(self) -> None:
        self.assertEqual(
            live_traffic_sampler._raw_osrm_seconds({"time_s": 321}),
            321,
        )
        self.assertEqual(
            live_traffic_sampler._raw_osrm_seconds({"leg_details": [{"duration_s": 100}, {"duration_s": 50}]}),
            150,
        )

    def test_load_job_requires_sqlite_for_job_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jobs_dir = root / "jobs"
            jobs_dir.mkdir()
            (jobs_dir / "old.json").write_text(
                json.dumps({"job_id": "old", "status": "succeeded"}),
                encoding="utf-8",
            )
            sqlite_path = root / "runtime.sqlite"

            with self.assertRaisesRegex(FileNotFoundError, "SQLite"):
                live_traffic_sampler._load_job("old", jobs_dir, sqlite_path)

            SqliteRuntimeStore(sqlite_path).upsert_job({"job_id": "old", "status": "succeeded"})

            self.assertEqual(
                live_traffic_sampler._load_job("old", jobs_dir, sqlite_path)["job_id"],
                "old",
            )

    def test_to_school_schedule_backsolves_from_target_arrival(self) -> None:
        args = argparse.Namespace(
            target_arrival_local_time="08:00",
            departure_local_time="",
            departure_multiplier=1.0,
            sample_due_routes_only=False,
            route_due_window_minutes=5,
        )

        schedule = live_traffic_sampler._route_sampling_schedule(
            args,
            {"route_id": "1-toschool"},
            1800,
            datetime(2026, 6, 15, 7, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            {},
        )

        self.assertEqual(schedule["schedule_source"], "target_arrival_fallback")
        self.assertTrue(str(schedule["target_arrival_local_time"]).endswith("08:00:00+09:00"))
        self.assertTrue(str(schedule["planned_departure_local_time"]).endswith("07:30:00+09:00"))

    def test_from_school_schedule_uses_departure_anchor(self) -> None:
        args = argparse.Namespace(
            target_arrival_local_time="",
            departure_local_time="15:40",
            departure_multiplier=1.0,
            sample_due_routes_only=False,
            route_due_window_minutes=5,
        )

        schedule = live_traffic_sampler._route_sampling_schedule(
            args,
            {"route_id": "1-fromschool"},
            1800,
            datetime(2026, 6, 15, 15, 40, tzinfo=ZoneInfo("Asia/Seoul")),
            {},
        )

        self.assertEqual(schedule["schedule_source"], "period_departure")
        self.assertIsNone(schedule["target_arrival_local_time"])
        self.assertTrue(str(schedule["planned_departure_local_time"]).endswith("15:40:00+09:00"))

    def test_baseline_json_with_coordinates_avoids_geocode_and_osrm_rebuild(self) -> None:
        baseline = {
            "baseline_id": "fixture",
            "source_title": "Fixture",
            "service_direction": "to_school",
            "source_run_sha256": "abc",
            "routes": [
                {
                    "route_id": "route-1",
                    "bus_type": "Large Bus",
                    "historical_duration_s": 1234.5,
                    "historical_distance_m": 6789.0,
                    "stops": [
                        {
                            "stop_sequence": 1,
                            "address": "A",
                            "country": "China",
                            "city": "Suzhou",
                            "lat": 31.1,
                            "lng": 120.1,
                            "passenger_count": 2,
                        },
                        {
                            "stop_sequence": 2,
                            "address": "School",
                            "country": "China",
                            "city": "Suzhou",
                            "lat": 31.2,
                            "lng": 120.2,
                            "is_school": True,
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            args = argparse.Namespace(baseline_path=str(path), baseline_dir=Path(tmpdir))

            with (
                mock.patch.object(live_traffic_sampler.planner, "geocode_records", side_effect=AssertionError("geocode")),
                mock.patch.object(live_traffic_sampler, "_route_osrm_metrics", side_effect=AssertionError("osrm")),
            ):
                _payload, metadata, points, routes = live_traffic_sampler._scenario_from_baseline_json(args)

        self.assertEqual(metadata["baseline_load_mode"], "coordinates")
        self.assertEqual(metadata["geocode_warning_count"], 0)
        self.assertEqual([point["node_id"] for point in points], [0, 1])
        self.assertEqual(points[0]["passenger_count"], 2)
        self.assertEqual(routes[0]["nodes"], [0, 1])
        self.assertEqual(routes[0]["raw_osrm_time_s"], 1234.5)
        self.assertEqual(routes[0]["distance_m"], 6789.0)
        self.assertEqual(routes[0]["baseline_metric_source"], "baseline_json_coordinates")

    def test_baseline_json_with_coordinates_can_fill_missing_metrics_from_osrm(self) -> None:
        baseline = {
            "baseline_id": "fixture",
            "source_title": "Fixture",
            "service_direction": "to_school",
            "routes": [
                {
                    "route_id": "route-1",
                    "stops": [
                        {"stop_sequence": 1, "address": "A", "lat": 31.1, "lng": 120.1},
                        {"stop_sequence": 2, "address": "School", "lat": 31.2, "lng": 120.2, "is_school": True},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            args = argparse.Namespace(baseline_path=str(path), baseline_dir=Path(tmpdir))

            with (
                mock.patch.object(live_traffic_sampler.planner, "geocode_records", side_effect=AssertionError("geocode")),
                mock.patch.object(
                    live_traffic_sampler,
                    "_route_osrm_metrics",
                    return_value=(6789.0, 1234.5, [{"from_node": 0, "to_node": 1}]),
                ) as osrm_metrics,
            ):
                _payload, metadata, points, routes = live_traffic_sampler._scenario_from_baseline_json(args)

        self.assertEqual(metadata["baseline_load_mode"], "coordinates")
        self.assertEqual([point["node_id"] for point in points], [0, 1])
        osrm_metrics.assert_called_once()
        self.assertEqual(routes[0]["raw_osrm_time_s"], 1234.5)
        self.assertEqual(routes[0]["distance_m"], 6789.0)
        self.assertEqual(routes[0]["leg_details"], [{"from_node": 0, "to_node": 1}])
        self.assertEqual(routes[0]["baseline_metric_source"], "baseline_json_coordinates_osrm")


if __name__ == "__main__":
    unittest.main()
