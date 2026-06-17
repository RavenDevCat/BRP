from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import report_live_traffic_readiness  # noqa: E402


class LiveTrafficReadinessReportTests(unittest.TestCase):
    def test_require_geo_checks_ratio_and_missing_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "am_peak.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-17T07:25:55+08:00",
                        "dry_run": False,
                        "routes": [
                            {
                                "route_id": "geo",
                                "factor": 1.8,
                                "route_fingerprint": {"cell_count": 3, "cells": ["a", "b", "c"]},
                            },
                            {"route_id": "scale", "factor": 1.9},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (sample_dir / "off_peak.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "off_peak",
                        "measured_at": "2026-06-17T11:14:29+08:00",
                        "dry_run": False,
                        "routes": [
                            {
                                "route_id": "geo",
                                "factor": 1.7,
                                "traffic_fingerprint": {"corridor_cells": ["a", "b"]},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = report_live_traffic_readiness.summarize(sample_dir)
            results = report_live_traffic_readiness.evaluate_requirements(
                summary,
                [
                    ("CN", "Shanghai", "am_peak"),
                    ("cn", "shanghai", "off_peak"),
                    ("CN", "Suzhou", "am_peak"),
                ],
                min_geo_ratio=0.75,
            )

        self.assertEqual(len(results), 3)
        self.assertFalse(results[0]["passed"])
        self.assertEqual(results[0]["reason"], "geo_ratio_below_requirement")
        self.assertAlmostEqual(float(results[0]["geo_route_sample_ratio"]), 0.5)
        self.assertTrue(results[1]["passed"])
        self.assertEqual(results[1]["reason"], "ok")
        self.assertFalse(results[2]["passed"])
        self.assertEqual(results[2]["reason"], "missing_sample_group")

    def test_min_measured_at_filters_legacy_scale_only_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "legacy_scale_only.json").write_text(
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
            (sample_dir / "rollout_geo.json").write_text(
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

            unfiltered = report_live_traffic_readiness.summarize(sample_dir)
            filtered = report_live_traffic_readiness.summarize(
                sample_dir,
                min_measured_at="2026-06-18T00:00:00+08:00",
            )
            results = report_live_traffic_readiness.evaluate_requirements(
                filtered,
                [("CN", "Shanghai", "am_peak")],
                min_geo_ratio=1.0,
            )

        self.assertEqual(unfiltered["sample_file_count"], 2)
        self.assertEqual(unfiltered["groups"][0]["geo_route_sample_ratio"], 0.5)
        self.assertEqual(filtered["sample_file_count"], 1)
        self.assertEqual(filtered["filtered_file_count"], 1)
        self.assertEqual(filtered["excluded_groups"][0]["latest_sample"], "legacy_scale_only.json")
        self.assertEqual(filtered["groups"][0]["geo_route_sample_ratio"], 1.0)
        self.assertTrue(results[0]["passed"])
        self.assertEqual(results[0]["latest_excluded_sample"], "legacy_scale_only.json")

    def test_missing_post_cutoff_group_reports_latest_excluded_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir)
            (sample_dir / "legacy_scale_only.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "pm_peak",
                        "measured_at": "2026-06-17T15:40:00+08:00",
                        "routes": [{"route_id": "legacy", "factor": 1.9}],
                    }
                ),
                encoding="utf-8",
            )

            filtered = report_live_traffic_readiness.summarize(
                sample_dir,
                min_measured_at="2026-06-18T00:00:00+08:00",
            )
            results = report_live_traffic_readiness.evaluate_requirements(
                filtered,
                [("CN", "Shanghai", "pm_peak")],
                min_geo_ratio=1.0,
            )

        self.assertEqual(filtered["sample_file_count"], 0)
        self.assertEqual(filtered["filtered_file_count"], 1)
        self.assertEqual(results[0]["reason"], "missing_sample_group")
        self.assertEqual(results[0]["latest_excluded_sample"], "legacy_scale_only.json")
        self.assertEqual(results[0]["excluded_route_sample_count"], 1)
        self.assertEqual(results[0]["excluded_geo_route_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
