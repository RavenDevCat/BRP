from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "ops" / "scripts"))

import report_live_traffic_budget as budget  # noqa: E402


class LiveTrafficBudgetReportTests(unittest.TestCase):
    def _baseline_args(self, tmpdir: str, *, cap: int = 1000):
        args = budget._build_sampler_args(
            city="Shanghai",
            period="am_peak",
            max_api_calls_per_run=cap,
            sample_due_routes_only=False,
            now_local_time="",
        )
        args.source = "baseline_json"
        args.baseline_dir = Path(tmpdir)
        args.baseline_path = "fixture.json"
        args.provider = "amap"
        return args

    def _write_baseline(self, tmpdir: str) -> None:
        payload = {
            "baseline_id": "fixture",
            "service_direction": "to_school",
            "routes": [
                {
                    "route_id": "r1",
                    "stops": [
                        {"stop_sequence": 1, "address": "school"},
                        {"stop_sequence": 2, "address": "home"},
                        {"stop_sequence": 3, "address": "home2"},
                    ],
                },
                {
                    "route_id": "r2",
                    "stops": [
                        {"stop_sequence": 1, "address": "school"},
                        {"stop_sequence": 2, "address": "home"},
                    ],
                },
            ],
        }
        Path(tmpdir, "fixture.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_fast_path_baseline(self, tmpdir: str) -> None:
        payload = {
            "baseline_id": "fixture",
            "service_direction": "to_school",
            "routes": [
                {
                    "route_id": "r1",
                    "raw_osrm_time_s": 100,
                    "distance_m": 1000,
                    "stops": [
                        {"stop_sequence": 1, "address": "school", "lat": 31.1, "lng": 121.1},
                        {"stop_sequence": 2, "address": "home", "lat": 31.2, "lng": 121.2},
                    ],
                },
                {
                    "route_id": "r2",
                    "historical_duration_s": 200,
                    "historical_distance_m": 2000,
                    "stops": [
                        {"stop_sequence": 1, "address": "school", "lat": 31.1, "lng": 121.1},
                        {"stop_sequence": 2, "address": "home2", "lat": 31.3, "lng": 121.3},
                    ],
                },
            ],
        }
        Path(tmpdir, "fixture.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_baseline_json_budget_is_outline_only_and_does_not_load_sampler_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_baseline(tmpdir)
            args = self._baseline_args(tmpdir)

            with mock.patch.object(budget.sampler, "_load_source", side_effect=AssertionError("should not load source")):
                row = budget.evaluate_profile("fixture", args)

        self.assertTrue(row["safe_outline_only"])
        self.assertEqual(row["route_count"], 2)
        self.assertEqual(row["candidate_route_count"], 2)
        self.assertEqual(row["estimated_api_call_count"], 2)
        self.assertEqual(row["status"], "ok")
        self.assertFalse(row["baseline_fast_path_ready"])
        self.assertEqual(row["baseline_stop_count"], 5)
        self.assertEqual(row["baseline_coordinate_stop_count"], 0)
        self.assertEqual(row["baseline_metric_route_count"], 0)

    def test_baseline_json_budget_reports_fast_path_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_fast_path_baseline(tmpdir)
            args = self._baseline_args(tmpdir)

            row = budget.evaluate_profile("fixture", args)

        self.assertTrue(row["baseline_fast_path_ready"])
        self.assertEqual(row["baseline_stop_count"], 4)
        self.assertEqual(row["baseline_coordinate_stop_count"], 4)
        self.assertEqual(row["baseline_metric_route_count"], 2)
        self.assertEqual(row["estimated_api_call_count"], 2)

    def test_baseline_json_budget_flags_universal_cap_overage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_baseline(tmpdir)
            args = self._baseline_args(tmpdir, cap=1)

            row = budget.evaluate_profile("fixture", args)

        self.assertEqual(row["estimated_api_call_count"], 2)
        self.assertEqual(row["max_api_calls_per_run"], 1)
        self.assertEqual(row["status"], "over_cap")

    def test_shanghai_sampler_args_default_to_baseline_json_profiles(self) -> None:
        with mock.patch.dict(budget.os.environ, {}, clear=True):
            am_args = budget._build_sampler_args(
                city="Shanghai",
                period="am_peak",
                max_api_calls_per_run=1000,
                sample_due_routes_only=False,
                now_local_time="",
            )
            pm_args = budget._build_sampler_args(
                city="Shanghai",
                period="pm_peak",
                max_api_calls_per_run=1000,
                sample_due_routes_only=False,
                now_local_time="",
            )

        self.assertEqual(am_args.source, "baseline_json")
        self.assertEqual(am_args.baseline_path, "demh/shanghai_demh_to_school_current_plan.json")
        self.assertEqual(pm_args.source, "baseline_json")
        self.assertEqual(pm_args.baseline_path, "demh/shanghai_demh_from_school_current_plan.json")

    def test_profile_specs_can_filter_single_active_profile(self) -> None:
        specs = budget._profile_specs(False, [" shanghai_pm_peak "])

        self.assertEqual(specs, (("shanghai_pm_peak", "Shanghai", "pm_peak"),))

    def test_profile_specs_requires_include_off_peak_for_optional_profile(self) -> None:
        self.assertEqual(budget._profile_specs(False, ["shanghai_off_peak"]), ())
        self.assertEqual(budget._profile_specs(True, ["shanghai_off_peak"]), (("shanghai_off_peak", "Shanghai", "off_peak"),))

    def test_profile_specs_include_kr_required_profiles_when_selected(self) -> None:
        specs = budget._profile_specs(False, ["kr_am_peak", "kr_pm_peak", "kr_off_peak"])

        self.assertEqual(
            specs,
            (
                ("kr_am_peak", "Seoul", "am_peak"),
                ("kr_pm_peak", "Seoul", "pm_peak"),
                ("kr_off_peak", "Seoul", "off_peak"),
            ),
        )

    def test_kr_sampler_args_use_kakao_baseline_defaults(self) -> None:
        with mock.patch.dict(
            budget.os.environ,
            {
                "BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH": "kr/to_school.json",
                "BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH": "kr/from_school.json",
            },
            clear=False,
        ):
            am_args = budget._build_sampler_args(
                city="Seoul",
                period="am_peak",
                max_api_calls_per_run=500,
                sample_due_routes_only=False,
                now_local_time="",
            )
            off_peak_args = budget._build_sampler_args(
                city="Seoul",
                period="off_peak",
                max_api_calls_per_run=500,
                sample_due_routes_only=False,
                now_local_time="",
            )

        self.assertEqual(am_args.market, "KR")
        self.assertEqual(am_args.city, "Seoul")
        self.assertEqual(am_args.provider, "kakao_navi")
        self.assertEqual(am_args.baseline_path, "kr/to_school.json")
        self.assertEqual(am_args.target_arrival_local_time, "08:00")
        self.assertEqual(off_peak_args.baseline_path, "kr/to_school.json")
        self.assertEqual(off_peak_args.departure_local_time, "11:00")

    def test_build_report_records_missing_profile_without_evaluating(self) -> None:
        args = argparse.Namespace(
            include_off_peak=False,
            profile=[" missing_profile "],
            max_api_calls_per_run=1000,
            sample_due_routes_only=False,
            now_local_time="",
        )

        with mock.patch.object(budget, "evaluate_profile", side_effect=AssertionError("should not evaluate missing profiles")):
            report = budget.build_report(args)

        self.assertEqual(report["selected_profiles"], ["missing_profile"])
        self.assertEqual(report["missing_profiles"], ["missing_profile"])
        self.assertEqual(report["profiles"], [])


if __name__ == "__main__":
    unittest.main()
