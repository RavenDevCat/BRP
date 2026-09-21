from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest
import google_geocoding as geo
import google_final_validation as google
import client_runtime as runtime

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def payload(lat=31.2, lng=121.4, **extra):
    return {"status": "OK", "results": [{"place_id": "test-place", "types": ["street_address"],
        "formatted_address": "Shanghai Test 1", "geometry": {"location_type": "ROOFTOP",
        "location": {"lat": lat, "lng": lng}}, **extra}]}


def point(address="Test 1", city="Shanghai"):
    return {"country": "China", "city": city, "address": address, "requested_address": address,
            "provider": "amap", "lat": 31.25, "lng": 121.5, "plot_lat": 31.24, "plot_lng": 121.49,
            "amap_poi_id": "wrong", "pickup_precision_status": "operator_confirmed",
            "pickup_override_revision": 1, "passenger_count": 3, "node_id": 4, "is_depot": False}


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "is_plausible_geocode_result", lambda *a, **k: True)
    monkeypatch.setattr(runtime, "amap_request_json", lambda *a, **k: pytest.fail("AMap called"))
    def lookup(country, city, address, budget, check, count):
        check(); count()
        return payload(formatted_address="Shanghai " + address)
    return geo.GoogleGeocodeResolver("unit", lookup=lookup, path=tmp_path/"google.json", now=lambda: NOW)


def test_same_address_separate_provider_cache_and_no_gcj_conversion(resolver, monkeypatch):
    original = point()
    saved = deepcopy(original)
    monkeypatch.setattr(runtime, "gcj02_to_wgs84", lambda *a: pytest.fail("double conversion"))
    legacy = deepcopy(runtime.GEOCODE_CACHE)
    result = resolver.resolve(original)
    assert original == saved and runtime.GEOCODE_CACHE == legacy
    assert (result["lat"], result["plot_lat"], result["lng"]) == (31.2, 31.2, 121.4)
    assert result["provider"] == "google" and result["coordinate_system"] == "WGS84"
    assert "amap_poi_id" not in result and "pickup_override_revision" not in result
    assert result["node_id"] == 4 and result["passenger_count"] == 3
    other = resolver.resolve({**original, "passenger_count": 8})
    assert other["passenger_count"] == 8 and resolver.api_calls == 1
    data = json.loads(resolver.path.read_text())
    assert len(data) == 1 and next(iter(data.values()))["provider"] == "google"
    assert "formatted_address" not in next(iter(data.values()))


def test_cache_shared_across_sessions_but_city_separate(resolver):
    resolver.resolve(point())
    new = geo.GoogleGeocodeResolver("second", lookup=resolver.lookup, path=resolver.path, now=lambda: NOW)
    new.resolve(point())
    assert new.api_calls == 0
    new.resolve(point(city="Beijing"))
    assert new.api_calls == 1 and len(json.loads(resolver.path.read_text())) == 2


def test_google_clear_does_not_clear_amap(resolver, monkeypatch):
    resolver.resolve(point())
    monkeypatch.setenv("BRP_GOOGLE_GEOCODE_CACHE_PATH", str(resolver.path))
    legacy = deepcopy(runtime.GEOCODE_CACHE)
    assert geo.clear_address("China", "Shanghai", "Test 1")["cleared"] == 1
    assert runtime.GEOCODE_CACHE == legacy and json.loads(resolver.path.read_text()) == {}


def test_real_street_only_partial_response_is_not_school(resolver):
    resolver.lookup = lambda *a: payload(partial_match=True, types=["route"])
    with pytest.raises(google.ValidationUnavailable, match="unresolved"):
        resolver.resolve(point("School road 2100"))
    assert next(iter(json.loads(resolver.path.read_text()).values()))["state"] == "unresolved"


def test_fleet_regeocodes_without_mutating_uploaded_cluster(resolver):
    original = {"school": {**point("School"), "status": "ok"},
                "clusters": [{"points": [point("Stop")]}]}
    saved = deepcopy(original)
    result = geo.refresh_fleet_points(original, resolver)
    assert original == saved
    assert result["school"]["provider"] == result["clusters"][0]["points"][0]["provider"] == "google"


def test_expired_cache_is_removed_not_used_on_failure(resolver):
    resolver.resolve(point())
    resolver.now = lambda: NOW+timedelta(days=30)
    resolver.lookup = lambda *a: {"status": "ZERO_RESULTS"}
    with pytest.raises(google.ValidationUnavailable, match="unresolved"):
        resolver.resolve(point())
    assert next(iter(json.loads(resolver.path.read_text()).values()))["state"] == "unresolved"


def test_corrupt_wrong_provider_future_cache_not_trusted(resolver):
    resolver.path.write_text('{"broken": {"provider": "amap"}}')
    resolver.resolve(point())
    data = json.loads(resolver.path.read_text())
    assert "broken" not in data
    entry = next(iter(data.values()))
    assert not geo.valid_entry({**entry, "resolved_at": (NOW+timedelta(days=1)).isoformat()}, NOW)


@pytest.mark.parametrize("value,code", [
    ({"status": "ZERO_RESULTS"}, "unresolved"),
    ({"status": "REQUEST_DENIED"}, "provider_rejected"),
    (payload(partial_match=True, formatted_address="Different place"), "unresolved"),
    (payload(types=["route"]), "unresolved"),
    (payload(lat=float("nan")), "unresolved"),
])
def test_bad_geocode_never_falls_back_or_caches(resolver, value, code):
    resolver.lookup = lambda *a: value
    with pytest.raises(google.ValidationUnavailable, match=code):
        resolver.resolve(point())
    if code == "provider_rejected":
        assert not resolver.path.exists()
    else:
        assert next(iter(json.loads(resolver.path.read_text()).values()))["state"] == "unresolved"


def test_ambiguous_geocode_local_failure(resolver):
    value = payload()
    value["results"] += payload(lat=31.21)["results"]
    resolver.lookup = lambda *a: value
    with pytest.raises(google.ValidationUnavailable) as failure:
        resolver.resolve(point())
    assert str(failure.value) == "google_geocode_ambiguous"
    assert google.is_local_measurement_error(failure.value)


def test_concurrent_json_writers_merge_and_reuse(resolver):
    def run(index):
        child = geo.GoogleGeocodeResolver(str(index), lookup=resolver.lookup,
            path=resolver.path, now=lambda: NOW)
        child.resolve(point(str(index % 3)))
        return child.api_calls
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(run, range(12))) == 3
    assert len(json.loads(resolver.path.read_text())) == 3


def test_cancellation_before_cache_and_call(resolver):
    def cancel():
        raise InterruptedError("canceled")
    resolver.check_canceled = cancel
    with pytest.raises(InterruptedError):
        resolver.resolve(point())
    assert resolver.api_calls == 0


def test_final_session_wires_google_resolver(resolver, monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_GOOGLE_GEOCODE_CACHE_PATH", str(resolver.path))
    from test_google_final_validation import response, body_points
    client = google.GoogleRoutesClient("actual", google.SqliteQuotaStore(tmp_path/"quota.sqlite"),
        transport=lambda b: response(body_points(b)), now=lambda: NOW)
    session = google.ValidationSession(client)
    assert isinstance(session.pickups, geo.GoogleGeocodeResolver)
    session.pickups.lookup = lambda country, city, address, *args: payload(
        lat=31.201 if address == "Other" else 31.2, formatted_address="Shanghai " + address)
    session.pickups.now = lambda: NOW
    points = session.pickups.resolve_points([point(), {**point(), "address": "Other", "requested_address": "Other"}])
    assert points[0]["provider"] == "google"


def test_preparation_google_on_and_off(resolver, monkeypatch):
    import client_core
    monkeypatch.setattr(geo, "GoogleGeocodeResolver", lambda *a, **k: resolver)
    records = [{"country": "China", "city": "Shanghai", "address": "School", "passenger_count": 0},
               {"country": "China", "city": "Shanghai", "address": "Stop", "passenger_count": 3}]
    monkeypatch.setattr(runtime, "resolve_geocoded_point", lambda *a, **k: pytest.fail("legacy resolved Google input"))
    data = client_core.prepare_client_payload(records, config=client_core.PlannerConfig(final_time_validation_mode="google"))
    assert all(p["provider"] == "google" for p in data["prepared_payload"]["original_points"])
    assert data["prepared_payload"]["original_points"][0]["is_depot"]
    assert not data["prepared_payload"]["original_points"][1]["is_depot"]
    monkeypatch.setattr(runtime, "geocode_records", lambda rows: ([], []))
    assert client_core.prepare_client_payload(records)["prepared_payload"]["original_points"] == []


def test_unresolved_school_stops_before_paid_passenger_queries(resolver):
    calls = []
    def lookup(*args):
        calls.append(args[2])
        return {"status": "ZERO_RESULTS"}
    resolver.lookup = lookup
    records = [{"country": "China", "city": "Shanghai", "address": address}
               for address in ("School", "Stop 1", "Stop 2")]
    with pytest.raises(RuntimeError, match="school"):
        runtime.geocode_records(records, resolver=resolver.resolve_address)
    assert 1 <= len(calls) <= 3 and all("School" in value for value in calls)
    assert not any("Stop" in value for value in calls)


def test_geocode_and_routes_share_monthly_cap(resolver, monkeypatch, tmp_path):
    import google_routes_transport
    from google_routes_quota import quota_periods
    monkeypatch.setattr(google, "require_available", lambda: None)
    path = tmp_path/"quota.sqlite"
    monkeypatch.setenv("BRP_GOOGLE_FINAL_QUOTA_DB", str(path))
    store = google.SqliteQuotaStore(path)
    monkeypatch.setattr(google.SqliteQuotaStore, "reserve_rate_limit", lambda *a, **k: 0)
    periods = quota_periods("budget", datetime.now(google.TZ))
    store.reserve_usage("google_routes", "compute_routes_pro", periods, count=9999)
    class Response:
        status_code = 200
        def json(self): return payload()
    monkeypatch.setattr(google_routes_transport, "post_geocode", lambda *a: Response())
    count = []
    geo.request_geocode("China", "Shanghai", "Test", "budget", lambda: None, lambda: count.append(1))
    with pytest.raises(google.ValidationUnavailable, match="budget_cap"):
        geo.request_geocode("China", "Shanghai", "Other", "budget", lambda: None, lambda: count.append(1))
    assert count == [1]


def test_relay_geocode_policy_is_strict():
    path = Path(__file__).resolve().parents[1]/"ops/relay/google_routes_relay.py"
    spec = importlib.util.spec_from_file_location("relay_for_geocoding", path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    request = {"budget_id": "test", "request": {"address": "Shanghai Test", "language": "zh-CN",
        "components": "country:CN", "region": "cn"}}
    assert relay.validate_geocode_request(request)[0] == "test"
    for key, value in [("key", "injected"), ("components", "country:US"), ("address", "")]:
        with pytest.raises(ValueError):
            relay.validate_geocode_request({**request, "request": {**request["request"], key: value}})
