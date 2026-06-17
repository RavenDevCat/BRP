from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertEqual(report["timers"]["problem_count"], 0)
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


if __name__ == "__main__":
    unittest.main()
