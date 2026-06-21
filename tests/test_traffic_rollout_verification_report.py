from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "ops" / "scripts" / "report_traffic_rollout_verification.py"
SCRIPT_DIR = SCRIPT_PATH.parent
sys.path.insert(0, str(SCRIPT_DIR))

spec = importlib.util.spec_from_file_location("report_traffic_rollout_verification", SCRIPT_PATH)
assert spec is not None
report_traffic_rollout_verification = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(report_traffic_rollout_verification)


def _args(tmpdir: str, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "sample_dir": Path(tmpdir) / "samples",
        "job_dir": Path(tmpdir) / "jobs",
        "sqlite_path": None,
        "min_measured_at": "2026-06-18T00:00:00+08:00",
        "profile": [],
        "service_direction": [],
        "require_scenario": [],
        "min_geo_ratio": 1.0,
        "min_geo_route_ratio": 1.0,
        "latest_limit": 200,
        "latest_job_name_contains": "",
        "latest_source_label_contains": "",
        "latest_min_created_at": "",
        "latest_min_finished_at": "",
        "local_timezone": "Asia/Shanghai",
        "check_jobs_when_waiting": False,
        "include_route_evidence": False,
        "include_top_matches": False,
        "no_timers": True,
        "no_osrm": True,
        "no_budget": True,
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TrafficRolloutVerificationReportTests(unittest.TestCase):
    def test_skips_job_checks_while_rollout_is_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = report_traffic_rollout_verification.build_verification(_args(tmpdir))

        self.assertEqual(report["status"], "waiting")
        self.assertTrue(report["job_checks_skipped"])
        self.assertEqual(report["job_checks"], [])

    def test_verifies_latest_jobs_when_rollout_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sample_dir = root / "samples"
            job_dir = root / "jobs"
            sample_dir.mkdir()
            job_dir.mkdir()
            (sample_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:20:00+08:00",
                        "routes": [{"route_id": "sample", "route_fingerprint": {"cells": ["a"]}}],
                    }
                ),
                encoding="utf-8",
            )
            for direction, job_id in [("To School", "to_job"), ("From School", "from_job")]:
                (job_dir / f"{job_id}.json").write_text(
                    json.dumps(
                        {
                            "job_id": job_id,
                            "status": "succeeded",
                            "created_at": "2026-06-18T02:00:00+00:00",
                            "finished_at": "2026-06-18T02:05:00+00:00",
                            "metadata": {
                                "job_name": f"DEMH {direction} rollout",
                                "source_label": f"DEMH-{direction}.xlsx",
                            },
                            "result": {
                                "structured_results": {
                                    "service_direction": direction,
                                    "traffic_coefficient_mode": "attributed",
                                    "traffic_attribution": {
                                        "enabled": True,
                                        "succeeded": True,
                                        "route_level_applied": True,
                                        "scenario_route_estimates": {
                                            "free_optimization_baseline": {
                                                "route_count": 1,
                                                "geo_attributed_route_count": 1,
                                                "route_estimates": [{"route_id": "R1", "method": "geo_route_similarity"}],
                                            },
                                            "time_constrained": {
                                                "route_count": 1,
                                                "geo_attributed_route_count": 1,
                                                "route_estimates": [{"route_id": "R1", "method": "geo_route_similarity"}],
                                            },
                                        },
                                    },
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            (job_dir / "index.json").write_text(
                json.dumps(
                    [
                        {"job_id": "to_job", "status": "succeeded", "finished_at": "2026-06-18T01:00:00+00:00"},
                        {"job_id": "from_job", "status": "succeeded", "finished_at": "2026-06-18T02:00:00+00:00"},
                    ]
                ),
                encoding="utf-8",
            )

            report = report_traffic_rollout_verification.build_verification(
                _args(
                    tmpdir,
                    profile=[("CN", "Shanghai", "am_peak")],
                    service_direction=["To School", "From School"],
                    latest_job_name_contains="rollout",
                    latest_source_label_contains="DEMH",
                    latest_min_finished_at="2026-06-18T02:00:00+00:00",
                )
            )

        self.assertEqual(report["status"], "verified")
        self.assertFalse(report["job_checks_skipped"])
        self.assertEqual([check["job_id"] for check in report["job_checks"]], ["to_job", "from_job"])
        self.assertTrue(all(check["status"] == "passed" for check in report["job_checks"]))

    def test_verification_metadata_filter_can_fail_job_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sample_dir = root / "samples"
            job_dir = root / "jobs"
            sample_dir.mkdir()
            job_dir.mkdir()
            (sample_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:20:00+08:00",
                        "routes": [{"route_id": "sample", "route_fingerprint": {"cells": ["a"]}}],
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "to_job.json").write_text(
                json.dumps(
                    {
                        "job_id": "to_job",
                        "status": "succeeded",
                        "created_at": "2026-06-18T02:00:00+00:00",
                        "finished_at": "2026-06-18T02:05:00+00:00",
                        "metadata": {"job_name": "DEMH To School rollout", "source_label": "DEMH-To School.xlsx"},
                        "result": {
                            "structured_results": {
                                "service_direction": "To School",
                                "traffic_coefficient_mode": "attributed",
                                "traffic_attribution": {
                                    "enabled": True,
                                    "succeeded": True,
                                    "route_level_applied": True,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "index.json").write_text(
                json.dumps([{"job_id": "to_job", "status": "succeeded", "finished_at": "2026-06-18T01:00:00+00:00"}]),
                encoding="utf-8",
            )

            report = report_traffic_rollout_verification.build_verification(
                _args(
                    tmpdir,
                    profile=[("CN", "Shanghai", "am_peak")],
                    service_direction=["To School"],
                    latest_job_name_contains="not this one",
                )
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["job_checks"][0]["reason"], "no_matching_attributed_job")

    def test_verification_min_finished_at_can_fail_job_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sample_dir = root / "samples"
            job_dir = root / "jobs"
            sample_dir.mkdir()
            job_dir.mkdir()
            (sample_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:20:00+08:00",
                        "routes": [{"route_id": "sample", "route_fingerprint": {"cells": ["a"]}}],
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "to_job.json").write_text(
                json.dumps(
                    {
                        "job_id": "to_job",
                        "status": "succeeded",
                        "created_at": "2026-06-18T01:00:00+00:00",
                        "finished_at": "2026-06-18T01:05:00+00:00",
                        "result": {
                            "structured_results": {
                                "service_direction": "To School",
                                "traffic_coefficient_mode": "attributed",
                                "traffic_attribution": {
                                    "enabled": True,
                                    "succeeded": True,
                                    "route_level_applied": True,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "index.json").write_text(
                json.dumps([{"job_id": "to_job", "status": "succeeded", "finished_at": "2026-06-18T01:05:00+00:00"}]),
                encoding="utf-8",
            )

            report = report_traffic_rollout_verification.build_verification(
                _args(
                    tmpdir,
                    profile=[("CN", "Shanghai", "am_peak")],
                    service_direction=["To School"],
                    latest_min_finished_at="2026-06-18T02:00:00+00:00",
                )
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["job_checks"][0]["reason"], "no_matching_attributed_job")

    def test_reports_failed_when_latest_job_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sample_dir = root / "samples"
            job_dir = root / "jobs"
            sample_dir.mkdir()
            job_dir.mkdir()
            (sample_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "market": "CN",
                        "city": "Shanghai",
                        "period": "am_peak",
                        "measured_at": "2026-06-18T07:20:00+08:00",
                        "routes": [{"route_id": "sample", "route_fingerprint": {"cells": ["a"]}}],
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "index.json").write_text("[]", encoding="utf-8")

            report = report_traffic_rollout_verification.build_verification(
                _args(tmpdir, profile=[("CN", "Shanghai", "am_peak")], service_direction=["To School"])
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["job_checks"][0]["reason"], "no_matching_attributed_job")


if __name__ == "__main__":
    unittest.main()
