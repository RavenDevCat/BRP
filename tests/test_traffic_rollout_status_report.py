from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "ops" / "scripts" / "report_traffic_rollout_status.py"
SCRIPT_DIR = SCRIPT_PATH.parent
sys.path.insert(0, str(SCRIPT_DIR))

spec = importlib.util.spec_from_file_location("report_traffic_rollout_status", SCRIPT_PATH)
assert spec is not None
report_traffic_rollout_status = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(report_traffic_rollout_status)


class TrafficRolloutStatusReportTests(unittest.TestCase):
    def test_summarizes_waiting_status_when_required_profile_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = report_traffic_rollout_status.build_status(
                sample_dir=Path(tmpdir),
                min_measured_at="2026-06-18T00:00:00+08:00",
                profiles=[("CN", "Shanghai", "am_peak")],
                min_geo_ratio=1.0,
                include_timers=False,
                include_osrm=False,
            )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["rollout_gate"]["status"], "failed")
        self.assertEqual(report["rollout_gate"]["failure_reason_counts"], {"missing_sample_group": 1})
        self.assertEqual(report["rollout_gate"]["missing_profiles"][0]["profile"], "CN:Shanghai:am_peak")
        self.assertEqual(report["rollout_gate"]["missing_profiles"][0]["reason"], "missing_sample_group")
        self.assertEqual(report["timers"]["problem_count"], 0)
        self.assertEqual(report["services"]["problem_count"], 0)
        self.assertTrue(report["osrm_manager"]["skipped"])

    def test_reports_ready_when_gate_passes_and_no_ops_problems(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:25:55+08:00",
                        "routes": [
                            {
                                "route_id": "geo",
                                "route_fingerprint": {"cell_count": 2, "cells": ["a", "b"]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = report_traffic_rollout_status.build_status(
                sample_dir=sample_dir,
                min_measured_at="2026-06-18T00:00:00+08:00",
                profiles=[("CN", "Shanghai", "am_peak")],
                min_geo_ratio=1.0,
                include_timers=False,
                include_osrm=False,
            )

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["rollout_gate"]["passed_requirement_count"], 1)
        self.assertIn("representative route audits", report["next_step"])

    def test_parse_systemctl_show_properties(self) -> None:
        parsed = report_traffic_rollout_status._parse_show_properties(
            "ActiveState=active\nSubState=waiting\nNoEquals\nResult=success\n"
        )

        self.assertEqual(parsed["ActiveState"], "active")
        self.assertEqual(parsed["SubState"], "waiting")
        self.assertEqual(parsed["Result"], "success")
        self.assertNotIn("NoEquals", parsed)

    def test_timer_status_adds_local_time_and_next_relevant_timer(self) -> None:
        class Result:
            returncode = 0
            stdout = (
                "ActiveState=active\n"
                "SubState=waiting\n"
                "Result=success\n"
                "NextElapseUSecRealtime=Wed 2026-06-17 22:20:00 UTC\n"
                "LastTriggerUSec=Tue 2026-06-16 22:20:01 UTC\n"
            )
            stderr = ""

        original = report_traffic_rollout_status._run_command
        try:
            report_traffic_rollout_status._run_command = lambda *_args, **_kwargs: Result()
            rows = report_traffic_rollout_status.collect_timer_status(
                ["brp-live-traffic-am.timer"],
                local_tz=ZoneInfo("Asia/Shanghai"),
                now=datetime(2026, 6, 17, 18, 20, tzinfo=timezone.utc),
            )
        finally:
            report_traffic_rollout_status._run_command = original

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_elapse_local"], "2026-06-18T06:20:00+08:00")
        self.assertEqual(rows[0]["last_trigger_local"], "2026-06-17T06:20:01+08:00")
        self.assertEqual(rows[0]["seconds_until_next_elapse"], 14400)
        next_timer = report_traffic_rollout_status._next_relevant_timer(rows)
        self.assertIsNotNone(next_timer)
        assert next_timer is not None
        self.assertEqual(next_timer["unit"], "brp-live-traffic-am.timer")

    def test_service_status_flags_nonzero_exec_main_status(self) -> None:
        class Result:
            returncode = 0
            stdout = (
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "Result=success\n"
                "ExecMainStatus=1\n"
                "ExecMainCode=1\n"
                "ExecMainStartTimestamp=today\n"
                "ExecMainExitTimestamp=today\n"
            )
            stderr = ""

        original = report_traffic_rollout_status._run_command
        try:
            report_traffic_rollout_status._run_command = lambda *_args, **_kwargs: Result()
            rows = report_traffic_rollout_status.collect_service_status(["brp-live-traffic-pm.service"])
        finally:
            report_traffic_rollout_status._run_command = original

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["problem"])
        self.assertEqual(rows[0]["exec_main_status"], 1)

    def test_budget_status_summarizes_api_and_fast_path_problems(self) -> None:
        original = report_traffic_rollout_status.report_live_traffic_budget.build_report
        try:
            report_traffic_rollout_status.report_live_traffic_budget.build_report = lambda _args: {
                "provider_api_called": False,
                "osrm_started": False,
                "missing_profiles": ["missing_profile"],
                "profiles": [
                    {
                        "profile": "ok_profile",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "provider": "amap",
                        "estimated_api_call_count": 21,
                        "max_api_calls_per_run": 1000,
                        "provider_refresh_cap": 0,
                        "baseline_fast_path_ready": True,
                        "status": "ok",
                    },
                    {
                        "profile": "bad_profile",
                        "city": "Suzhou",
                        "period": "pm_peak",
                        "provider": "amap",
                        "estimated_api_call_count": 1001,
                        "max_api_calls_per_run": 1000,
                        "provider_refresh_cap": 0,
                        "baseline_fast_path_ready": False,
                        "source": "baseline_json",
                        "status": "over_cap",
                    },
                ],
            }

            status = report_traffic_rollout_status.collect_budget_status()
        finally:
            report_traffic_rollout_status.report_live_traffic_budget.build_report = original

        self.assertTrue(status["available"])
        self.assertTrue(status["problem"])
        self.assertFalse(status["provider_api_called"])
        self.assertFalse(status["osrm_started"])
        self.assertEqual(status["missing_profile_count"], 1)
        self.assertEqual(status["over_cap_profiles"], ["bad_profile"])
        self.assertEqual(status["baseline_fast_path_problem_profiles"], ["bad_profile"])
        self.assertEqual(status["total_estimated_api_call_count"], 1022)
        self.assertEqual(status["max_estimated_api_call_count"], 1001)

    def test_budget_status_flags_safety_violations(self) -> None:
        original = report_traffic_rollout_status.report_live_traffic_budget.build_report
        try:
            report_traffic_rollout_status.report_live_traffic_budget.build_report = lambda _args: {
                "provider_api_called": True,
                "osrm_started": True,
                "missing_profiles": [],
                "profiles": [
                    {
                        "profile": "looks_ok",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "provider": "amap",
                        "estimated_api_call_count": 21,
                        "max_api_calls_per_run": 1000,
                        "provider_refresh_cap": 0,
                        "baseline_fast_path_ready": True,
                        "status": "ok",
                    }
                ],
            }

            status = report_traffic_rollout_status.collect_budget_status()
        finally:
            report_traffic_rollout_status.report_live_traffic_budget.build_report = original

        self.assertTrue(status["problem"])
        self.assertEqual(status["safety_violation_reasons"], ["provider_api_called", "osrm_started"])

    def test_build_status_includes_budget_when_requested(self) -> None:
        original = report_traffic_rollout_status.collect_budget_status
        try:
            report_traffic_rollout_status.collect_budget_status = lambda: {
                "available": True,
                "problem": True,
                "total_estimated_api_call_count": 1001,
            }
            with tempfile.TemporaryDirectory() as tmpdir:
                report = report_traffic_rollout_status.build_status(
                    sample_dir=Path(tmpdir),
                    min_measured_at="2026-06-18T00:00:00+08:00",
                    profiles=[("CN", "Shanghai", "am_peak")],
                    min_geo_ratio=1.0,
                    include_timers=False,
                    include_osrm=False,
                    include_budget=True,
                )
        finally:
            report_traffic_rollout_status.collect_budget_status = original

        self.assertEqual(report["api_budget"]["total_estimated_api_call_count"], 1001)
        self.assertEqual(report["status"], "waiting")

    def test_problem_services_are_summarized(self) -> None:
        rows = [
            {
                "unit": "brp-live-traffic-pm.service",
                "problem": True,
                "result": "success",
                "active_state": "inactive",
                "exec_main_status": 1,
                "exec_main_exit_local": "2026-06-17T15:40:02+08:00",
            },
            {"unit": "brp-live-traffic-am.service", "problem": False},
        ]

        problems = report_traffic_rollout_status._problem_services(rows)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["unit"], "brp-live-traffic-pm.service")
        self.assertEqual(problems[0]["exec_main_status"], 1)


if __name__ == "__main__":
    unittest.main()
