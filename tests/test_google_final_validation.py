import importlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/backend"))
g = importlib.import_module("google_final_validation")
core = importlib.import_module("planner_core")
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
POINTS = [(31.2, 121.4), (31.201, 121.401), (31.202, 121.402)]


def response(points, duration=600):
    legs = []
    for a, b in zip(points, points[1:]):
        loc = lambda p: {"latLng": {"latitude": p[0], "longitude": p[1]}}
        legs.append({"duration": f"{duration}s", "distanceMeters": 1000,
                     "startLocation": loc(a), "endLocation": loc(b),
                     "polyline": {"geoJsonLinestring": {"type": "LineString", "coordinates": [a[::-1], b[::-1]]}}})
    return {"routes": [{"duration": f"{duration*len(legs)}s", "distanceMeters": 1000*len(legs), "legs": legs}]}


def body_points(body):
    return [g.location(x["location"]) for x in [body["origin"], *body["intermediates"], body["destination"]]]

@pytest.fixture
def client(tmp_path):
    store = g.SqliteQuotaStore(tmp_path / "quota.sqlite")
    store.reserve_rate_limit = lambda *args: 0
    return g.GoogleRoutesClient("test-task", store, transport=lambda body: response(body_points(body)), now=lambda: NOW)

def test_off_config_is_backward_compatible():
    a, b = core.build_planner_config({}), core.build_planner_config({"final_time_validation_mode": "legacy"})
    assert a == b
    records = [{"address": "test", "passenger_count": 1}]
    assert core.build_planner_cache_key(records, a) == core.build_planner_cache_key(records, b)
    assert "google_validation" not in core.build_planner_cache_key(records, a)

@pytest.mark.parametrize("mode", [True, False, "Google", "", None, "amap"])
def test_invalid_mode_rejected(mode):
    with pytest.raises(ValueError):
        core.build_planner_config({"final_time_validation_mode": mode})

@pytest.mark.parametrize("value", ["2030/01/02", "2030-99-10", "20300102"])
def test_invalid_service_date(value):
    with pytest.raises(ValueError):
        core.build_planner_config({"final_time_validation_mode": "google", "validation_service_date": value})

def test_default_availability_closed(monkeypatch):
    monkeypatch.delenv("BRP_GOOGLE_FINAL_ENABLED", raising=False)
    assert not g.availability()["available"]
    with pytest.raises(g.ValidationUnavailable):
        g.prepare_submission({"final_time_validation_mode": "google", "validation_service_date": "2030-01-02"})

def test_disabled_legacy_submission_unchanged():
    payload = {"stop_service_minutes": 1}
    assert g.prepare_submission(payload) is payload

def test_server_freezes_scheduled_date_and_new_budget(monkeypatch):
    monkeypatch.setattr(g, "require_available", lambda: None)
    payload = {"final_time_validation_mode": "google", "validation_budget_id": "user-supplied",
               "validation_service_date": "2030-01-01", "time_window_start": "06:30", "time_window_end": "08:00"}
    first = g.prepare_submission(payload, "2031-01-02")
    second = g.prepare_submission(payload, "2031-01-03")
    assert first["validation_service_date"] == "2031-01-02"
    assert first["validation_budget_id"] != second["validation_budget_id"] != "user-supplied"
    assert payload["validation_service_date"] == "2030-01-01"

def test_google_cache_identity_includes_date_and_budget():
    payload = {"final_time_validation_mode": "google", "validation_service_date": "2030-01-02", "validation_budget_id": "one"}
    a = core.build_planner_config(payload)
    b = core.build_planner_config({**payload, "validation_service_date": "2030-01-03"})
    assert core.build_planner_cache_key([], a) != core.build_planner_cache_key([], b)

def test_request_native_fields_and_no_reordering(client):
    bodies = []
    def transport(body):
        bodies.append(body)
        return response(body_points(body))
    client.transport = transport
    legs = client.route(POINTS, NOW+timedelta(days=1))
    assert len(legs) == 2
    assert bodies[0]["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert bodies[0]["optimizeWaypointOrder"] is False
    assert body_points(bodies[0]) == POINTS
    assert client.calls == 1

def test_no_past_departure_consumes_no_budget(client):
    with pytest.raises(g.ValidationUnavailable, match="past"):
        client.route(POINTS, NOW-timedelta(seconds=1))
    assert client.calls == 0

@pytest.mark.parametrize("case", ["missing", "legs", "snap", "duration", "distance", "nan", "geometry", "fallback"])
def test_bad_native_evidence_rejected_and_charged(client, case):
    bad = response(POINTS)
    route = bad["routes"][0]
    if case == "missing": bad = {"routes": []}
    if case == "legs": route["legs"].pop()
    if case == "snap": route["legs"][0]["startLocation"]["latLng"]["latitude"] = 32
    if case == "duration": route["duration"] = "9999s"
    if case == "distance": route["distanceMeters"] = 10000
    if case == "nan": route["legs"][0]["duration"] = "NaNs"
    if case == "geometry": route["legs"][0]["polyline"] = {}
    if case == "fallback": bad["fallbackInfo"] = {"routingMode": "FALLBACK_TRAFFIC_UNAWARE", "reason": "SERVER_ERROR"}
    client.transport = lambda body: bad
    with pytest.raises(g.ValidationUnavailable):
        client.route(POINTS, NOW+timedelta(days=1))
    usage = client.store.get_usage("google_routes", "compute_routes_pro", "task", "test-task")
    assert usage["attempted"] == 1 and usage["failed"] == 1

def test_task_cap_survives_client_restart(client):
    client.limits = (1, 10, 10)
    client.route(POINTS, NOW+timedelta(days=1))
    restart = g.GoogleRoutesClient("test-task", client.store, limits=(1, 10, 10), transport=client.transport, now=client.now)
    with pytest.raises(RuntimeError, match="cap"):
        restart.route(POINTS, NOW+timedelta(days=1))
    assert restart.calls == 0

def test_campaign_cap_shared_across_jobs(client):
    client.store.reserve_usage("google_routes", "compute_routes_pro", [("campaign", "google-final-pilot-v1", 500)], count=500)
    with pytest.raises(RuntimeError, match="cap"):
        client.route(POINTS, NOW+timedelta(days=1))
    assert client.store.get_usage("google_routes", "compute_routes_pro", "task", "test-task")["attempted"] == 0

def test_atomic_concurrent_budget(client):
    def reserve(_):
        try:
            client.store.reserve_usage("google_routes", "compute_routes_pro", [("task", "parallel", 3)])
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(reserve, range(12))) == 3

def test_cancellation_before_call_is_free(client):
    def cancel(): raise InterruptedError()
    client.check_canceled = cancel
    with pytest.raises(InterruptedError): client.route(POINTS, NOW+timedelta(days=1))
    assert client.calls == 0

def test_rolling_dwell_departures(client):
    times = []
    def transport(body):
        times.append(datetime.fromisoformat(body["departureTime"]))
        return response(body_points(body))
    client.transport = transport
    start = NOW+timedelta(days=1)
    measured = g.ValidationSession(client).measure(POINTS, start, [60, 60, 0])
    assert times == [start+timedelta(seconds=60), start+timedelta(seconds=720)]
    assert measured.arrival == start+timedelta(seconds=1320)

def test_reverse_departure_returns_queried_time(client):
    times = []
    start = NOW+timedelta(days=1)
    def transport(body):
        times.append(datetime.fromisoformat(body["departureTime"]))
        return response(body_points(body), 5400 if len(times) == 1 else 3600)
    client.transport = transport
    result = g.ValidationSession(client).validate(POINTS[:2], [0, 0], start, start+timedelta(hours=1.5), 1800, True)
    assert result.departure in times and result.arrival <= start+timedelta(hours=1.5)
    assert len(times) == 2

def test_earliest_infeasible_is_failed_measurement_not_fake_pass(client):
    client.transport = lambda body: response(body_points(body), 7200)
    start = NOW+timedelta(days=1)
    result = g.ValidationSession(client).validate(POINTS[:2], [0, 0], start, start+timedelta(hours=1), 3600, True)
    assert result.departure == start and result.arrival > start+timedelta(hours=1)

def test_round_limit_never_returns_unqueried_departure(client):
    client.transport = lambda body: response(body_points(body), 5400)
    start = NOW+timedelta(days=1)
    with pytest.raises(g.ValidationUnavailable, match="converged"):
        g.ValidationSession(client, max_rounds=1).validate(POINTS[:2], [0, 0], start, start+timedelta(hours=2), 1800, True)

def scenario_setup(client):
    config = core.PlannerConfig(final_time_validation_mode="google", validation_service_date="2030-01-02",
        validation_budget_id="test-task", service_direction="To School", stop_service_minutes=1)
    config._google_validation_session = g.ValidationSession(client)
    points = [{"plot_lat": p[0], "plot_lng": p[1], "passenger_count": 1} for p in POINTS]
    scenario = {"routes": [{"route_id": "R1", "nodes": [1, 2, 0], "time_s": 1260,
        "stop_service_time_s": 60, "leg_details": [{"duration_s": 600}, {"duration_s": 600}]}]}
    return config, points, scenario

def test_gate_native_evidence_and_time_impact_source(client):
    config, points, scenario = scenario_setup(client)
    gate = core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Current")
    assert gate["status"] == "passed" and gate["provider"] == "google_routes"
    assert len(scenario["routes"][0]["route_evidence"]["legs"]) == 2
    validator = core._build_final_time_impact_validator(scenario, points, config, 15)
    assert validator(deepcopy(scenario), points)["status"] == "passed"
    bad = deepcopy(scenario)
    bad["routes"][0]["final_route_traffic_gate"]["provider"] = "amap"
    with pytest.raises(g.ValidationUnavailable, match="source_mismatch"): validator(bad, points)

def test_partial_scenario_never_published(client):
    config, points, scenario = scenario_setup(client)
    scenario["routes"].append({"nodes": [1, 0], "time_s": 600, "stop_service_time_s": 120})
    original = deepcopy(scenario)
    with pytest.raises(g.ValidationUnavailable, match="dwell"):
        core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Strict")
    assert scenario == original

def test_non_china_and_amap_injection_rejected(client):
    config, points, scenario = scenario_setup(client)
    with pytest.raises(g.ValidationUnavailable, match="country"):
        core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "South Korea"}], "Current")
    with pytest.raises(g.ValidationUnavailable, match="amap"):
        core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Current", measurement_provider=object())
    assert client.calls == 0

def test_legacy_never_dispatches_google(monkeypatch):
    """OFF must never enter the optional implementation."""
    def unexpected(*args, **kwargs): raise AssertionError("Google called in legacy mode")
    monkeypatch.setattr(g, "attach_gate", unexpected)
    monkeypatch.setattr(core, "FINAL_ROUTE_TRAFFIC_VERIFICATION_ENABLED", False)
    scenario = {"routes": []}
    gate = core.attach_final_route_traffic_gate(object(), scenario, [], core.PlannerConfig(), [{"country": "China"}], "Current")
    assert gate["status"] == "disabled"


def test_native_shared_map_and_export_contract(client, monkeypatch):
    config, points, scenario = scenario_setup(client)
    core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Current")
    views = importlib.import_module("route_measurement_view")
    assert views.route_display_metrics(scenario["routes"][0]) == {
        "duration_s": 1260, "distance_m": 2000, "source": "measurement"}
    rendered = views.measured_route_views(scenario["routes"])
    assert rendered[0]["time_s"] == 1260
    assert len(rendered[0]["drawing_segments"]) == 2
    service = importlib.import_module("backend_service")
    def forbidden(*args, **kwargs): raise AssertionError("Map tried AMap fallback")
    monkeypatch.setattr(service, "_amap_display_geometry_for_route", forbidden)
    scenario["points"] = [{**p, "lat": p["plot_lat"], "lng": p["plot_lng"],
                           "address": f"Synthetic {i}", "country": "China", "city": "Shanghai"}
                          for i, p in enumerate(points)]
    job = {"config": config.__dict__, "result": {"structured_results": {"current_plan": scenario}}}
    data, error = service._build_job_map_payload(job, "current_plan", "current_plan_map", attach_impact=False)
    assert error is None
    assert data["routes"][0]["display_geometry_source"] == "google_routes"
    assert data["routes"][0]["evidence_status"] == "verified"
    assert data["routes"][0]["duration_s"] == 1260
    assert data["summary"]["measurement_unavailable_route_count"] == 0


def test_pm_dwell_and_legacy_window(client):
    config, points, scenario = scenario_setup(client)
    config.service_direction = "From School"
    config.time_window_start, config.time_window_end = "15:40", "17:40"
    scenario["routes"][0]["nodes"] = [0, 1, 2]
    scenario["routes"][0]["stop_service_time_s"] = 120
    gate = core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Protected")
    assert gate["status"] == "passed"
    assert scenario["routes"][0]["final_route_traffic_gate"]["verified_departure_label"] == "15:40"
    assert scenario["routes"][0]["final_route_traffic_gate"]["verified_total_duration_s"] == 1320


def test_google_window_is_authoritative_without_changing_legacy():
    payload = {"time_window_start": "06:00", "time_window_end": "08:15", "to_school_arrival_time": "08:00"}
    assert core.build_planner_config(payload).to_school_arrival_time == "08:00"
    assert core.build_planner_config({**payload, "final_time_validation_mode": "google"}).to_school_arrival_time == "08:15"


def test_configured_grace_is_preserved(client, monkeypatch):
    config, points, scenario = scenario_setup(client)
    config.time_window_end = "06:50"
    scenario["routes"][0]["time_s"] = 1320
    monkeypatch.setattr(core, "AM_ARRIVAL_GATE_GRACE_MINUTES", 1)
    gate = core.attach_final_route_traffic_gate(object(), scenario, points, config, [{"country": "China"}], "Current")
    assert gate["status"] == "passed"
    assert gate["max_time_window_overrun_minutes"] == 1
    assert scenario["routes"][0]["final_route_traffic_gate"]["grace_minutes"] == 1
