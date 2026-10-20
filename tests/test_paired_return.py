from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import sys

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/backend"))
from paired_return import (reverse_payload, route_timings, ensure_current_window, run_return_measurement,
                           build_return_workbook, create_pair)
from runtime_store_sqlite import SqliteRuntimeStore

NOW = datetime(2026, 10, 20, 7, 40, tzinfo=timezone.utc)


def source():
    stops = [{"route_id": "R1", "stop_sequence": i, "address": address, "is_depot": i == 2,
              "country": "China", "city": "Shanghai", "passenger_count": count}
             for i, (address, count) in enumerate((("A", 2), ("B", 0), ("School", 0)))]
    records = [stops[2], stops[0], stops[1]]
    return {"current_plan": {"service_direction": "To School", "stops": stops,
                             "routes": [{"geometry": [[0, 0], [1, 1]]}]},
            "input_records": deepcopy(records), "original_points": [
                {**s, "lat": 31 + i * .01, "lng": 121 + i * .01, "provider": "amap"} for i, s in enumerate(records)]}


def test_reverse_preserves_original_zero_rider_and_identity():
    prepared = source()
    original = deepcopy(prepared)
    pm = reverse_payload(prepared)
    assert prepared == original
    assert [s["address"] for s in pm["current_plan"]["stops"]] == ["School", "B", "A"]
    assert [s["source_stop_sequence"] for s in pm["current_plan"]["stops"]] == [2, 1, 0]
    assert [s["stop_sequence"] for s in pm["current_plan"]["stops"]] == [0, 1, 2]
    assert sum(s["passenger_count"] for s in pm["current_plan"]["stops"]) == 2
    assert "routes" not in pm["current_plan"]
    assert pm["original_points"] == original["original_points"]


@pytest.mark.parametrize("invalid", ["school", "duplicate", "direction"])
def test_invalid_topology_rejected(invalid):
    prepared = source()
    if invalid == "school":
        prepared["current_plan"]["stops"][-1]["is_depot"] = False
    elif invalid == "duplicate":
        prepared["current_plan"]["stops"][0]["stop_sequence"] = 1
    else:
        prepared["current_plan"]["service_direction"] = "From School"
    with pytest.raises(ValueError):
        reverse_payload(prepared)


@pytest.mark.parametrize("total,expected", [(3599, True), (3599.99, True), (3600, False), (3601, False)])
def test_highlight_uses_exact_seconds(total, expected):
    ordered = reverse_payload(source())["current_plan"]["stops"]
    data = route_timings(ordered, {"complete": True, "duration_s": total - 120,
                                  "leg_durations_s": [1000, total - 1120]}, 60, NOW)
    assert data["total_duration_s"] == total
    assert data["under_one_hour"] is expected
    assert data["stops"][1]["ride_duration_s"] == 1000
    assert data["stops"][2]["ride_duration_s"] == total - 60
    assert data["stops"][2]["arrival_at"] == (NOW + timedelta(seconds=total - 60)).isoformat()


@pytest.mark.parametrize("evidence", [
    {"complete": False, "duration_s": 100, "leg_durations_s": [50, 50]},
    {"complete": True, "duration_s": 100, "leg_durations_s": [100]},
    {"complete": True, "duration_s": 100, "leg_durations_s": [50, float("nan")]},
    {"complete": True, "duration_s": 200, "leg_durations_s": [50, 50]},
    {"complete": True, "duration_s": 100, "leg_durations_s": [True, 99]},
])
def test_missing_or_invalid_measurements_not_green(evidence):
    with pytest.raises(ValueError):
        route_timings(reverse_payload(source())["current_plan"]["stops"], evidence, 60, NOW)


def test_schedule_window():
    meta = {"schedule_expires_at": NOW.isoformat()}
    ensure_current_window(meta, NOW)
    with pytest.raises(ValueError, match="expired"):
        ensure_current_window(meta, NOW + timedelta(seconds=1))
    ensure_current_window({}, NOW)


def record(job_id, signature="same"):
    return {"job_id": job_id, "owner_email": "operator@example.com", "status": "scheduled",
            "metadata": {"pair_request_hash": signature}, "created_at": NOW.isoformat()}


def test_atomic_pair_idempotence_and_conflict(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    pair = [record("pair-am"), record("pair-pm")]
    assert store.insert_job_pair(pair) == pair
    assert store.insert_job_pair(pair) == pair
    restarted = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    assert restarted.insert_job_pair(pair) == pair
    with pytest.raises(ValueError, match="different settings"):
        restarted.insert_job_pair([record("pair-am", "new"), record("pair-pm", "new")])
    assert restarted.get_job("pair-am") == pair[0]


def test_pair_transaction_rolls_back(tmp_path, monkeypatch):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    upsert = store.upsert_job
    def fail_second(value, **kwargs):
        if value["job_id"] == "pair-pm":
            raise RuntimeError("injected disk failure")
        upsert(value, **kwargs)
    monkeypatch.setattr(store, "upsert_job", fail_second)
    with pytest.raises(RuntimeError):
        store.insert_job_pair([record("pair-am"), record("pair-pm")])
    assert store.get_job("pair-am") is None


def test_measurement_uses_reversed_native_route_not_direct_or_recovery(monkeypatch):
    import direct_school_analysis as analysis
    requested = []
    class Provider:
        def __init__(self, name, *, departure_time, api_call_limit):
            assert name == "amap"
            assert departure_time is None
            self.state = {"api_calls": 0}
        def route(self, points):
            requested.append([p["address"] for p in points])
            self.state["api_calls"] += 1
            return {"complete": True, "duration_s": 1800, "distance_m": 10000,
                    "leg_durations_s": [600, 1200], "geometry": [[121, 31], [121.1, 31.1]]}
    monkeypatch.setattr(analysis, "_osrm_leg", lambda a, b, cache: {"duration_s": 100, "distance_m": 100})
    result = run_return_measurement(reverse_payload(source()), {"stop_service_minutes": 1},
                                    provider_factory=Provider, now=lambda: NOW)
    assert requested == [["School", "B", "A"]]
    assert result["status"] == "complete"
    assert result["progress"]["provider_api_calls"] == 1
    assert result["routes"][0]["total_duration_s"] == 1920
    assert result["routes"][0]["stops"][-1]["ride_duration_s"] == 1860
    workbook = load_workbook(BytesIO(build_return_workbook({"result": result})))
    assert workbook["Return routes"]["F6"].value == 1920
    assert workbook["Return routes"]["G6"].value == "Yes / 是"
    assert workbook["Return routes"]["G6"].fill.fgColor.rgb == "00DCFCE7"


def test_google_rejected_before_provider_use():
    with pytest.raises(ValueError, match="existing map"):
        run_return_measurement(reverse_payload(source()), {"final_time_validation_mode": "google"})


def test_pair_concurrent_submission_inserts_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    pair = [record("am"), record("pm")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.insert_job_pair(deepcopy(pair)), range(2)))
    assert results[0] == results[1]
    assert len(store.list_jobs(include_all=True)) == 2
    am = store.get_job("am")
    am["status"] = "canceled"
    store.upsert_job(am)
    assert store.get_job("pm")["status"] == "scheduled"


def test_fleet_and_assignments_preserved_without_old_geometry():
    prepared = source()
    prepared["current_plan"].update(fleet=[{"route_id": "R1", "bus_type": "18-fbus"}],
                                     assignments=[{"route_id": "R1", "passenger_count": 2}])
    pm = reverse_payload(prepared)
    assert pm["current_plan"]["fleet"] == prepared["current_plan"]["fleet"]
    assert pm["current_plan"]["assignments"] == prepared["current_plan"]["assignments"]


def test_cancellation_happens_before_any_route_call(monkeypatch):
    calls = []
    class Provider:
        def __init__(self, *args, **kwargs):
            self.state = {}
        def route(self, points):
            calls.append(points)
    def canceled():
        raise RuntimeError("canceled")
    with pytest.raises(RuntimeError, match="canceled"):
        run_return_measurement(reverse_payload(source()), {"stop_service_minutes": 1},
                               provider_factory=Provider, check_canceled=canceled)
    assert calls == []


def test_history_includes_planned_time(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    scheduled = record("scheduled")
    scheduled["metadata"].update(scheduled_start_at=NOW.isoformat(), scheduled_trigger_label="15:40")
    store.upsert_job(scheduled)
    history = store.list_jobs(include_all=True)[0]
    assert history["scheduled_start_at"] == NOW.isoformat()
    assert history["scheduled_trigger_label"] == "15:40"


def test_partial_route_keeps_riders_and_is_not_green(monkeypatch):
    import direct_school_analysis as analysis
    class Provider:
        def __init__(self, *args, **kwargs):
            self.state = {}
        def route(self, points):
            raise RuntimeError("provider unavailable")
    monkeypatch.setattr(analysis, "_osrm_leg", lambda *args: {})
    result = run_return_measurement(reverse_payload(source()), {"stop_service_minutes": 1}, provider_factory=Provider)
    assert result["status"] == "partial"
    assert result["summary"]["rider_count"] == 2
    assert result["summary"]["under_one_hour_count"] == 0
    assert len(result["routes"][0]["stops"]) == 3


def test_real_submit_api_pair_and_result_permissions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api_app
    import backend_service as service
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    monkeypatch.setattr(service, "_runtime_sqlite_store", lambda: store)
    monkeypatch.setattr(service, "SCHEDULED_JOBS_ENABLED", True)
    prepared = source()
    prepared["current_plan"]["input_records"] = deepcopy(prepared["input_records"])
    monkeypatch.setattr(service, "_prepare_direct_school_upload", lambda _: (
        "paired-test.xlsx", deepcopy(prepared["current_plan"]), deepcopy(prepared), {}))
    app = api_app.app
    app.dependency_overrides[api_app.require_authorized_request] = lambda: None
    context = api_app.UserContext(email="operator@example.com", is_admin=False)
    app.dependency_overrides[api_app.current_user_context] = lambda: context
    try:
        client = TestClient(app)
        day = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
        payload = {"file_name": "paired-test.xlsx", "file_base64": "fixture", "scheduled_job": True,
                   "scheduled_date": day, "scheduled_time": "07:00", "return_scheduled_time": "15:40",
                   "submission_id": "test-pair", "analysis_config": {"stop_service_minutes": 1}}
        response = client.post("/api/distance-checker/direct-school/jobs", json=payload)
        assert response.status_code == 202, response.text
        jobs = response.json()["paired_jobs"]
        assert len(jobs) == 2
        assert [j["metadata"]["period"] for j in jobs] == ["am", "pm"]
        assert client.post("/api/distance-checker/direct-school/jobs", json=payload).json()["paired_jobs"] == jobs
        saved_pm = store.get_job(jobs[1]["job_id"])
        assert [s["address"] for s in saved_pm["prepared_payload"]["current_plan"]["stops"]] == ["School", "B", "A"]
        assert saved_pm["metadata"]["analysis_config"]["final_time_validation_mode"] == "legacy"
        assert saved_pm["metadata"]["paired_job_ids"]["am"] == jobs[0]["job_id"]
        bad = client.post("/api/distance-checker/direct-school/jobs", json={**payload, "return_scheduled_time": "06:00"})
        assert bad.status_code == 422
        context = api_app.UserContext(email="other@example.com", is_admin=False)
        hidden = client.get("/api/distance-checker/direct-school/jobs/" + jobs[1]["job_id"])
        assert hidden.status_code in {403, 404}
    finally:
        app.dependency_overrides.clear()
