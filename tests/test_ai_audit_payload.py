from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import ai_audit  # noqa: E402
import backend_service  # noqa: E402


def test_ai_audit_payload_includes_operational_review_without_full_addresses() -> None:
    payload = ai_audit.build_ai_audit_payload(
        {
            "job_id": "audit-123",
            "owner_email": "ops@example.com",
            "metadata": {"job_name": "Seoul AM test"},
            "config": {"service_direction": "To School", "max_route_duration_minutes": 75, "time_impact_limit_minutes": 20},
            "result": {
                "service_direction": "To School",
                "traffic_profile_name": "AM Peak",
                "traffic_profile_context": "Direct provider validation",
                "current_plan_assessment": {
                    "route_count": 2,
                    "route_summaries": [
                        {
                            "route_id": "R2",
                            "bus_type": "30-seat",
                            "service_stop_count": 4,
                            "passenger_count": 9,
                            "load_factor": 0.3,
                            "distance_m": 9000,
                            "duration_s": 2400,
                        }
                    ],
                },
                "time_constrained_optimization": {
                    "route_count": 2,
                    "traffic_gate": {
                        "status": "failed",
                        "checked_route_count": 2,
                        "failed_route_count": 1,
                    },
                    "time_impact": {
                        "available": True,
                        "acceptance_threshold_minutes": 15,
                        "compared_stop_count": 8,
                        "compared_rider_count": 42,
                        "acceptance_rider_ratio": 0.76,
                        "over_acceptance_stop_count": 2,
                        "over_acceptance_rider_count": 10,
                        "high_risk_stop_count": 0,
                        "high_risk_rider_count": 0,
                        "max_adverse_delta_minutes": 22,
                        "max_over_acceptance_delta_minutes": 7,
                        "weighted_avg_adverse_delta_minutes": 8.4,
                        "worse_rider_count": 16,
                        "better_rider_count": 12,
                        "route_changed_rider_count": 28,
                        "top_impacted_stops": [
                            {
                                "address": "This full address must not leak",
                                "new_route_id": "R1",
                                "affected_rider_count": 5,
                                "adverse_delta_minutes": 22,
                                "impact_direction": "worse",
                                "acceptance_status": "over",
                            }
                        ],
                    },
                    "points": [
                        {"passenger_count": 22, "demand_batch_index": 1, "demand_batch_count": 2},
                        {"passenger_count": 20, "demand_batch_index": 2, "demand_batch_count": 2},
                    ],
                },
                "input_address_review": {
                    "summary": {
                        "warning_count": 2,
                        "school_distance_warning_count": 1,
                        "region_mismatch_warning_count": 1,
                        "route_context_warning_count": 0,
                    },
                    "warnings": [
                        {
                            "type": "region_mismatch",
                            "status": "needs_review",
                            "accepted": True,
                            "address": "This full address must not leak either",
                            "expected_city": "Seoul",
                            "resolved_city": "Suwon",
                        }
                    ],
                },
            },
        }
    )

    review = payload["decision_review"]
    assert payload["job"]["time_impact_limit_minutes"] == 20
    assert payload["scenario_outcomes"][1]["name"] == "Strict Plan"
    assert payload["recommended_scenario"] is None
    assert review["time_impact"]["decision"] == "review_needed"
    assert review["time_impact"]["acceptance_rider_pct"] == 76
    assert review["input_address_review"]["warning_count"] == 2
    assert review["provider_validation"]["strict_plan"]["status"] == "failed"
    assert review["aggregated_stop_batches"]["has_split_stop_batches"] is True
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "This full address must not leak" not in serialized


def test_recommended_scenario_uses_fully_passing_plan_with_fewest_routes() -> None:
    scenarios = [
        {
            "key": "time_constrained",
            "enabled": True,
            "route_count": 18,
            "traffic_gate": {"status": "passed", "vehicle_saving_target": {"status": "passed"}},
            "time_constraint": {"strict_satisfied": True},
            "time_impact": {"available": False},
        },
        {
            "key": "exception_preserving",
            "enabled": True,
            "route_count": 16,
            "traffic_gate": {"status": "failed", "vehicle_saving_target": {"status": "passed"}},
            "exception_accepted": True,
            "time_constraint": {"strict_satisfied": True},
            "time_impact": {"available": False},
        },
    ]

    recommended = ai_audit._recommended_scenario(scenarios)
    assert recommended["key"] == "exception_preserving"
    assert recommended["recommendation_type"] == "adoption_ready"
    assert recommended["adoption_ready"] is True


def test_recommended_scenario_uses_provider_time_when_route_counts_tie() -> None:
    shared = {
        "enabled": True,
        "route_count": 16,
        "traffic_gate": {"status": "passed", "vehicle_saving_target": {"status": "passed"}},
        "time_constraint": {"strict_satisfied": True},
        "time_impact": {"available": False},
    }
    scenarios = [
        {**shared, "key": "time_constrained", "provider_total_duration_s": 7200},
        {**shared, "key": "exception_preserving", "provider_total_duration_s": 6900},
    ]

    assert ai_audit._recommended_scenario(scenarios)["key"] == "exception_preserving"


def test_scenario_decision_metrics_unions_affected_riders_and_tracks_worst_miss() -> None:
    metrics = ai_audit._scenario_decision_metrics(
        {
            "points": [
                {"node_id": 0, "passenger_count": 0},
                {"node_id": 1, "passenger_count": 4},
                {"node_id": 2, "passenger_count": 3},
                {"node_id": 3, "passenger_count": 2},
            ],
            "routes": [
                {
                    "route_id": "Bus 1",
                    "nodes": [0, 1, 2],
                    "load": 7,
                    "final_route_traffic_gate": {
                        "status": "failed",
                        "time_window_overrun_minutes": 6,
                    },
                },
                {
                    "route_id": "Bus 2",
                    "nodes": [0, 3],
                    "load": 2,
                    "final_route_traffic_gate": {"status": "passed"},
                },
            ],
            "traffic_gate": {
                "status": "failed",
                "checked_route_count": 2,
                "failed_route_ids": ["Bus 1"],
                "failed_route_count": 1,
                "unavailable_route_count": 0,
                "max_time_window_overrun_minutes": 6,
            },
            "final_time_impact_gate": {
                "status": "failed",
                "threshold_minutes": 15,
                "over_limit_rider_count": 5,
                "max_over_limit_minutes": 9,
                "unavailable_stop_count": 0,
                "unavailable_route_count": 0,
                "violations": [
                    {"node_index": 2, "affected_rider_count": 3, "over_limit_minutes": 9},
                    {"node_index": 3, "affected_rider_count": 2, "over_limit_minutes": 4},
                ],
            },
        }
    )

    assert metrics["evidence_complete"] is True
    assert metrics["affected_rider_count"] == 9
    assert metrics["worst_over_limit_minutes"] == 9
    assert metrics["worst_source"] == "time_impact"
    assert metrics["excess_rider_minutes"] == 77


def test_recommended_scenario_uses_least_harm_reference_when_both_fail() -> None:
    scenarios = [
        {
            "key": "time_constrained",
            "enabled": True,
            "route_count": 16,
            "provider_total_duration_s": 7000,
            "traffic_gate": {"status": "failed"},
            "decision_metrics": {
                "evidence_complete": True,
                "affected_rider_count": 50,
                "worst_over_limit_minutes": 20,
                "excess_rider_minutes": 400,
            },
        },
        {
            "key": "exception_preserving",
            "enabled": True,
            "route_count": 18,
            "provider_total_duration_s": 7600,
            "traffic_gate": {"status": "failed"},
            "decision_metrics": {
                "evidence_complete": True,
                "affected_rider_count": 25,
                "worst_over_limit_minutes": 12,
                "excess_rider_minutes": 180,
            },
        },
    ]

    recommended = ai_audit._recommended_scenario(scenarios)
    assert recommended["key"] == "exception_preserving"
    assert recommended["recommendation_type"] == "review_reference"
    assert recommended["adoption_ready"] is False


def test_recommended_scenario_does_not_guess_when_failed_evidence_is_incomplete() -> None:
    scenarios = [
        {
            "key": "time_constrained",
            "enabled": True,
            "traffic_gate": {"status": "failed"},
            "decision_metrics": {"evidence_complete": False},
        },
        {
            "key": "exception_preserving",
            "enabled": True,
            "traffic_gate": {"status": "failed"},
            "decision_metrics": {"evidence_complete": True},
        },
    ]

    assert ai_audit._recommended_scenario(scenarios) is None


def test_legacy_hard_time_impact_without_final_gate_is_not_passing() -> None:
    scenario = {
        "time_constraint": {
            "enabled": True,
            "mode": "hard",
            "strict_satisfied": True,
            "bounded_solver_stop_count": 116,
            "expected_solver_stop_count": 116,
        },
        "feasibility_report": {
            "hard_constraints": {"time_impact": {"status": "passed"}},
        },
    }

    assert ai_audit._scenario_time_impact_passed(scenario) is False


def test_ai_audit_prompt_headings_cover_new_sections() -> None:
    assert "## Executive conclusion" in ai_audit._ai_audit_section_headings("English")
    assert "## Time-window impact" in ai_audit._ai_audit_section_headings("English")
    assert "## 이 계획을 선택한 이유" in ai_audit._ai_audit_section_headings("Korean")
    assert "## 为什么选择这个方案" in ai_audit._ai_audit_section_headings("Chinese")


def test_backend_ai_audit_injects_time_impact_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate(record, *, force=False, language=None):
        captured["record"] = record
        captured["force"] = force
        captured["language"] = language
        return {"report_markdown": "ok"}

    monkeypatch.setattr(backend_service, "_generate_ai_audit_report", fake_generate)

    backend_service.generate_ai_audit_report(
        {
            "job_id": "impact-context",
            "config": {"from_school_departure_time": "15:40", "stop_service_minutes": 0},
            "result": {
                "service_direction": "From School",
                "structured_results": {
                    "current_plan": {
                        "points": [
                            {
                                "address": "School",
                                "plot_lat": 31.2,
                                "plot_lng": 121.4,
                                "passenger_count": 0,
                                "is_depot": True,
                            },
                            {
                                "address": "Stop A",
                                "plot_lat": 31.21,
                                "plot_lng": 121.41,
                                "passenger_count": 3,
                                "is_depot": False,
                            },
                        ],
                        "routes": [
                            {
                                "vehicle_id": 1,
                                "nodes": [0, 1],
                                "time_s": 600,
                                "distance_m": 1200,
                                "leg_details": [
                                    {
                                        "duration_s": 600,
                                        "distance_m": 1200,
                                        "geometry": [[31.2, 121.4], [31.21, 121.41]],
                                    }
                                ],
                            }
                        ],
                    },
                    "time_constrained": {
                        "points": [
                            {
                                "address": "School",
                                "plot_lat": 31.2,
                                "plot_lng": 121.4,
                                "passenger_count": 0,
                                "is_depot": True,
                            },
                            {
                                "address": "Stop A",
                                "plot_lat": 31.21,
                                "plot_lng": 121.41,
                                "passenger_count": 3,
                                "is_depot": False,
                            },
                        ],
                        "routes": [
                            {
                                "route_id": "R9",
                                "vehicle_id": 9,
                                "nodes": [0, 1],
                                "time_s": 1500,
                                "distance_m": 2200,
                                "leg_details": [
                                    {
                                        "duration_s": 1500,
                                        "distance_m": 2200,
                                        "geometry": [[31.2, 121.4], [31.21, 121.41]],
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
        },
        force=True,
        language="English",
    )

    record = captured["record"]
    assert captured["force"] is True
    assert captured["language"] == "English"
    assert isinstance(record, dict)
    time_impact = record["result"]["time_constrained_optimization"]["time_impact"]
    assert time_impact["available"] is True
    assert time_impact["compared_stop_count"] == 1
    assert time_impact["worse_rider_count"] == 3
