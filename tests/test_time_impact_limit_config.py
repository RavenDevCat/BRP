import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
backend_service = importlib.import_module("backend_service")


def test_time_impact_limit_payload_normalizes_user_input():
    payload = backend_service._planner_config_payload({"time_impact_limit_minutes": 20})

    assert payload["time_impact_limit_minutes"] == 20


def test_time_impact_summary_uses_user_limit():
    stops = [
        {
            "passenger_count": 3,
            "time_impact": {
                "comparison_available": True,
                "adverse_delta_minutes": 18,
            },
        }
    ]

    summary = backend_service._time_impact_summary(stops, acceptance_threshold=20)

    assert summary["acceptance_threshold_minutes"] == 20
    assert summary["within_acceptance_stop_count"] == 1
    assert summary["over_acceptance_stop_count"] == 0


def test_time_impact_scenario_label_falls_back_to_config_limit():
    job_record = {
        "config": {"time_impact_limit_minutes": 20},
        "result": {"structured_results": {"time_constrained": {}}},
    }

    assert (
        backend_service._job_map_scenario_label(
            job_record,
            job_record["result"],
            "time_constrained",
        )
        == "20-Minute Constrained"
    )
