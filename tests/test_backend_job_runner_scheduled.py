from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_job_runner  # noqa: E402


def _scheduled_result(*, api_calls: int = 1, gate_status: str = "failed") -> dict[str, object]:
    return {
        "scheduled_run_traffic_refresh": {
            "provider": "amap",
            "status": "ready",
            "api_calls": api_calls,
        },
        "structured_results": {
            "current_plan": {
                "routes": [{"route_id": "Bus 1"}],
                "traffic_gate": {
                    "provider": "amap",
                    "status": gate_status,
                    "checked_route_count": 1,
                    "unavailable_route_count": 1 if gate_status == "unavailable" else 0,
                },
            }
        },
    }


def test_scheduled_validation_requires_fresh_calls_and_available_gates() -> None:
    assert backend_job_runner._scheduled_final_traffic_validation_error(
        _scheduled_result(api_calls=0)
    ) == "scheduled fresh traffic refresh made no provider API calls"
    assert backend_job_runner._scheduled_final_traffic_validation_error(
        _scheduled_result(gate_status="unavailable")
    ) == "scheduled final traffic gate unavailable for current_plan"
    assert backend_job_runner._scheduled_final_traffic_validation_error(
        _scheduled_result(gate_status="failed")
    ) is None
    result_with_legacy_scenario = _scheduled_result(gate_status="failed")
    result_with_legacy_scenario["structured_results"]["original"] = {"routes": [{}]}
    assert backend_job_runner._scheduled_final_traffic_validation_error(
        result_with_legacy_scenario
    ) is None


def test_worker_config_uses_legacy_zero_stop_limit_semantics() -> None:
    config = backend_job_runner.build_planner_config({"route_stop_limit": 0})

    assert config.route_stop_limit is None


def _partial_result():
    result = _scheduled_result()
    scenario = result["structured_results"]["current_plan"]
    scenario["traffic_feasible"] = False
    scenario["routes"] = [
        {"route_id": "R1", "final_route_traffic_gate": {"route_id": "R1", "status": "failed",
            "passes": False, "verified_total_duration_s": 4200, "verified_distance_m": 20000}},
        {"route_id": "R2", "final_route_traffic_gate": {"route_id": "R2", "status": "unavailable",
            "passes": None, "error": "continuous_chunk_join_unverified"}},
    ]
    scenario["traffic_gate"].update(unavailable_route_count=1, failed_route_count=1)
    return result


def test_local_failure_preserves_failed_constraints_and_marks_partial():
    result = _partial_result()
    original = deepcopy(result["structured_results"])
    assert backend_job_runner._scheduled_final_traffic_validation_error(result) is None
    assert result["structured_results"] == original
    assert result["traffic_validation_summary"]["status"] == "partial"
    assert result["traffic_validation_summary"]["incomplete_scenarios"][0]["route_ids"] == ["R2"]


def test_protected_scope_counts_frozen_failures_without_erasing_them():
    result = _partial_result()
    scenario = result["structured_results"]["current_plan"]
    scenario["routes"][0]["exception_role"] = "frozen_current"
    scenario["traffic_gate"].update(evaluation_scope="optimized_remainder", failed_route_count=0,
        all_routes_failed_route_count=1, excluded_route_count=1, excluded_route_ids=["R1"])
    assert backend_job_runner._scheduled_final_traffic_validation_error(result) is None
    assert scenario["routes"][0]["final_route_traffic_gate"]["status"] == "failed"
    scenario["traffic_gate"]["all_routes_failed_route_count"] = 0
    assert backend_job_runner._scheduled_final_traffic_validation_error(result)


@pytest.mark.parametrize("fault", ["counts", "missing_gate", "passes", "no_error", "feasible", "no_metric", "all_missing"])
def test_partial_is_not_a_bypass_for_missing_or_inconsistent_evidence(fault):
    result = _partial_result()
    scenario = result["structured_results"]["current_plan"]
    if fault == "counts":
        scenario["traffic_gate"]["unavailable_route_count"] = 2
    elif fault == "missing_gate":
        scenario["routes"][1].pop("final_route_traffic_gate")
    elif fault == "passes":
        scenario["routes"][1]["final_route_traffic_gate"]["passes"] = True
    elif fault == "no_error":
        scenario["routes"][1]["final_route_traffic_gate"].pop("error")
    elif fault == "feasible":
        scenario["traffic_feasible"] = True
    elif fault == "no_metric":
        scenario["routes"][0]["final_route_traffic_gate"].pop("verified_total_duration_s")
    else:
        scenario["routes"] = scenario["routes"][1:]
        scenario["traffic_gate"].update(status="unavailable", checked_route_count=0, failed_route_count=0)
    assert backend_job_runner._scheduled_final_traffic_validation_error(result)
    assert "traffic_validation_summary" not in result


def test_partial_worker_completes_and_preserves_evidence(monkeypatch):
    result = _partial_result()
    state = {"record": {"job_id": "partial", "status": "queued", "metadata": {"scheduled_job": True}}}
    monkeypatch.setattr(backend_job_runner, "_load_job", lambda _: deepcopy(state["record"]))
    monkeypatch.setattr(backend_job_runner, "_save_job", lambda record: state.update(record=deepcopy(record)))
    monkeypatch.setattr(backend_job_runner, "_release_concurrency_slot", lambda: None)
    monkeypatch.setattr(backend_job_runner, "_create_automatic_deep_verifications", lambda _: None)
    monkeypatch.setattr(backend_job_runner, "run_backend_planner_with_prepared_data", lambda *a, **k: result)
    monkeypatch.setattr(sys, "argv", ["backend_job_runner.py", "partial"])
    assert backend_job_runner.main() == 0
    assert state["record"]["status"] == "succeeded"
    assert state["record"]["result"]["traffic_validation_summary"]["status"] == "partial"
    assert state["record"]["result"]["structured_results"]["current_plan"]["traffic_feasible"] is False


def test_scheduled_runner_preserves_result_when_fresh_validation_fails(monkeypatch) -> None:
    result = _scheduled_result(api_calls=0)
    state = {
        "record": {
            "job_id": "scheduled-1",
            "status": "queued",
            "metadata": {"scheduled_job": True},
            "config": {},
            "prepared_payload": {},
        }
    }

    monkeypatch.setattr(
        backend_job_runner,
        "_load_job",
        lambda _job_id: deepcopy(state["record"]),
    )
    monkeypatch.setattr(
        backend_job_runner,
        "_save_job",
        lambda record: state.update(record=deepcopy(record)),
    )
    monkeypatch.setattr(backend_job_runner, "_release_concurrency_slot", lambda: None)
    monkeypatch.setattr(
        backend_job_runner,
        "run_backend_planner_with_prepared_data",
        lambda _payload, *, config, require_fresh_final_traffic: result,
    )
    monkeypatch.setattr(sys, "argv", ["backend_job_runner.py", "scheduled-1"])

    assert backend_job_runner.main() == 1
    assert state["record"]["status"] == "failed"
    assert state["record"]["result"] == result
    assert "no provider API calls" in str(state["record"]["error"])
