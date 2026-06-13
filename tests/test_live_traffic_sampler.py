from __future__ import annotations

import argparse
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import live_traffic_sampler  # noqa: E402


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

    def test_raw_osrm_seconds_accepts_current_route_audit_fields(self) -> None:
        self.assertEqual(
            live_traffic_sampler._raw_osrm_seconds({"time_s": 321}),
            321,
        )
        self.assertEqual(
            live_traffic_sampler._raw_osrm_seconds({"leg_details": [{"duration_s": 100}, {"duration_s": 50}]}),
            150,
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


if __name__ == "__main__":
    unittest.main()
