import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))

backend_service = importlib.import_module("backend_service")


def _job_record():
    return {
        "job_id": "traffic123",
        "status": "succeeded",
        "result": {
            "structured_results": {
                "service_direction": "To School",
                "traffic_profile_name": "AM Peak (Attributed)",
                "traffic_time_multiplier": 1.23,
                "traffic_coefficient_mode": "attributed",
                "traffic_attribution": {
                    "enabled": True,
                    "succeeded": True,
                    "mode": "attributed",
                    "method": "route_network",
                    "confidence": "medium",
                    "route_level_applied": True,
                    "observed_route_sample_count": 20,
                    "geo_route_sample_count": 18,
                    "scale_only_route_sample_count": 2,
                    "geo_route_sample_ratio": 0.9,
                    "scenario_route_estimates": {
                        "free_optimization_baseline": {
                            "route_count": 2,
                            "observed_route_sample_count": 20,
                            "geo_route_sample_count": 18,
                            "scale_only_route_sample_count": 2,
                            "geo_route_sample_ratio": 0.9,
                            "route_estimates": [
                                {
                                    "route_id": "R1",
                                    "scenario": "free_optimization_baseline",
                                    "method": "geo_route_similarity",
                                    "quality_reason": "geo_threshold_passed",
                                    "factor": 1.31,
                                    "avg_similarity": 0.88,
                                    "matched_sample_count": 4,
                                    "candidate_count": 9,
                                    "geo_candidate_count": 7,
                                    "usable_geo_candidate_count": 4,
                                    "osrm_duration_s": 1800,
                                    "stop_count": 6,
                                    "top_matches": [
                                        {
                                            "route_id": "sample-r1",
                                            "source_id": "sample-file",
                                            "factor": 1.29,
                                            "similarity_score": 0.91,
                                            "similarity_method": "geo_route_similarity",
                                            "geo_similarity_score": 0.89,
                                            "corridor_overlap": 0.8,
                                            "center_distance_km": 1.2,
                                            "bearing_score": 0.7,
                                            "duration_score": 0.95,
                                            "stop_score": 0.9,
                                            "scale_score": 0.92,
                                        }
                                    ],
                                },
                                {
                                    "route_id": "R2",
                                    "scenario": "free_optimization_baseline",
                                    "method": "fallback",
                                    "quality_reason": "no_similar_route_sample",
                                    "reason": "fallback_to_profile",
                                    "factor": 1.23,
                                },
                            ],
                        }
                    },
                },
            }
        },
    }


def test_job_traffic_attribution_payload_summarizes_scenarios():
    payload = backend_service._job_traffic_attribution_payload(_job_record())

    assert payload["job_id"] == "traffic123"
    assert payload["has_traffic_attribution"] is True
    assert payload["route_level_applied"] is True
    assert payload["scenario_count"] == 1
    scenario = payload["scenarios"][0]
    assert scenario["scenario"] == "free_optimization_baseline"
    assert scenario["route_estimate_count"] == 2
    assert scenario["geo_attributed_route_count"] == 1
    assert scenario["non_geo_route_count"] == 1
    assert scenario["non_geo_routes"][0]["route_id"] == "R2"
    assert scenario["method_counts"] == {"geo_route_similarity": 1, "fallback": 1}


def test_job_traffic_attribution_payload_can_include_route_evidence():
    payload = backend_service._job_traffic_attribution_payload(
        _job_record(),
        include_route_evidence=True,
        include_top_matches=True,
    )

    evidence = payload["scenarios"][0]["route_evidence"]
    assert evidence[0]["route_id"] == "R1"
    assert evidence[0]["osrm_duration_min"] == 30
    assert evidence[0]["top_matches"][0]["source_id"] == "sample-file"
