from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from test_measurement_reviews import source, Provider, review, SqliteRuntimeStore
import api_app as api
from measurement_review_queue import MeasurementReviewQueue
from job_queue import JobConcurrencyGate


BODY = {"route_keys": ["current_plan:R1"], "request_key": "api-test",
        "provider_call_limit": 10, "confirm_provider_calls": True}
BASE = "/api/jobs/source/measurement-reviews"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    service = api.backend_service
    monkeypatch.setattr(service, "SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(service, "JOB_STORE", store)
    monkeypatch.setattr(service, "_runtime_sqlite_store", lambda: store)
    monkeypatch.setattr(service, "JOB_QUEUE_SCOPE", "test")
    monkeypatch.setattr(service, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(service, "DEV_USER_EMAIL", "")
    monkeypatch.setattr(service, "_is_admin_email", lambda email: email == "admin@example.test")
    schedule = Mock()
    queue = MeasurementReviewQueue(store=store, gate=JobConcurrencyGate(1, tmp_path / "slots", slot_attach_stale_seconds=30),
        queue_scope="test", runner_path=Path("unused"), base_dir=tmp_path,
        python_executable="unused", schedule_normal=schedule)
    monkeypatch.setattr(service, "JOB_QUEUE", SimpleNamespace(measurement_reviews=queue, schedule_queued_jobs=schedule))
    monkeypatch.setattr(service, "_start_job_scheduler", lambda: pytest.fail("No real scheduler in API tests"))
    # No context manager: do not enter the application's scheduler lifespan.
    client = TestClient(api.app, headers={"Authorization": "Bearer test-token", "X-BRP-User-Email": "admin@example.test"})
    yield client, store, schedule
    client.close()


def test_service_token_and_admin_required_before_any_mutation(fixture):
    client, store, schedule = fixture
    assert client.post(BASE, json=BODY, headers={"Authorization": "Bearer wrong"}).status_code == 401
    for email in ("owner@example.test", "stranger@example.test"):
        assert client.post(BASE, json=BODY, headers={"X-BRP-User-Email": email}).status_code == 403
    assert not store.list_route_measurement_reviews("source")
    schedule.assert_not_called()


@pytest.mark.parametrize("change", [
    {"confirm_provider_calls": False}, {"confirm_provider_calls": 1}, {"provider_call_limit": True},
    {"provider_call_limit": 0}, {"provider_call_limit": 501}, {"provider_call_limit": "10"},
    {"route_keys": []}, {"route_keys": ["r"] * 21}, {"owner_email": "forged@example.test"},
    {"request_key": ""}, {"routes": [{"points": []}]},
])
def test_request_requires_explicit_bounded_confirmation(fixture, change):
    client, store, schedule = fixture
    assert client.post(BASE, json={**BODY, **change}).status_code == 422
    assert not store.list_route_measurement_reviews("source")
    schedule.assert_not_called()


def test_create_is_idempotent_and_does_not_expose_worker_details(fixture):
    client, store, schedule = fixture
    original = deepcopy(store.get_job("source"))
    first = client.post(BASE, json=BODY)
    assert first.status_code == 200, first.text
    row = first.json()
    assert client.post(BASE, json=BODY).json() == row
    assert len(store.list_route_measurement_reviews("source")) == 1
    assert row["request"]["route_keys"] == BODY["route_keys"]
    assert not {"worker_token", "worker_pid", "queue_scope", "job_slot_path"} & row.keys()
    assert "points" not in str(row)
    assert store.get_job("source") == original
    assert schedule.call_count == 2
    assert client.post(BASE, json={**BODY, "provider_call_limit": 9}).status_code == 409


def test_source_acl_applies_to_risk_list_detail_and_control(fixture):
    client, store, schedule = fixture
    row = client.post(BASE, json=BODY).json()
    schedule.reset_mock()
    detail = f"{BASE}/{row['review_id']}"
    for path in ("/api/jobs/source/measurement-risk", BASE, detail):
        assert client.get(path, headers={"X-BRP-User-Email": "stranger@example.test"}).status_code == 403
        assert client.get(path, headers={"X-BRP-User-Email": "owner@example.test"}).status_code == 200
    assert client.post(detail + "/actions/cancel", headers={"X-BRP-User-Email": "owner@example.test"}).status_code == 403
    schedule.assert_not_called()
    assert store.get_route_measurement_review(row["review_id"])["status"] == "queued"


def test_shared_workspace_viewer_inherits_source_read_access(fixture, monkeypatch):
    client, _, schedule = fixture
    monkeypatch.setattr(api, "_workspace_item_role", lambda scope, job, context: "viewer" if context.email == "viewer@example.test" else None)
    assert client.get("/api/jobs/source/measurement-risk", headers={"X-BRP-User-Email": "viewer@example.test"}).status_code == 200
    schedule.assert_not_called()


def test_parent_mismatch_and_admin_actions(fixture):
    client, store, schedule = fixture
    row = client.post(BASE, json=BODY).json()
    detail = f"{BASE}/{row['review_id']}"
    store.upsert_job({**source(), "job_id": "other"})
    assert client.get(detail.replace("jobs/source", "jobs/other")).status_code == 404
    assert client.post(detail + "/actions/pause").json()["status"] == "paused"
    assert client.post(detail + "/actions/resume").json()["status"] == "queued"
    assert client.post(detail + "/actions/cancel").json()["status"] == "canceled"
    assert client.post(detail + "/actions/invalid").status_code == 409
    assert client.get(detail).json()["status"] == "canceled"


def test_risk_unfinished_source_is_unknown_and_read_only(fixture):
    client, store, schedule = fixture
    store.upsert_job({**source(), "status": "scheduled", "result": None})
    risk = client.get("/api/jobs/source/measurement-risk").json()
    assert risk["status"] == "unavailable" and risk["reason"] == "source_not_finished"
    assert client.post(BASE, json=BODY).status_code == 409
    schedule.assert_not_called()


def test_completed_detail_keeps_scope_limits_and_list_skips_large_snapshots(fixture):
    client, store, _ = fixture
    row = client.post(BASE, json=BODY).json()
    review.execute_saved_review(store, row["review_id"], "worker", provider_factory=Provider)
    detail = client.get(f"{BASE}/{row['review_id']}").json()
    assert detail["status"] == "succeeded" and detail["api_calls"] == 2
    assert detail["result"]["scope"] == "selected_routes_only"
    assert detail["result"]["time_window_revalidated"] is False
    assert detail["result"]["student_classification_recomputed"] is False
    assert store.list_route_measurement_reviews("source", include_result=False)[0]["result"] is None
    assert store.list_route_measurement_reviews("source")[0]["result"] == detail["result"]
    assert "result" not in client.get(BASE).json()["reviews"][0]


@pytest.mark.parametrize("mode", ["full_audit", "full_direct_school"])
def test_full_review_availability_is_native_read_only_and_acl_scoped(fixture, mode):
    from test_audit_measurement_review import source as audit_source
    from test_full_measurement_review import source as school_source
    client, store, schedule = fixture
    store.upsert_job(audit_source() if mode == "full_audit" else school_source())
    before = deepcopy(store.get_job("source"))
    for _ in range(2):
        response = client.get("/api/jobs/source/measurement-risk", headers={"X-BRP-User-Email": "owner@example.test"})
        assert response.status_code == 200, response.text
        capability = response.json()["full_review"]
        assert capability["mode"] == mode and capability["available"] is True
        assert capability["scope_summary"]["route_count"] > 0
        assert not {"points", "routes", "full_input", "before_analysis"} & capability.keys()
    assert client.get("/api/jobs/source/measurement-risk", headers={"X-BRP-User-Email": "stranger@example.test"}).status_code == 403
    assert store.get_job("source") == before
    assert not store.list_route_measurement_reviews("source")
    schedule.assert_not_called()


def test_risk_remains_visible_when_native_correction_input_is_missing(fixture):
    client, store, schedule = fixture
    before = deepcopy(store.get_job("source"))
    response = client.get("/api/jobs/source/measurement-risk")
    assert response.status_code == 200
    risk = response.json()
    assert risk["source_supported"] and risk["routes"]
    assert risk["full_review"]["available"] is False
    assert risk["full_review"]["reason"]
    assert not store.list_route_measurement_reviews("source")
    assert store.get_job("source") == before
    schedule.assert_not_called()
