from copy import deepcopy
from datetime import timedelta

import pytest

from test_google_final_validation import client, NOW, POINTS, response, body_points
from test_google_timing_entrypoints import context, CONFIG, points
from test_google_pickup_isolation import two_routes
from test_direct_school_analysis import fake_osrm
import google_final_validation as google
import google_pickup_points as pickup
import direct_school_analysis as direct


def legacy_point():
    return {"address": "Test (East gate)", "provider": "amap", "country": "China",
            "city": "Shanghai", "lat": 31.2, "lng": 121.4, "plot_lat": 31.2, "plot_lng": 121.4,
            "formatted_address": "Test", "geocode_level": "residential",
            "pickup_precision_status": "needs_review", "pickup_precision_issues": ["pickup_entrance_unconfirmed"]}


def gate_point(gate):
    point = legacy_point()
    point.update(address="\u6d4b\u8bd5\u82b1\u56ed("+gate+")", formatted_address="\u6d4b\u8bd5\u82b1\u56ed")
    return point


def matched_candidate(point):
    return {**point, "geocode_level": "poi", "amap_poi_id": "test-gate",
        "amap_poi_name": point["address"], "formatted_address": point["address"],
        "pickup_entrance_source": "named_gate_poi", "pickup_precision_status": "matched",
        "pickup_resolution_status": "matched", "pickup_precision_issues": [],
        "plot_lat": 31.201, "lat": 31.201}


def test_old_and_new_identity_failures_are_resolved_once_without_mutation():
    old = gate_point("\u4e1c\u95e8")
    saved = deepcopy(old)
    calls = []
    def lookup(point, check, count):
        check(); count(); calls.append(point)
        return matched_candidate(point)
    resolver = pickup.PickupResolver(lookup=lookup)
    first = resolver.resolve(old)
    second = resolver.resolve({**old, "passenger_count": 12, "node_id": 9})
    assert len(calls) == 1 and resolver.api_calls == 1
    assert old == saved and first["plot_lat"] == 31.201
    assert second["passenger_count"] == 12 and second["node_id"] == 9
    assert first["pickup_resolution_evidence"]["original_coordinate"] == [31.2, 121.4]
    newer = {**old, "pickup_resolution_status": "reference_only"}
    assert resolver.resolve(newer)["plot_lat"] == first["plot_lat"]


def test_unresolved_is_local_and_not_retried_for_each_route(context):
    bad = gate_point("\u4e1c\u95e8")
    calls = []
    def lookup(point, check, count):
        calls.append(point); count()
        return {**point, "pickup_resolution_status": "reference_only"}
    context.session.pickups.lookup = lookup
    for _ in range(2):
        with pytest.raises(google.ValidationUnavailable, match="identity_unresolved") as error:
            context.route([bad, points()[-1]])
        assert google.is_local_measurement_error(error.value)
        assert error.value.details["point_index"] == 0
    assert len(calls) == 1 and context.session.client.calls == 0
    assert context.state["pickup_resolution_api_calls"] == 1


def test_confirmed_override_and_non_identity_warning_do_not_lookup():
    resolver = pickup.PickupResolver(lookup=lambda *args: pytest.fail("unexpected lookup"))
    confirmed = {**gate_point("\u4e1c\u95e8"), "pickup_precision_status": "operator_confirmed", "pickup_override_revision": 3}
    assert resolver.resolve(confirmed) == confirmed
    point = {**legacy_point(), "address": "Test", "formatted_address": "Test",
             "pickup_precision_issues": ["coarse_or_unknown_geocode_precision"]}
    assert resolver.resolve(point) == point


def test_different_gates_same_coordinate_never_become_zero_leg(context):
    values = [{**gate_point(g), "pickup_precision_status": "operator_confirmed", "pickup_override_revision": 1}
              for g in ("\u4e1c\u5317\u95e8", "\u4e1c\u5357\u95e8")]
    with pytest.raises(google.ValidationUnavailable, match="identity_conflict") as error:
        context.route(values)
    assert error.value.details["other_point_index"] == 0
    assert context.session.client.calls == 0


def native_zero(point):
    value = response([point, point], 0)
    route = value["routes"][0]
    route.pop("distanceMeters")
    leg = route["legs"][0]
    leg.pop("distanceMeters")
    leg["polyline"]["geoJsonLinestring"]["coordinates"] = [list(point[::-1])]
    return value


def test_real_zero_leg_keeps_dwell_and_native_evidence(client):
    client.transport = lambda body: native_zero(body_points(body)[0])
    value = google.ValidationSession(client).measure([POINTS[0], POINTS[0]], NOW+timedelta(days=1), [60, 120])
    assert value.drive_s == 0 and value.dwell_s == 180
    assert value.arrival == value.departure + timedelta(seconds=180)
    assert value.legs[0]["zero_length"] and value.legs[0]["distance_m"] == 0
    assert client.calls == 1


def test_single_point_not_accepted_for_distinct_requested_points(client):
    other = (POINTS[0][0]+0.0001, POINTS[0][1])
    client.transport = lambda body: native_zero(POINTS[0])
    with pytest.raises(google.ValidationUnavailable, match="geometry_missing"):
        client.route([POINTS[0], other], NOW+timedelta(days=1))


@pytest.mark.parametrize("code", sorted(google.LOCAL_MEASUREMENT_ERRORS))
def test_each_local_error_preserves_other_route_and_rider_counts(context, monkeypatch, code):
    monkeypatch.setattr(direct, "_osrm_leg", fake_osrm)
    original = context.route
    def route(points, **kwargs):
        if any(p["address"] == "Far stop" for p in points):
            raise google.ValidationUnavailable(code, details={"address": "Far stop"})
        return original(points, **kwargs)
    monkeypatch.setattr(context, "route", route)
    checkpoints = []
    result = direct.run_direct_school_analysis(two_routes(), CONFIG, timing_context=context, checkpoint=checkpoints.append)
    assert result["status"] == "partial" and result["measurement_attempts_complete"]
    rows = {row["route_id"]:row for row in result["routes"]}
    assert rows["R2"]["status"] == "resolved" and rows["R1"]["status"] == "failed"
    assert rows["R1"]["riders"] > 0
    assert not result["operational_conclusion"]["final"]["all_measured_routes_within_window"]
    assert checkpoints[-1]["progress"]["pickup_resolution_api_calls"] == 0


@pytest.mark.parametrize("code", ["google_response_invalid", "google_http_403", "google_budget_cap", "google_pickup_lookup_unavailable"])
def test_systemic_errors_are_not_local(code):
    assert not google.is_local_measurement_error(google.ValidationUnavailable(code))


def test_reuse_requires_same_points_departure_and_fresh_task_receipt(client):
    departure = NOW+timedelta(days=1)
    first = client.route(POINTS, departure)
    first[0]["duration_s"] = 9999
    assert client.route(POINTS, departure)[0]["duration_s"] == 600
    assert client.calls == 1 and client.cache_hits == 1
    client.route(POINTS, departure+timedelta(minutes=1))
    changed = [(POINTS[0][0]+0.00001, POINTS[0][1]), *POINTS[1:]]
    client.route(changed, departure)
    assert client.calls == 3
    client.now = lambda: NOW+timedelta(minutes=11)
    client.route(POINTS, departure)
    assert client.calls == 4


def test_failed_receipts_are_never_reused(client):
    client.transport = lambda body: {"routes": []}
    for _ in range(2):
        with pytest.raises(google.ValidationUnavailable):
            client.route(POINTS, NOW+timedelta(days=1))
    assert client.calls == 2 and not client.measurements


def test_resolution_updates_map_coordinates_and_preserves_input(context, monkeypatch):
    payload = two_routes()
    saved = deepcopy(payload)
    monkeypatch.setattr(direct, "_osrm_leg", fake_osrm)
    original = context.session.pickups.resolve
    def resolve(point):
        result = original(point)
        if point["address"] == "Near stop":
            result.update(plot_lat=31.23, plot_lng=121.43)
        return result
    monkeypatch.setattr(context.session.pickups, "resolve", resolve)
    result = direct.run_direct_school_analysis(payload, CONFIG, timing_context=context)
    row = next(row for row in result["stops"] if row["address"] == "Near stop")
    assert (row["lat"], row["lng"]) == (31.23, 121.43)
    assert payload == saved
    assert result["routes"][0]["route_evidence"]["requested_waypoints"][1]["plot_lat"] == 31.23


def test_exact_compound_address_outranks_unit_address_without_cache_write(monkeypatch):
    import client_runtime as runtime
    road = "\u6d4b\u8bd5\u8def100\u5f04"
    point = {**legacy_point(), "address": road+"\u95e8\u53e3", "formatted_address": road}
    def poi(identity, address, location):
        return {"id":identity, "name":"\u6d4b\u8bd5\u82b1\u56ed", "address":address,
            "location":location, "entr_location":location, "type":"\u4f4f\u5b85\u533a",
            "adcode":"310000", "cityname":"\u4e0a\u6d77\u5e02"}
    raw = {"pois":[poi("compound",road,"121.4,31.2"),poi("units",road+"1-10\u53f7","121.41,31.21")]}
    original = deepcopy(raw)
    calls = []
    def request(endpoint, params, limiter):
        calls.append(params)
        return deepcopy(raw)
    monkeypatch.setattr(runtime,"amap_request_json",request)
    monkeypatch.setattr(runtime,"is_plausible_geocode_result",lambda *args, **kwargs:True)
    resolved = pickup.PickupResolver().resolve(point)
    assert resolved["amap_poi_id"] == "compound"
    assert resolved["pickup_entrance_source"] == "provider_entr_location"
    assert len(calls) == 1 and calls[0]["keywords"] == point["address"]
    assert raw == original


def test_lookup_cancellation_cannot_be_swallowed_by_resolver(monkeypatch):
    import client_runtime as runtime
    monkeypatch.setattr(runtime,"amap_request_json",lambda *args:pytest.fail("called after cancel"))
    def canceled():
        raise InterruptedError("canceled")
    with pytest.raises(InterruptedError):
        pickup._lookup(gate_point("\u4e1c\u95e8"),canceled,lambda:pytest.fail("charged after cancel"))


def test_unproven_coincidence_is_rejected_but_same_service_identity_is_allowed(context):
    first = points()[0]
    second = {**first,"address":"Different site"}
    with pytest.raises(google.ValidationUnavailable,match="identity_conflict"):
        context.route([first,second])
    assert context.session.client.calls == 0
    context.session.client.transport = lambda body:native_zero(body_points(body)[0])
    result = context.route([first,deepcopy(first)],dwell_s=[60,120])
    assert result["duration_s"] == 0 and result["stop_service_time_s"] == 180


def test_reuse_preserves_receipt_time_and_cancellation(client):
    departure = NOW+timedelta(days=1)
    client.route(POINTS,departure)
    client.now = lambda:NOW+timedelta(minutes=5)
    assert client.route(POINTS,departure)[0]["provider_called_at"] == NOW.isoformat()
    def canceled():
        raise InterruptedError("canceled")
    client.check_canceled = canceled
    with pytest.raises(InterruptedError):
        client.route(POINTS,departure)
    assert client.calls == 1
