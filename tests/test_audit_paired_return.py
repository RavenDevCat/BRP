from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_paired_return import source
import api_app
import backend_service as service
import backend_job_runner as runner
import paired_return
from runtime_store_sqlite import SqliteRuntimeStore


@pytest.fixture
def audit_api(tmp_path, monkeypatch):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    monkeypatch.setattr(service, "_runtime_sqlite_store", lambda: store)
    monkeypatch.setattr(service, "SCHEDULED_JOBS_ENABLED", True)
    prepared = source()
    prepared["current_plan"]["input_records"] = deepcopy(prepared["input_records"])
    core = SimpleNamespace(prepare_client_payload=lambda *args, **kwargs: {"prepared_payload": deepcopy(prepared)})
    monkeypatch.setattr(service, "_read_current_plan_upload", lambda _: (core, "audit.xlsx", prepared["current_plan"]))
    monkeypatch.setattr(service, "_build_client_planner_config", lambda *args: None)
    monkeypatch.setattr(service, "_build_address_review", lambda *args: {"items": []})
    monkeypatch.setattr(service, "_workbook_solver_readiness", lambda **kwargs: {"status": "ready"})
    monkeypatch.setattr(service, "_auto_current_plan_route_budget_details", lambda *args: {"status": "ready", "minutes": 90})
    monkeypatch.setattr(service, "_current_plan_preview_map", lambda *args: ({}, None))
    monkeypatch.setattr(service, "_spawn_job_worker", lambda *args: None)
    context = {"value": api_app.UserContext(email="operator@example.com", is_admin=False)}
    api_app.app.dependency_overrides[api_app.require_authorized_request] = lambda: None
    api_app.app.dependency_overrides[api_app.current_user_context] = lambda: context["value"]
    day = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    payload = {"file_name": "audit.xlsx", "file_base64": "fixture", "scheduled_job": True,
               "scheduled_date": day, "return_scheduled_time": "15:40", "submission_id": "audit-pair",
               "config": {"service_direction": "To School", "time_window_start": "06:30",
                          "time_window_end": "08:00", "stop_service_minutes": 2}}
    try:
        yield TestClient(api_app.app), store, payload, context, prepared
    finally:
        api_app.app.dependency_overrides.clear()


def test_new_audit_pair_history_permissions_export_and_idempotence(audit_api):
    client, store, payload, context, prepared = audit_api
    response = client.post("/api/workbooks/submit", json=payload)
    assert response.status_code == 202, response.text
    pair = response.json()["paired_jobs"]
    assert len(pair) == 2
    am, pm = [store.get_job(j["job_id"]) for j in pair]
    assert am["config"]["stop_service_minutes"] == 2
    assert am["config"]["max_route_duration_minutes"] == 90
    assert am["prepared_payload"] == prepared
    assert pm["metadata"]["job_kind"] == "route_audit"
    assert pm["metadata"]["analysis_config"]["stop_service_minutes"] == 2
    assert pm["metadata"]["analysis_config"]["final_time_validation_mode"] == "legacy"
    assert pm["prepared_payload"]["current_plan"]["stops"][0]["is_depot"]
    assert [s["source_stop_sequence"] for s in pm["prepared_payload"]["current_plan"]["stops"]] == [2, 1, 0]
    again = client.post("/api/workbooks/submit", json=payload)
    assert again.status_code == 202
    assert again.json()["paired_jobs"] == pair
    assert len(store.list_jobs(include_all=True)) == 2
    history = client.get("/api/jobs")
    assert history.status_code == 200
    assert all(j["job_id"] in history.text for j in pair)
    assert client.post(f'/api/jobs/{pm["job_id"]}/release').status_code == 409
    export_url = f'/api/jobs/{pm["job_id"]}/exports/return-routes'
    assert client.get(export_url).status_code == 404
    pm["status"] = "succeeded"
    pm["result"] = {"analysis_kind": "pm_return", "routes": [{"route_id": "R1", "status": "resolved",
        "riders": 2, "total_duration_s": 3599, "under_one_hour": True, "stops": []}]}
    store.upsert_job(pm)
    exported = client.get(export_url)
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content))
    assert workbook["Return routes"]["F6"].value == 3599
    assert workbook["Return routes"]["G6"].value == "Yes / 是"
    context["value"] = api_app.UserContext(email="other@example.com", is_admin=False)
    assert client.get(export_url).status_code in {403, 404}
    assert client.get(f'/api/jobs/{pm["job_id"]}').status_code in {403, 404}


@pytest.mark.parametrize("change", ["time", "config"])
def test_audit_pair_changed_request_conflicts_without_duplicate(audit_api, change):
    client, store, payload, _, _ = audit_api
    assert client.post("/api/workbooks/submit", json=payload).status_code == 202
    changed = deepcopy(payload)
    if change == "time":
        changed["return_scheduled_time"] = "16:00"
    else:
        changed["config"]["minimum_vehicle_saving"] = 3
        changed["config"]["stop_service_minutes"] = 3
    response = client.post("/api/workbooks/submit", json=changed)
    assert response.status_code == 422
    assert len(store.list_jobs(include_all=True)) == 2


def test_invalid_pm_time_does_not_create_am(audit_api):
    client, store, payload, _, _ = audit_api
    response = client.post("/api/workbooks/submit", json={**payload, "return_scheduled_time": "06:00"})
    assert response.status_code == 422
    assert store.list_jobs(include_all=True) == []


@pytest.mark.parametrize("mode", ["immediate", "from_school", "non_china"])
def test_original_single_job_paths_remain(audit_api, monkeypatch, mode):
    client, store, payload, _, _ = audit_api
    if mode == "immediate":
        payload["scheduled_job"] = False
    elif mode == "from_school":
        payload["config"]["service_direction"] = "From School"
    else:
        monkeypatch.setattr(service, "infer_traffic_location", lambda _: ("Korea", "Seoul"))
    response = client.post("/api/workbooks/submit", json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["paired_jobs"] is None
    assert len(store.list_jobs(include_all=True)) == 1


@pytest.mark.parametrize("period", ["am", "pm", "expired", "early"])
def test_worker_dispatch_and_timing_gate(monkeypatch, period):
    now = datetime.now(timezone.utc)
    is_pm = period != "am"
    metadata = {"scheduled_job": True, "job_kind": "route_audit", "pair_id": "pair",
                "scheduled_start_at": (now - timedelta(seconds=1)).isoformat(),
                "schedule_expires_at": (now + timedelta(minutes=14)).isoformat(),
                "analysis_config": {"stop_service_minutes": 1}}
    if is_pm:
        metadata["measurement_kind"] = "pm_return"
    if period == "expired":
        metadata["schedule_expires_at"] = (now - timedelta(seconds=1)).isoformat()
    if period == "early":
        metadata["scheduled_start_at"] = (now + timedelta(hours=1)).isoformat()
    state = {"job_id": "pair", "status": "queued", "metadata": metadata, "config": {}, "prepared_payload": source()}
    calls = []
    monkeypatch.setattr(runner, "_load_job", lambda _: deepcopy(state))
    monkeypatch.setattr(runner, "_save_job", lambda record: state.update(deepcopy(record)))
    monkeypatch.setattr(runner, "_release_concurrency_slot", lambda: None)
    monkeypatch.setattr(runner, "_create_automatic_deep_verifications", lambda _: calls.append("deep"))
    monkeypatch.setattr(runner, "_scheduled_final_traffic_validation_error", lambda _: None)
    def audit(*args, **kwargs):
        assert kwargs["require_fresh_final_traffic"] is True
        calls.append("audit")
        return {"audit": True}
    def pm(*args, **kwargs):
        calls.append("pm")
        kwargs["check_canceled"]()
        kwargs["checkpoint"]({"analysis_kind": "pm_return", "status": "running"})
        return {"analysis_kind": "pm_return", "status": "complete"}
    monkeypatch.setattr(runner, "run_backend_planner_with_prepared_data", audit)
    monkeypatch.setattr(paired_return, "run_return_measurement", pm)
    monkeypatch.setattr(sys, "argv", ["backend_job_runner.py", "pair"])
    assert runner.main() == (1 if period in {"expired", "early"} else 0)
    assert calls == (["audit", "deep"] if period == "am" else ["pm"] if period == "pm" else [])
