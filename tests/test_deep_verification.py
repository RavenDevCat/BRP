from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from deep_verification import (  # noqa: E402
    build_automatic_verification_records,
    next_target_vehicle_count,
    public_verification_record,
    solver_seconds_for_target,
    terminal_status_for_record,
)
from deep_verification_runner import (  # noqa: E402
    _append_result_job_id,
    _build_derived_result_job,
    _provider_api_calls,
    _remaining_slice_budgets,
)


def _passed_scenario() -> dict[str, object]:
    return {
        "scenario_status": "passed",
        "bus_count": 20,
        "routes": [{"route_id": f"Bus {index}"} for index in range(1, 21)],
        "vehicle_ladder_search": {
            "attempts": [
                {"target_vehicle_count": 19, "status": "unresolved"},
                {"target_vehicle_count": 18, "status": "infeasible"},
            ]
        },
        "constraint_search_outcome": {
            "theoretical_min_vehicle_count": 17,
            "search_complete": False,
        },
        "final_time_impact_gate": {
            "over_limit_rider_count": 0,
            "max_adverse_minutes": 14,
        },
        "traffic_gate": {"status": "passed"},
    }


def _parent_job() -> dict[str, object]:
    return {
        "job_id": "parent1",
        "owner_email": "alice@example.com",
        "status": "succeeded",
        "config": {"minimum_vehicle_reduction": 2},
        "prepared_payload_summary": {"valid_stop_count": 104},
        "metadata": {"job_queue_scope": "staging"},
        "result": {
            "structured_results": {
                "time_constrained": _passed_scenario(),
                "exception_preserving": {"scenario_status": "rejected"},
            }
        },
    }


def test_automatic_verification_targets_only_unresolved_lower_counts() -> None:
    records = build_automatic_verification_records(_parent_job())

    assert len(records) == 1
    record = records[0]
    assert record["scenario_key"] == "time_constrained"
    assert record["best_vehicle_count"] == 20
    assert record["lower_bound_vehicle_count"] == 17
    assert record["target_states"]["18"]["status"] == "certified_infeasible"
    assert record["pending_target_vehicle_counts"] == [19, 17]
    assert next_target_vehicle_count(record) == 19
    assert solver_seconds_for_target(record, 19) == 60


def test_manual_or_automatic_enqueue_uses_the_current_environment_scope() -> None:
    parent = _parent_job()
    parent["metadata"]["job_queue_scope"] = "prod"

    record = build_automatic_verification_records(
        parent,
        queue_scope="staging",
    )[0]

    assert record["job_queue_scope"] == "staging"


def test_verification_covers_every_lower_count_before_retrying() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    record["target_states"]["19"]["attempt_count"] = 1

    assert next_target_vehicle_count(record) == 17

    record["target_states"]["17"]["attempt_count"] = 1
    assert next_target_vehicle_count(record) == 19
    assert solver_seconds_for_target(record, 19) == 120


def test_verification_certifies_only_after_all_lower_counts_are_infeasible() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    record["target_states"]["19"]["status"] = "certified_infeasible"
    record["target_states"]["17"]["status"] = "certified_infeasible"

    assert terminal_status_for_record(record) == "certified_minimum"


def test_exhausted_unresolved_targets_remain_explicitly_unproven() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    record["target_states"]["19"]["attempt_count"] = 3
    record["target_states"]["17"]["attempt_count"] = 3

    assert next_target_vehicle_count(record) is None
    assert terminal_status_for_record(record) == "best_found_unproven"


def test_derived_result_does_not_start_another_verifier() -> None:
    parent = _parent_job()
    parent["metadata"]["deep_verification_result"] = True

    assert build_automatic_verification_records(parent) == []


def test_provider_usage_counts_all_candidate_gate_calls() -> None:
    scenario = {"traffic_gate": {"api_calls": 5}}
    result = {
        "structured_results": {
            "runtime_profile": {"traffic_api_calls": 18},
        }
    }

    assert _provider_api_calls(scenario, result) == 18


def test_slice_budgets_are_capped_by_record_remaining_resources() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    record.update(
        {
            "elapsed_seconds": 5395.2,
            "total_budget_seconds": 5400,
            "provider_api_calls": 298,
            "provider_call_budget": 300,
        }
    )

    assert _remaining_slice_budgets(record, 19) == (4, 2)


def test_slice_does_not_start_after_a_budget_is_exhausted() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    record["elapsed_seconds"] = record["total_budget_seconds"]

    assert _remaining_slice_budgets(record, 19) == (0, 300)

    record["elapsed_seconds"] = 0
    record["provider_api_calls"] = record["provider_call_budget"]
    assert _remaining_slice_budgets(record, 19) == (0, 0)


def test_derived_result_renders_artifacts_before_it_is_shared(monkeypatch) -> None:
    import planner_core

    rendered = {"count": 0}

    def fake_rerender(structured, _config):
        rendered["count"] += 1
        payload = dict(structured)
        payload["output_paths"] = {"time_constrained": "/tmp/child/time.html"}
        return payload

    monkeypatch.setattr(planner_core, "build_planner_config", lambda _payload: object())
    monkeypatch.setattr(
        planner_core,
        "compare_current_plan_to_baseline",
        lambda _current, _scenario: {"ready": True},
    )
    monkeypatch.setattr(
        planner_core,
        "summarize_structured_results",
        lambda _structured, _service_rows: {"ready": True},
    )
    monkeypatch.setattr(planner_core, "rerender_html_from_structured_results", fake_rerender)

    class FakeStore:
        def __init__(self):
            self.saved = []
            self.copied = None

        def get_job(self, _job_id):
            return None

        def upsert_job(self, record):
            self.saved.append(record)

        def copy_route_audit_workspace(self, parent_job_id, child_job_id):
            self.copied = (parent_job_id, child_job_id)

    store = FakeStore()
    parent = {
        "job_id": "parent1",
        "owner_email": "alice@example.com",
        "shared_with_all": True,
        "config": {},
        "prepared_payload": {"input_records": []},
        "prepared_payload_summary": {},
        "metadata": {"job_name": "Morning audit"},
        "result": {
            "structured_results": {
                "current_plan_assessment": {},
                "time_constrained": {},
            }
        },
    }
    scenario = {
        "scenario_status": "passed",
        "display_name": "Strict Plan",
        "bus_count": 19,
        "routes": [{"route_id": "Bus 1"}],
    }
    record = {
        "verification_id": "verify1",
        "parent_job_id": "parent1",
        "scenario_key": "time_constrained",
        "status": "running",
        "result_job_ids": [],
    }

    child_job_id = _build_derived_result_job(
        store,
        record,
        parent,
        scenario,
        {"logs": "done", "elapsed_seconds": 12.5},
    )

    assert child_job_id
    assert rendered["count"] == 1
    assert store.saved[-1]["status"] == "succeeded"
    assert (
        store.saved[-1]["result"]["structured_results"]["deep_verification"][
            "artifact_generation_status"
        ]
        == "ready"
    )
    assert store.copied == ("parent1", child_job_id)

    second_scenario = dict(scenario)
    second_scenario["bus_count"] = 18
    second_child_job_id = _build_derived_result_job(
        store,
        record,
        parent,
        second_scenario,
        {"logs": "done again", "elapsed_seconds": 9.0},
    )

    assert second_child_job_id != child_job_id
    assert len(store.saved) == 2
    assert "verified 19-route Strict Plan" in store.saved[0]["metadata"]["job_name"]
    assert "verified 18-route Strict Plan" in store.saved[1]["metadata"]["job_name"]


def test_derived_result_ids_are_appended_in_improvement_order() -> None:
    record = {"result_job_ids": ["verified19"]}

    _append_result_job_id(record, "verified18")
    _append_result_job_id(record, "verified18")

    assert record["result_job_ids"] == ["verified19", "verified18"]


def test_public_record_omits_heavy_parent_attempt_evidence() -> None:
    record = build_automatic_verification_records(_parent_job())[0]
    assert "parent_attempt" in record["target_states"]["19"]

    public = public_verification_record(record)

    assert "parent_attempt" not in public["target_states"]["19"]
    assert public["target_states"]["19"]["status"] == "pending"
