from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys


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
    missing_optimization_gate = _scheduled_result(gate_status="failed")
    missing_optimization_gate["structured_results"]["original"] = {"routes": [{}]}
    assert backend_job_runner._scheduled_final_traffic_validation_error(
        missing_optimization_gate
    ) == "scheduled final traffic gate unavailable for original"


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
