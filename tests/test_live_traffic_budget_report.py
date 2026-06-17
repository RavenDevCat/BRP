from __future__ import annotations

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

    def test_baseline_json_budget_flags_universal_cap_overage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_baseline(tmpdir)
            args = self._baseline_args(tmpdir, cap=1)

            row = budget.evaluate_profile("fixture", args)

        self.assertEqual(row["estimated_api_call_count"], 2)
        self.assertEqual(row["max_api_calls_per_run"], 1)
        self.assertEqual(row["status"], "over_cap")


if __name__ == "__main__":
    unittest.main()
