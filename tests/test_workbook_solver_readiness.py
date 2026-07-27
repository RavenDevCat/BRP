import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_service  # noqa: E402


def _address_review() -> dict:
    return {
        "status": "ok",
        "blocking_count": 0,
        "review_count": 0,
        "requires_acknowledgement": False,
        "items": [],
    }


def test_readiness_separates_invalid_and_retryable_failures() -> None:
    invalid = backend_service._workbook_solver_readiness(
        address_review=_address_review(),
        auto_route_budget={"status": "unavailable", "reason": "no_measurable_current_routes"},
    )
    retryable = backend_service._workbook_solver_readiness(
        address_review=_address_review(),
        auto_route_budget={"status": "unavailable", "reason": "ConnectionError"},
    )
    ready = backend_service._workbook_solver_readiness(
        address_review=_address_review(),
        auto_route_budget={"status": "ready", "minutes": 60},
    )

    assert invalid["status"] == "invalid"
    assert retryable["status"] == "retryable_error"
    assert ready["status"] == "ready"


def test_submit_rejects_unready_workbook_before_job_creation(monkeypatch) -> None:
    class FakeClientCore:
        @staticmethod
        def prepare_client_payload(*_args, **_kwargs):
            return {"prepared_payload": {"original_points": [{"node_id": 0}, {"node_id": 1}]}}

    current_plan = {"input_records": [{"address": "stop"}], "summary": {}}
    create_job = mock.Mock()
    monkeypatch.setattr(
        backend_service,
        "_read_current_plan_upload",
        lambda _payload: (FakeClientCore(), "sample.xlsx", current_plan),
    )
    monkeypatch.setattr(backend_service, "_build_client_planner_config", lambda *_args: object())
    monkeypatch.setattr(backend_service, "_build_address_review", lambda *_args: _address_review())
    monkeypatch.setattr(backend_service, "_auto_current_plan_route_budget_details", lambda *_args: None)
    monkeypatch.setattr(backend_service, "_current_plan_preview_map", lambda *_args: ({}, None))
    monkeypatch.setattr(backend_service.JOB_STORE, "create_job", create_job)

    with pytest.raises(backend_service.WorkbookReadinessError) as exc:
        backend_service._handle_workbook_submit({}, "user@example.com")

    assert exc.value.readiness["status"] == "invalid"
    create_job.assert_not_called()


def test_submit_exposes_preparation_failure_as_retryable(monkeypatch) -> None:
    class FakeClientCore:
        @staticmethod
        def prepare_client_payload(*_args, **_kwargs):
            raise ConnectionError("geocoder unavailable")

    create_job = mock.Mock()
    monkeypatch.setattr(
        backend_service,
        "_read_current_plan_upload",
        lambda _payload: (
            FakeClientCore(),
            "sample.xlsx",
            {"input_records": [{"address": "stop"}]},
        ),
    )
    monkeypatch.setattr(backend_service, "_build_client_planner_config", lambda *_args: object())
    monkeypatch.setattr(backend_service.JOB_STORE, "create_job", create_job)

    with pytest.raises(backend_service.WorkbookReadinessError) as exc:
        backend_service._handle_workbook_submit({}, "user@example.com")

    assert exc.value.readiness["status"] == "retryable_error"
    create_job.assert_not_called()
