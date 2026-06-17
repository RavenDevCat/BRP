from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "ops" / "scripts" / "report_traffic_rollout_readiness.py"
SCRIPT_DIR = SCRIPT_PATH.parent
sys.path.insert(0, str(SCRIPT_DIR))

spec = importlib.util.spec_from_file_location("report_traffic_rollout_readiness", SCRIPT_PATH)
assert spec is not None
report_traffic_rollout_readiness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(report_traffic_rollout_readiness)


class TrafficRolloutReadinessReportTests(unittest.TestCase):
    def test_rollout_gate_ignores_legacy_scale_only_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "legacy.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-17T07:25:55+08:00",
                        "routes": [{"route_id": "legacy", "factor": 1.9}],
                    }
                ),
                encoding="utf-8",
            )
            (sample_dir / "rollout.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:25:55+08:00",
                        "routes": [
                            {
                                "route_id": "geo",
                                "factor": 1.8,
                                "route_fingerprint": {"cell_count": 2, "cells": ["a", "b"]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = report_traffic_rollout_readiness.build_report(
                sample_dir,
                min_measured_at="2026-06-18T00:00:00+08:00",
                profiles=[("CN", "Shanghai", "am_peak")],
                min_geo_ratio=1.0,
            )

        self.assertEqual(report["status"], "ok")
        readiness = report["readiness"]
        self.assertEqual(readiness["sample_file_count"], 1)
        self.assertEqual(readiness["filtered_file_count"], 1)
        self.assertTrue(readiness["requirements"][0]["passed"])

    def test_rollout_gate_fails_missing_required_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = report_traffic_rollout_readiness.build_report(
                Path(tmpdir),
                min_measured_at="2026-06-18T00:00:00+08:00",
                profiles=[("CN", "Suzhou", "pm_peak")],
                min_geo_ratio=1.0,
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["readiness"]["requirements"][0]["reason"], "missing_sample_group")


if __name__ == "__main__":
    unittest.main()
