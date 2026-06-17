from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import enrich_live_traffic_baseline as enrich  # noqa: E402


class EnrichLiveTrafficBaselineTests(unittest.TestCase):
    def test_enriches_coordinates_and_metrics_by_route_id(self) -> None:
        baseline = {
            "routes": [
                {
                    "route_id": "r2",
                    "stops": [
                        {"stop_sequence": 1, "address": "School", "is_school": True},
                        {"stop_sequence": 2, "address": "B"},
                    ],
                },
                {
                    "route_id": "r1",
                    "stops": [
                        {"stop_sequence": 1, "address": "A"},
                        {"stop_sequence": 2, "address": "School", "is_school": True},
                    ],
                },
            ]
        }
        job = {
            "result": {
                "current_plan_scenario": {
                    "points": [
                        {"address": "A", "lat": 31.1, "lng": 121.1, "formatted_address": "fmt A"},
                        {"address": "School", "lat": 31.2, "lng": 121.2, "formatted_address": "fmt S"},
                        {"address": "B", "lat": 31.3, "lng": 121.3, "formatted_address": "fmt B"},
                    ],
                    "routes": [
                        {"route_id": "r1", "nodes": [0, 1], "raw_osrm_time_s": 100, "distance_m": 1000},
                        {"route_id": "r2", "nodes": [1, 2], "raw_osrm_time_s": 200, "distance_m": 2000},
                    ],
                }
            }
        }

        enriched, stats = enrich.enrich_baseline_from_job(baseline, job, job_id="abc123")

        self.assertEqual(stats["route_count"], 2)
        self.assertEqual(stats["coordinate_stop_count"], 4)
        r1 = next(route for route in enriched["routes"] if route["route_id"] == "r1")
        r2 = next(route for route in enriched["routes"] if route["route_id"] == "r2")
        self.assertEqual(r1["raw_osrm_time_s"], 100)
        self.assertEqual(r1["distance_m"], 1000)
        self.assertEqual(r1["historical_duration_s"], 100)
        self.assertEqual(r1["stops"][0]["lat"], 31.1)
        self.assertEqual(r1["stops"][0]["formatted_address"], "fmt A")
        self.assertEqual(r2["stops"][0]["lat"], 31.2)
        self.assertEqual(enriched["enriched_from_job_id"], "abc123")
        self.assertEqual(enriched["coordinate_source"], "route_audit_current_plan")

    def test_rejects_address_mismatch(self) -> None:
        baseline = {"routes": [{"route_id": "r1", "stops": [{"stop_sequence": 1, "address": "A"}]}]}
        job = {
            "result": {
                "current_plan_scenario": {
                    "points": [{"address": "B", "lat": 31.1, "lng": 121.1}],
                    "routes": [{"route_id": "r1", "nodes": [0], "raw_osrm_time_s": 100, "distance_m": 1000}],
                }
            }
        }

        with self.assertRaisesRegex(ValueError, "Baseline/job mismatch"):
            enrich.enrich_baseline_from_job(baseline, job, job_id="abc123")


if __name__ == "__main__":
    unittest.main()
