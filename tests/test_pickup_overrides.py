from copy import deepcopy
import importlib
from pathlib import Path
import sqlite3
import os
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "apps", ROOT / "apps/client", ROOT / "apps/backend"):
    sys.path.insert(0, str(directory))

import pickup_overrides as overrides
import client_runtime as runtime
import planner_core as core
import api_app as api

ADDRESS = "\u9752\u677e\u8def\u94f6\u674f\u8def\u516c\u4ea4\u7ad9"
BODY = {"country": "China", "city": "Shanghai", "address": ADDRESS,
        "lat": 31.2, "lng": 121.43, "poi_id": "test-station", "poi_name": ADDRESS,
        "reason": "Operator confirmed the pickup road.", "expected_revision": 0, "confirm": True}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("BRP_PICKUP_OVERRIDES_DB_PATH", str(tmp_path / "pickups.sqlite"))
    monkeypatch.setattr(runtime, "run_geocode_provider", lambda *_: pytest.fail("No provider calls"))
    monkeypatch.setattr(runtime, "save_json_cache", lambda *_: pytest.fail("No cache mutation"))


def save(body=None, operator="admin@example.test"):
    payload = body or BODY
    city_code, address, point = overrides.prepare_confirmation(payload, runtime)
    return overrides.change_pickup_correction(city_code=city_code, address=address, point=point,
        operator=operator, reason=payload["reason"], expected_revision=payload["expected_revision"])


def test_absent_registry_reads_do_not_create_files(tmp_path):
    assert overrides.read_pickup_correction("310000", ADDRESS) is None
    assert not (tmp_path / "pickups.sqlite").exists()


def test_versioned_save_retry_conflict_and_reversible_deactivation(tmp_path):
    row = save()
    assert row["revision"] == 1 and row["active"]
    assert save() == row
    with pytest.raises(overrides.PickupRevisionConflict):
        save({**BODY, "lat": 31.201})
    disabled = save({**BODY, "active": False, "expected_revision": 1, "reason": "Undo correction."})
    assert disabled["revision"] == 2 and not disabled["active"]
    assert overrides.confirmed_pickup("310000", ADDRESS) is None
    assert save({**BODY, "expected_revision": 2})["revision"] == 3
    with sqlite3.connect(tmp_path / "pickups.sqlite") as db:
        assert db.execute("SELECT revision FROM pickup_corrections ORDER BY revision").fetchall() == [(1,), (2,), (3,)]


def test_independent_process_reads_never_overwrite_confirmed_location(monkeypatch):
    save()
    first, _, changed = runtime.resolve_geocoded_point("China", "Shanghai", ADDRESS)
    assert not changed
    old = {"provider": "amap", "lat": 31.25, "lng": 121.45, "address": ADDRESS}
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {runtime.geocode_cache_key("China", "Shanghai", ADDRESS): old})
    before = deepcopy(runtime.GEOCODE_CACHE)
    save({**BODY, "lng": 121.431, "expected_revision": 1})
    fresh = json.loads(subprocess.check_output([sys.executable, "-c",
        "import json,pickup_overrides; print(json.dumps(pickup_overrides.confirmed_pickup('310000', " + repr(ADDRESS) + ")))"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "apps")}, text=True))
    assert fresh["lng"] == 121.431 and fresh["pickup_override_revision"] == 2
    second, warning, changed = runtime.resolve_geocoded_point("China", "Shanghai", ADDRESS)
    assert first["lng"] == 121.43 and second["lng"] == 121.431
    assert second["pickup_override_revision"] == 2 and warning is None and not changed
    assert runtime.GEOCODE_CACHE == before


@pytest.mark.parametrize("city", ["Shanghai", "\u4e0a\u6d77", "\u4e0a\u6d77\u5e02"])
def test_city_aliases_share_only_the_exact_confirmed_address(city):
    save()
    point, warning, changed = runtime.resolve_geocoded_point("China", city, ADDRESS)
    assert point["pickup_precision_status"] == "operator_confirmed" and not warning and not changed
    assert overrides.confirmed_pickup("110000", ADDRESS) is None
    assert overrides.confirmed_pickup("310000", ADDRESS + "\u4e1c\u95e8") is None


@pytest.mark.parametrize("backend", [False, True])
def test_new_preparation_preserves_order_riders_school_and_zero_rider_stop(monkeypatch, backend):
    save()
    module = core.load_legacy_planner() if backend else runtime
    old = {"provider": "amap", "lat": 31.25, "lng": 121.45,
           "plot_lat": 31.252, "plot_lng": 121.446, "address": ADDRESS}
    key = module.geocode_cache_key("China", "Shanghai", ADDRESS)
    monkeypatch.setattr(module, "GEOCODE_CACHE", {key: deepcopy(old)})
    monkeypatch.setattr(module, "save_json_cache", lambda *_: pytest.fail("No cache writes"))
    records = [{"country": "China", "city": "Shanghai", "address": ADDRESS, "passenger_count": n}
               for n in (0, 4, 0, 2)]
    original = deepcopy(records)
    points, _ = module.geocode_records(records)
    assert records == original and module.GEOCODE_CACHE[key] == old
    assert [p["passenger_count"] for p in points] == [0, 4, 0, 2]
    assert [p["is_depot"] for p in points] == [True, False, False, False]
    assert all(p["lng"] == BODY["lng"] and p["pickup_override_revision"] == 1 for p in points)
    if backend:
        assert module.geocode_query("China", "Shanghai", ADDRESS)["lng"] == BODY["lng"]


def test_map_payload_keeps_native_and_plot_coordinates_separate():
    save()
    point = overrides.confirmed_pickup("310000", ADDRESS)
    demand = importlib.import_module("demand_routing")
    payload = demand._point_payload(point)
    assert payload["lat"] == BODY["lat"] and payload["coordinate_system"] == "GCJ02"
    plot = demand._osrm_point_payload(payload)
    assert (plot["lat"], plot["lng"]) == (point["plot_lat"], point["plot_lng"])
    assert plot["coordinate_system"] == "WGS84"
    assert demand._osrm_point_payload(plot) == plot
    assert payload["pickup_override_revision"] == 1


@pytest.fixture
def client(monkeypatch):
    service = api.backend_service
    monkeypatch.setattr(service, "SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(service, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(service, "DEV_USER_EMAIL", "")
    monkeypatch.setattr(service, "_is_admin_email", lambda email: email == "admin@example.test")
    monkeypatch.setattr(service, "_start_job_scheduler", lambda: pytest.fail("No scheduler"))
    instance = TestClient(api.app, headers={"Authorization": "Bearer test-token", "X-BRP-User-Email": "admin@example.test"})
    yield instance
    instance.close()


def test_confirmation_api_requires_admin_and_service_auth(client, tmp_path):
    for method in ("get", "post"):
        kwargs = {"params": {k: BODY[k] for k in ("country", "city", "address")}} if method == "get" else {"json": BODY}
        call = getattr(client, method)
        assert call("/api/pickup-corrections", headers={"Authorization": "Bearer wrong"}, **kwargs).status_code == 401
        assert call("/api/pickup-corrections", headers={"X-BRP-User-Email": "viewer@example.test"}, **kwargs).status_code == 403
    assert not (tmp_path / "pickups.sqlite").exists()


@pytest.mark.parametrize("change", [
    {"confirm": 1}, {"confirm": False}, {"expected_revision": True}, {"expected_revision": "0"},
    {"operator": "forged"}, {"city": "Beijing"}, {"country": "South Korea"}, {"city": "unknown"},
    {"lat": "31.2"}, {"lng": True}, {"lat": 91}, {"poi_id": ""}, {"poi_name": ""},
    {"reason": ""}, {"address": ""}, {"active": 0},
])
def test_confirmation_rejects_unbounded_ambiguous_or_forged_inputs(client, change, tmp_path):
    response = client.post("/api/pickup-corrections", json={**BODY, **change})
    assert response.status_code == 422, response.text
    assert not (tmp_path / "pickups.sqlite").exists()


def test_api_save_read_retry_and_undo_without_provider_or_job_mutation(client):
    query = {k: BODY[k] for k in ("country", "city", "address")}
    assert client.get("/api/pickup-corrections", params=query).json()["revision"] == 0
    response = client.post("/api/pickup-corrections", json=BODY)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["operator"] == "admin@example.test" and row["revision"] == 1
    assert client.post("/api/pickup-corrections", json=BODY).json() == row
    assert client.get("/api/pickup-corrections", params=query).json() == row
    assert client.post("/api/pickup-corrections", json={**BODY, "lng": 121.431}).status_code == 409
    response = client.post("/api/pickup-corrections", json={**BODY, "active": False, "expected_revision": 1})
    assert response.status_code == 200 and not response.json()["active"]


def test_disabling_correction_restores_usable_old_cache_without_geocoding(monkeypatch):
    save()
    save({**BODY, "active": False, "expected_revision": 1})
    point = {"provider": "amap", "lat": 31.25, "lng": 121.45, "address": ADDRESS,
             "formatted_address": ADDRESS, "adcode": "310105"}
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {runtime.geocode_cache_key("China", "Shanghai", ADDRESS): point})
    result, _, _ = runtime.resolve_geocoded_point("China", "Shanghai", ADDRESS)
    assert (result["lat"], result["lng"]) == (point["lat"], point["lng"])
