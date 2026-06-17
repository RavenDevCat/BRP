from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import report_job_traffic_attribution  # noqa: E402


class JobTrafficAttributionReportTests(unittest.TestCase):
    def test_reports_missing_attribution_on_legacy_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            (job_dir / "legacy.json").write_text(
                json.dumps(
                    {
                        "job_id": "legacy",
                        "status": "succeeded",
                        "result": {
                            "structured_results": {
                                "service_direction": "To School",
                                "traffic_profile_name": "AM Peak",
                                "traffic_time_multiplier": 1.75,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = report_job_traffic_attribution.summarize_job("legacy", job_dir)
            requirements = report_job_traffic_attribution.evaluate_requirements(
                summary,
                require_attribution=True,
                required_scenarios=["free_optimization_baseline"],
                min_geo_route_ratio=1.0,
            )

        self.assertFalse(summary["has_traffic_attribution"])
        self.assertEqual(summary["scenario_count"], 0)
        self.assertFalse(requirements[0]["passed"])
        self.assertEqual(requirements[0]["reason"], "missing_traffic_attribution")
        self.assertFalse(requirements[1]["passed"])
        self.assertEqual(requirements[1]["reason"], "missing_scenario_attribution")

    def test_reports_geo_route_attribution_from_structured_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            (job_dir / "geo.json").write_text(
                json.dumps(
                    {
                        "job_id": "geo",
                        "status": "succeeded",
                        "result": {
                            "structured_results": {
                                "service_direction": "From School",
                                "traffic_profile_name": "PM Peak (Attributed)",
                                "traffic_time_multiplier": 1.88,
                                "traffic_coefficient_mode": "attributed",
                                "traffic_attribution": {
                                    "enabled": True,
                                    "succeeded": True,
                                    "mode": "attributed",
                                    "method": "route_similarity",
                                    "confidence": "high",
                                    "route_level_applied": True,
                                    "observed_route_sample_count": 21,
                                    "geo_route_sample_count": 21,
                                    "scale_only_route_sample_count": 0,
                                    "geo_route_sample_ratio": 1.0,
                                    "scenario_route_estimates": {
                                        "free_optimization_baseline": {
                                            "route_count": 2,
                                            "geo_attributed_route_count": 2,
                                            "route_similarity_route_count": 0,
                                            "fallback_route_count": 0,
                                            "method_counts": {"geo_route_similarity": 2},
                                            "quality_reason_counts": {"geo_threshold_passed": 2},
                                            "route_estimates": [
                                                {
                                                    "route_id": "R1",
                                                    "method": "geo_route_similarity",
                                                    "quality_reason": "geo_threshold_passed",
                                                    "factor": 1.23,
                                                    "avg_similarity": 0.91,
                                                    "matched_sample_count": 3,
                                                    "candidate_count": 7,
                                                    "geo_candidate_count": 5,
                                                    "usable_geo_candidate_count": 3,
                                                    "osrm_duration_s": 1800,
                                                    "stop_count": 6,
                                                    "top_matches": [
                                                        {
                                                            "route_id": "sample-r1",
                                                            "source_id": "sample-file",
                                                            "factor": 1.2,
                                                            "similarity_score": 0.94,
                                                            "similarity_method": "geo_route_similarity",
                                                            "geo_similarity_score": 0.89,
                                                            "corridor_overlap": 0.7,
                                                            "center_distance_km": 1.5,
                                                            "bearing_score": 0.8,
                                                            "duration_score": 0.95,
                                                            "stop_score": 0.9,
                                                            "scale_score": 0.92,
                                                        }
                                                    ],
                                                },
                                                {
                                                    "route_id": "R2",
                                                    "method": "geo_route_similarity",
                                                    "quality_reason": "geo_threshold_passed",
                                                },
                                            ],
                                        },
                                        "time_constrained": {
                                            "route_count": 2,
                                            "geo_attributed_route_count": 1,
                                            "route_similarity_route_count": 1,
                                            "fallback_route_count": 0,
                                            "route_estimates": [
                                                {"route_id": "R1", "method": "geo_route_similarity"},
                                                {"route_id": "R2", "method": "route_similarity"},
                                            ],
                                        },
                                    },
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = report_job_traffic_attribution.summarize_job("geo", job_dir)
            evidence_summary = report_job_traffic_attribution.summarize_job(
                "geo",
                job_dir,
                include_route_evidence=True,
                include_top_matches=True,
            )
            pass_requirements = report_job_traffic_attribution.evaluate_requirements(
                summary,
                require_attribution=True,
                required_scenarios=["free_optimization_baseline"],
                min_geo_route_ratio=1.0,
            )
            fail_requirements = report_job_traffic_attribution.evaluate_requirements(
                summary,
                require_attribution=True,
                required_scenarios=["time_constrained"],
                min_geo_route_ratio=1.0,
            )

        self.assertTrue(summary["has_traffic_attribution"])
        self.assertTrue(summary["attribution_succeeded"])
        self.assertTrue(summary["route_level_applied"])
        self.assertEqual(summary["scenario_count"], 2)
        self.assertNotIn("route_evidence", summary["scenarios"][0])
        free_evidence = {
            scenario["scenario"]: scenario for scenario in evidence_summary["scenarios"]
        }["free_optimization_baseline"]["route_evidence"]
        self.assertEqual(free_evidence[0]["route_id"], "R1")
        self.assertEqual(free_evidence[0]["method"], "geo_route_similarity")
        self.assertEqual(free_evidence[0]["quality_reason"], "geo_threshold_passed")
        self.assertAlmostEqual(free_evidence[0]["factor"], 1.23)
        self.assertEqual(free_evidence[0]["matched_sample_count"], 3)
        self.assertEqual(free_evidence[0]["usable_geo_candidate_count"], 3)
        self.assertAlmostEqual(free_evidence[0]["osrm_duration_min"], 30.0)
        self.assertEqual(free_evidence[0]["top_matches"][0]["source_id"], "sample-file")
        self.assertEqual(free_evidence[0]["top_matches"][0]["route_id"], "sample-r1")
        scenarios = {scenario["scenario"]: scenario for scenario in summary["scenarios"]}
        self.assertEqual(scenarios["time_constrained"]["non_geo_route_count"], 1)
        self.assertEqual(scenarios["time_constrained"]["non_geo_routes"][0]["route_id"], "R2")
        self.assertEqual(scenarios["time_constrained"]["non_geo_routes"][0]["method"], "route_similarity")
        self.assertTrue(all(item["passed"] for item in pass_requirements))
        self.assertFalse(fail_requirements[1]["passed"])
        self.assertEqual(fail_requirements[1]["reason"], "geo_route_ratio_below_requirement")
        self.assertAlmostEqual(float(fail_requirements[1]["geo_attributed_route_ratio"]), 0.5)
        self.assertEqual(fail_requirements[1]["non_geo_route_count"], 1)
        self.assertEqual(fail_requirements[1]["non_geo_routes"][0]["route_id"], "R2")

    def test_reads_scenario_payload_when_top_level_attribution_lacks_scenario_estimates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            (job_dir / "scenario.json").write_text(
                json.dumps(
                    {
                        "job_id": "scenario",
                        "status": "succeeded",
                        "result": {
                            "structured_results": {
                                "traffic_attribution": {
                                    "enabled": True,
                                    "succeeded": True,
                                    "route_level_applied": True,
                                },
                                "free_optimization_baseline": {
                                    "traffic_route_attribution": {
                                        "route_count": 1,
                                        "route_estimates": [
                                            {
                                                "route_id": "R1",
                                                "method": "geo_route_similarity",
                                                "quality_reason": "geo_threshold_passed",
                                            }
                                        ],
                                    }
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = report_job_traffic_attribution.summarize_job("scenario", job_dir)

        self.assertEqual(summary["scenario_count"], 1)
        self.assertEqual(summary["scenarios"][0]["scenario"], "free_optimization_baseline")
        self.assertEqual(summary["scenarios"][0]["geo_attributed_route_count"], 1)


if __name__ == "__main__":
    unittest.main()
