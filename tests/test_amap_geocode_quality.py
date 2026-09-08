import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "apps", ROOT / "apps" / "client", ROOT / "apps" / "backend"):
    sys.path.insert(0, str(directory))

quality = importlib.import_module("amap_geocode_quality")
runtime = importlib.import_module("client_runtime")
core = importlib.import_module("planner_core")

ROAD_A = "\u9752\u677e\u8def"
ROAD_B = "\u94f6\u674f\u8def"
BUS = "\u516c\u4ea4\u7ad9"
REQUEST = ROAD_A + ROAD_B + BUS


def poi(name=REQUEST, location="121.430,31.200", **extra):
    return {"name": name, "location": location, "adcode": "310105", "pname": "\u4e0a\u6d77\u5e02",
            "address": ROAD_A, "type": BUS, **extra}


def test_coarse_road_and_residential_area_are_not_pickup_evidence():
    for level in ("\u9053\u8def", "\u4f4f\u5b85\u533a", ""):
        candidate = {"level": level, "formatted_address": REQUEST}
        assert "coarse_or_unknown_geocode_precision" in quality.amap_candidate_issues(REQUEST, candidate)


def test_poi_must_preserve_both_roads_and_bus_stop():
    wrong = poi("\u67ab\u6811\u8def" + ROAD_B + BUS)
    right = poi()
    assert quality.select_amap_pickup_candidate(REQUEST, [wrong, right], poi=True) == right
    assert quality.select_amap_pickup_candidate(REQUEST, [poi(ROAD_A + ROAD_B, type="shop")], poi=True) is None


def test_bus_stop_with_reversed_road_names_is_not_the_requested_station():
    reversed_stop = poi(ROAD_B + ROAD_A + BUS)
    correct = poi(ROAD_A + ROAD_B + "(" + BUS + ")")
    assert "requested_bus_stop_road_order_not_preserved" in quality.amap_candidate_issues(REQUEST, reversed_stop, poi=True)
    assert quality.select_amap_pickup_candidate(REQUEST, [reversed_stop, correct], poi=True) == correct


def test_ambiguous_bus_stop_does_not_select_first_or_shortest():
    with pytest.raises(quality.GeocodePrecisionError, match="Multiple"):
        quality.select_amap_pickup_candidate(REQUEST, [poi(), poi(location="121.431,31.200")], poi=True)


def test_distinct_poi_ids_at_same_location_are_still_ambiguous():
    with pytest.raises(quality.GeocodePrecisionError, match="Multiple"):
        quality.select_amap_pickup_candidate(REQUEST, [poi(id="one"), poi(id="two")], poi=True)


def test_invalid_prepared_coordinates_block_before_any_provider_request(monkeypatch):
    analysis = importlib.import_module("direct_school_analysis")
    monkeypatch.setattr(core, "load_legacy_planner", lambda: type("Planner", (), {"AMAP_KEY": "test"})())
    monkeypatch.setattr(core, "_amap_route_stats", lambda *_: pytest.fail("must not request routes for unverified pickups"))
    points = [{"provider": "amap", "address": REQUEST, "lat": float("nan"), "lng": 121.43},
              {"provider": "amap", "address": "school", "lat": 31.21, "lng": 121.44}]
    snapshot = [dict(point) for point in points]
    provider = analysis.FreshRouteProvider("amap", departure_time=None, api_call_limit=10)
    with pytest.raises(quality.GeocodePrecisionError, match="Re-prepare"):
        provider.route(points)
    assert provider.state["api_calls"] == 0
    assert provider.state["last_route_evidence"]["status"] == "needs_review"
    state = {}
    with pytest.raises(quality.GeocodePrecisionError):
        core._check_amap_pickup_precision(points, state)
    with pytest.raises(ValueError, match="stop cannot be skipped"):
        core._route_amap_points(points, {"nodes": [0, 1]}, state)
    assert state["last_route_evidence"]["issues"][0]["code"] == "pickup_precision_needs_review"
    assert points == snapshot
    service = importlib.import_module("backend_service")
    fleet = {"school": {"country": "China"}, "routes": [{"ordered_points": points}]}
    service._attach_fleet_route_measurements(fleet, service._client_module("demand_routing"))
    assert fleet["summary"]["route_measurement_review_count"] == 1
    assert fleet["routes"][0]["evidence_status"] == "needs_review"
    assert fleet["routes"][0]["ordered_points"] == snapshot


def test_fleet_point_payload_preserves_precision_evidence():
    demand = importlib.import_module("demand_routing")
    point = {"provider": "amap", "lat": 31.2, "lng": 121.43,
             "address": REQUEST, "geocode_level": "poi", "amap_poi_name": REQUEST,
             "geocode_quality_version": quality.GEOCODE_QUALITY_VERSION}
    payload = demand._point_payload(point)
    assert quality.reusable_amap_geocode(payload, REQUEST)


def test_insert_map_points_preserve_precision_and_wgs_boundary(monkeypatch):
    api = importlib.import_module("api_app")
    source = {"provider": "amap", "address": REQUEST, "lat": 31.2, "lng": 121.43,
              "plot_lat": 31.202, "plot_lng": 121.426,
              "geocode_quality_version": quality.GEOCODE_QUALITY_VERSION,
              "geocode_level": "poi", "amap_poi_name": REQUEST}
    planner = type("Planner", (), {"geocode_query": lambda *args: dict(source)})()
    monkeypatch.setattr(api.backend_service, "load_legacy_planner", lambda: planner)
    resolved, warnings = api._insert_geocode_stops([{"address": REQUEST}], "China", "Shanghai")
    assert not warnings
    point = api._insert_coord_payload(resolved[0], "China", "Shanghai")
    assert (point["lat"], point["lng"]) == (source["plot_lat"], source["plot_lng"])
    assert point["coordinate_system"] == "WGS84"
    assert quality.reusable_amap_geocode(point, REQUEST)
    point.pop("geocode_quality_version")
    state = {}
    assert core._route_amap_points([point, point], {"nodes": [0, 1]}, state)
    point["lng"] = float("nan")
    with pytest.raises(quality.GeocodePrecisionError):
        core._check_amap_pickup_precision([point, point], state)
    assert state["last_route_evidence"]["status"] == "needs_review"


def test_parking_and_tenant_cannot_replace_requested_landmark():
    requested = ROAD_A + "\u661f\u5149\u5546\u573a"
    parking = poi("\u661f\u5149\u5546\u573a\u505c\u8f66\u573a", type="\u505c\u8f66\u573a")
    tenant = poi("\u5065\u8eab\u4e2d\u5fc3(\u661f\u5149\u5546\u573a\u5e97)", type="fitness")
    assert quality.select_amap_pickup_candidate(requested, [parking, tenant], poi=True) is None


def test_precise_level_does_not_certify_a_different_landmark():
    requested = ROAD_A + "\u661f\u5149\u5546\u573a"
    wrong = {"level": "\u5174\u8da3\u70b9", "formatted_address": ROAD_A + "\u661f\u5149\u516c\u5bd3"}
    assert quality.select_amap_pickup_candidate(requested, [wrong]) is None
    assert quality.select_amap_pickup_candidate(requested, [poi("\u661f\u5149\u5546\u573a")], poi=True)


def test_residential_name_is_not_stripped_as_an_administrative_district():
    requested = "\u661f\u5149\u5c0f\u533a" + ROAD_A + "123\u53f7"
    assert quality._local_address(requested) == requested


def test_building_number_and_explicit_entrance_must_match():
    requested = ROAD_A + "123\u53f7(\u4e1c\u95e8)"
    candidate = {"level": "\u95e8\u724c\u53f7", "formatted_address": ROAD_A + "123\u53f7(\u897f\u95e8)"}
    assert "requested_entrance_not_preserved" in quality.amap_candidate_issues(requested, candidate)
    candidate["formatted_address"] = ROAD_A + "125\u53f7(\u4e1c\u95e8)"
    assert "requested_building_number_not_preserved" in quality.amap_candidate_issues(requested, candidate)


@pytest.mark.parametrize("backend", [False, True])
def test_both_clients_prefer_the_named_bus_stop_not_a_road_centroid(monkeypatch, backend):
    module = core.load_legacy_planner() if backend else runtime
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        assert params["city"] == "310000"
        if endpoint == "/v3/geocode/geo":
            return {"geocodes": [{"formatted_address": "\u4e0a\u6d77\u5e02" + ROAD_A,
                                   "level": "\u9053\u8def", "location": "121.43,31.20", "adcode": "310105"}]}
        assert params["citylimit"] == "true"
        return {"pois": [poi(ROAD_B + ROAD_A + BUS), poi()]}
    monkeypatch.setattr(module, "amap_request_json", fetch)
    point = module.amap_geocode_query("China", "Shanghai", REQUEST)
    assert calls == ["/v3/place/text"]
    assert point["pickup_precision_status"] == "matched"
    assert point["geocode_quality_version"] == quality.GEOCODE_QUALITY_VERSION
    assert quality.reusable_amap_geocode(point, REQUEST)
    point["amap_poi_name"] = "wrong stop"
    point["formatted_address"] = "wrong stop"
    assert "requested_road_not_preserved" in quality.amap_candidate_issues(REQUEST, point)


@pytest.mark.parametrize("backend", [False, True])
@pytest.mark.parametrize("poi_problem", ["missing", "exception", "ambiguous", "reversed_only"])
def test_bus_poi_problem_keeps_valid_geocode_visible_without_relocation(monkeypatch, backend, poi_problem):
    module = core.load_legacy_planner() if backend else runtime
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        if endpoint == "/v3/place/text":
            if poi_problem == "exception":
                raise RuntimeError("Provider unavailable")
            candidates = [] if poi_problem == "missing" else [poi(), poi(location="121.431,31.200")] if poi_problem == "ambiguous" else [poi(ROAD_B+ROAD_A+BUS)]
            return {"pois": candidates}
        return {"geocodes": [{"formatted_address": "\u4e0a\u6d77\u5e02" + ROAD_A,
                               "level": "\u9053\u8def", "location": "121.435,31.205", "adcode": "310105"}]}
    monkeypatch.setattr(module, "amap_request_json", fetch)
    point = module.amap_geocode_query("China", "Shanghai", REQUEST)
    assert calls == ["/v3/place/text", "/v3/geocode/geo"]
    assert (point["lat"], point["lng"]) == (31.205, 121.435)
    assert point["pickup_precision_status"] == "needs_review"
    assert quality.reusable_amap_geocode(point, REQUEST)


def test_ordinary_street_address_still_uses_geocoding_first(monkeypatch):
    calls = []
    address = ROAD_A + "123\u53f7"
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        assert endpoint == "/v3/geocode/geo"
        return {"geocodes": [{"formatted_address": "\u4e0a\u6d77\u5e02"+address, "level": "\u95e8\u724c\u53f7",
                               "location": "121.435,31.205", "adcode": "310105"}]}
    monkeypatch.setattr(runtime, "amap_request_json", fetch)
    assert runtime.amap_geocode_query("China", "Shanghai", address)["pickup_precision_status"] == "matched"
    assert calls == ["/v3/geocode/geo"]


def test_old_amap_cache_without_precision_remains_usable_without_relocation(monkeypatch):
    address = ROAD_A + "123\u53f7"
    key = runtime.geocode_cache_key("China", "Shanghai", address)
    old = {"provider": "amap", "lat": 31.2, "lng": 121.43,
           "formatted_address": "\u4e0a\u6d77\u5e02" + address, "adcode": "310105"}
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {key: old, "unrelated": {"keep": True}})
    calls = []
    def fresh(*args):
        calls.append(args)
        return {**old, "geocode_level": "\u95e8\u724c\u53f7", "geocode_quality_version": quality.GEOCODE_QUALITY_VERSION}
    monkeypatch.setattr(runtime, "run_geocode_provider", fresh)
    point, warning, changed = runtime.resolve_geocoded_point("China", "Shanghai", address)
    assert warning is None and changed and len(calls) == 0
    assert (point["lat"], point["lng"]) == (old["lat"], old["lng"])
    assert point["pickup_precision_status"] == "needs_review"
    assert runtime.GEOCODE_CACHE["unrelated"] == {"keep": True}
    assert point["geocode_quality_version"] == quality.GEOCODE_QUALITY_VERSION
    runtime.resolve_geocoded_point("China", "Shanghai", address)
    assert len(calls) == 0


@pytest.mark.parametrize("level", ["\u95e8\u5740", "\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9", "\u9053\u8def\u4ea4\u53c9\u8def\u53e3"])
def test_actual_provider_precision_levels_are_recognized(level):
    assert "coarse_or_unknown_geocode_precision" not in quality.amap_candidate_issues(REQUEST, {"level": level, "formatted_address": REQUEST})


def test_legacy_points_are_measurable_but_pickup_review_is_preserved(monkeypatch):
    points = [{"provider": "amap", "address": REQUEST, "lat": 31.2, "lng": 121.43},
              {"provider": "amap", "address": "school", "lat": 31.21, "lng": 121.44}]
    snapshot = [dict(point) for point in points]
    state = {}
    assert core._route_amap_points(points, {"nodes": [0, 1]}, state) == [(31.2, 121.43), (31.21, 121.44)]
    assert len(state["pickup_precision_reviews"]) == 2
    evidence = {"status": "verified", "duration_s": 600, "distance_m": 1000}
    monkeypatch.setattr(core, "measure_amap_route", lambda *_, **__: evidence)
    result = core._amap_route_stats(None, [(31.2, 121.43), (31.21, 121.44)], {}, state)
    assert len(result["pickup_precision_reviews"]) == 2
    assert points == snapshot


def test_failed_v1_precision_cache_is_retried_without_manual_cache_clear(monkeypatch):
    address = ROAD_A + "123\u53f7"
    key = runtime.geocode_cache_key("China", "Shanghai", address)
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {key: {"cache_status": "failed", "attempted_providers": ["amap"],
                                                      "geocode_quality_version": "amap-pickup-precision-v1"}})
    calls = []
    def fresh(*args):
        calls.append(args)
        return quality.annotate_amap_pickup({"provider": "amap", "lat": 31.2, "lng": 121.43,
                                            "formatted_address": address, "geocode_level": "\u95e8\u5740"}, address)
    monkeypatch.setattr(runtime, "run_geocode_provider", fresh)
    point, warning, changed = runtime.resolve_geocoded_point("China", "Shanghai", address)
    assert point and warning is None and changed and len(calls) == 1
    runtime.resolve_geocoded_point("China", "Shanghai", address)
    assert len(calls) == 1


@pytest.mark.parametrize("backend", [False, True])
def test_failed_school_cannot_promote_zero_passenger_waypoint_to_depot(monkeypatch, backend):
    module = core.load_legacy_planner() if backend else runtime
    monkeypatch.setattr(module, "GEOCODE_CACHE", {})
    monkeypatch.setattr(module, "save_json_cache", lambda *_: None)
    rows = [{"country": "China", "city": "Shanghai", "address": address, "passenger_count": 0}
            for address in ("school", "waypoint")]
    if backend:
        def geocode(_country, _city, address):
            if address == "school":
                raise ValueError("unresolved school")
            return {"address": address, "lat": 31.2, "lng": 121.4}
        monkeypatch.setattr(module, "geocode_query", geocode)
    else:
        monkeypatch.setattr(module, "resolve_geocoded_point", lambda _c, _city, address, _rows:
                            (None, {"address": address}, False) if address == "school" else
                            ({"address": address, "lat": 31.2, "lng": 121.4}, None, False))
    with pytest.raises(RuntimeError, match="school address"):
        module.geocode_records(rows)


RESIDENCE = "\u661f\u5149\u5c0f\u533a"
EAST_GATE = "\u4e1c\u95e8"
WEST_GATE = "\u897f\u95e8"


@pytest.mark.parametrize("requested,actual,matched", [
    ("8\u53f7\u95e8", "8\u53f7\u95e8", True),
    ("8\u53f7\u95e8", "18\u53f7\u95e8", False),
    ("1\u53f7\u95e8", "11\u53f7\u95e8", False),
    ("\uff18\u53f7\u95e8", "8\u53f7\u95e8", True),
    ("\u516b\u53f7\u95e8", "8\u53f7\u95e8", True),
    ("\u7b2c\u5341\u516b\u53f7\u95e8", "18\u53f7\u95e8", True),
    ("A\u95e8", "B\u95e8", False),
    ("A\u95e8", "A\u95e8", True),
    (EAST_GATE, "\u4e1c\u5357\u95e8", False),
    ("\u5357\u95e8", "\u4e1c\u5357\u95e8", False),
    (EAST_GATE, "\u4e1c\u5165\u53e3", True),
    ("\u4e1c\u95e8\u53e3", EAST_GATE, True),
])
def test_gate_identity_is_exact_not_a_substring_or_building_number(requested, actual, matched):
    address = ROAD_A + "123\u53f7 " + RESIDENCE + "(" + requested + ")"
    candidate = poi(RESIDENCE + "(" + actual + ")", address=ROAD_A + "123\u53f7", type="\u51fa\u5165\u53e3")
    issues = quality.amap_candidate_issues(address, candidate, poi=True)
    assert (not issues) == matched
    assert "requested_building_number_not_preserved" not in issues
    if not matched:
        assert "requested_entrance_not_preserved" in issues


def test_parent_location_cannot_impersonate_a_gate_in_its_address():
    address = RESIDENCE + "(" + EAST_GATE + ")"
    candidate = poi(RESIDENCE, address=address, type="\u4f4f\u5b85\u533a")
    issues = quality.amap_candidate_issues(address, candidate, poi=True)
    assert "requested_entrance_not_preserved" in issues
    assert "residential_entrance_unconfirmed" in issues


@pytest.mark.parametrize("backend", [False, True])
def test_residential_explicit_gate_uses_gate_poi_not_centroid(monkeypatch, backend):
    module = core.load_legacy_planner() if backend else runtime
    address = RESIDENCE + "(8\u53f7\u95e8)"
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        assert params["city"] == "310000" and params["citylimit"] == "true"
        assert params["extensions"] == "all"
        return {"pois": [poi(RESIDENCE, type="\u4f4f\u5b85\u533a"),
                          poi(RESIDENCE + "(18\u53f7\u95e8)", type="\u51fa\u5165\u53e3"),
                          poi(address, location="121.431,31.201", type="\u51fa\u5165\u53e3")]}
    monkeypatch.setattr(module, "amap_request_json", fetch)
    point = module.amap_geocode_query("China", "Shanghai", address)
    assert calls == ["/v3/place/text"]
    assert (point["lat"], point["lng"]) == (31.201, 121.431)
    assert point["pickup_precision_status"] == "matched"
    assert point["pickup_entrance_source"] == "named_gate_poi"
    assert point["address"] == address


@pytest.mark.parametrize("explicit", [False, True])
def test_provider_entrance_is_separate_from_parent_poi_and_cannot_substitute_explicit_gate(monkeypatch, explicit):
    address = RESIDENCE + ("(" + EAST_GATE + ")" if explicit else "")
    def fetch(endpoint, params, limiter):
        if endpoint == "/v3/place/text":
            return {"pois": [poi(RESIDENCE, type="\u4f4f\u5b85\u533a", entr_location="121.432,31.202")]}
        return {"geocodes": [{"formatted_address": address, "level": "\u4f4f\u5b85\u533a",
                               "location": "121.430,31.200", "adcode": "310105"}]}
    monkeypatch.setattr(runtime, "amap_request_json", fetch)
    point = runtime.amap_geocode_query("China", "Shanghai", address)
    if explicit:
        assert (point["lat"], point["lng"]) == (31.200, 121.430)
        assert point["pickup_precision_status"] == "needs_review"
    else:
        assert (point["lat"], point["lng"]) == (31.202, 121.432)
        assert point["pickup_precision_status"] == "matched"
        assert point["pickup_entrance_source"] == "provider_entr_location"
        assert point["amap_poi_location"] == "121.430,31.200"
        assert point["amap_poi_entr_location"] == "121.432,31.202"
        assert (point["plot_lat"], point["plot_lng"]) == runtime.gcj02_to_wgs84(31.202, 121.432)


@pytest.mark.parametrize("backend", [False, True])
@pytest.mark.parametrize("failure", ["missing", "exception", "multiple", "invalid_entrance", "centroid_only"])
def test_unconfirmed_entrances_keep_geocode_visible_and_do_not_pick_nearest(monkeypatch, backend, failure):
    module = core.load_legacy_planner() if backend else runtime
    def fetch(endpoint, params, limiter):
        if endpoint == "/v3/geocode/geo":
            return {"geocodes": [{"formatted_address": RESIDENCE, "level": "\u4f4f\u5b85\u533a",
                                   "location": "121.435,31.205", "adcode": "310105"}]}
        if failure == "exception":
            raise RuntimeError("Provider unavailable")
        candidates = []
        if failure == "multiple":
            candidates = [poi(RESIDENCE + "(" + gate + ")", location=location, type="\u51fa\u5165\u53e3")
                          for gate, location in [(EAST_GATE, "121.431,31.201"), (WEST_GATE, "121.432,31.202")]]
        if failure in {"invalid_entrance", "centroid_only"}:
            candidates = [poi(RESIDENCE, type="\u4f4f\u5b85\u533a", entr_location="nan,31.2" if failure == "invalid_entrance" else "")]
        return {"pois": candidates}
    monkeypatch.setattr(module, "amap_request_json", fetch)
    point = module.amap_geocode_query("China", "Shanghai", RESIDENCE)
    assert (point["lat"], point["lng"]) == (31.205, 121.435)
    assert point["pickup_precision_status"] == "needs_review"
    assert quality.reusable_amap_geocode(point, RESIDENCE)
    again = quality.annotate_amap_pickup(point, RESIDENCE)
    assert again["pickup_precision_issues"] == point["pickup_precision_issues"]


def test_residential_geocode_discovered_from_street_number_checks_entrance(monkeypatch):
    address = ROAD_A + "123\u53f7"
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        if endpoint == "/v3/geocode/geo":
            return {"geocodes": [{"formatted_address": address, "level": "\u4f4f\u5b85\u533a",
                                   "location": "121.435,31.205", "adcode": "310105"}]}
        return {"pois": [poi(RESIDENCE + "(" + gate + ")", address=address, type="\u51fa\u5165\u53e3")
                          for gate in (EAST_GATE, WEST_GATE)]}
    monkeypatch.setattr(runtime, "amap_request_json", fetch)
    point = runtime.amap_geocode_query("China", "Shanghai", address)
    assert calls == ["/v3/geocode/geo", "/v3/place/text"]
    assert (point["lat"], point["lng"]) == (31.205, 121.435)
    assert point["pickup_precision_status"] == "needs_review"


@pytest.mark.parametrize("address", ["\u82b1\u56ed\u8def123\u53f7", "\u897f\u95e8\u8def123\u53f7", "\u516c\u5bd3\u8def123\u53f7"])
def test_road_names_do_not_become_residential_or_gate_requests(address):
    assert not quality._gate_tokens(address)
    assert not quality._residential_pickup(address, {})


def test_old_residential_cache_warns_without_relocation_or_provider_calls(monkeypatch):
    key = runtime.geocode_cache_key("China", "Shanghai", RESIDENCE)
    old = {"provider": "amap", "lat": 31.2, "lng": 121.43, "adcode": "310105",
           "formatted_address": "\u4e0a\u6d77\u5e02" + RESIDENCE, "geocode_level": "poi",
           "amap_poi_name": RESIDENCE, "amap_poi_type": "\u4f4f\u5b85\u533a"}
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {key: old})
    monkeypatch.setattr(runtime, "run_geocode_provider", lambda *_: pytest.fail("Do not silently refresh live caches"))
    point, warning, _ = runtime.resolve_geocoded_point("China", "Shanghai", RESIDENCE)
    assert warning is None and quality.reusable_amap_geocode(point, RESIDENCE)
    assert (point["lat"], point["lng"]) == (old["lat"], old["lng"])
    assert "residential_entrance_unconfirmed" in point["pickup_precision_issues"]


def test_operator_confirmation_survives_shared_annotation_for_old_landmark():
    point = {"provider": "amap", "lat": 31.2, "lng": 121.43, "pickup_precision_status": "operator_confirmed",
             "pickup_override_revision": 1, "pickup_precision_issues": [], "amap_poi_name": "confirmed pickup"}
    assert quality.annotate_amap_pickup(point, RESIDENCE) == point


def test_fleet_payload_keeps_separate_entrance_provenance():
    demand = importlib.import_module("demand_routing")
    point = {"provider": "amap", "lat": 31.2, "lng": 121.43, "address": RESIDENCE,
             "pickup_entrance_source": "provider_entr_location", "amap_poi_location": "121.44,31.21",
             "amap_poi_entr_location": "121.43,31.2"}
    payload = demand._point_payload(point)
    for key in ("pickup_entrance_source", "amap_poi_location", "amap_poi_entr_location"):
        assert payload[key] == point[key]


def test_unknown_city_does_not_discard_resolved_residential_geocode():
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        assert endpoint == "/v3/geocode/geo"
        return {"geocodes": [{"formatted_address": RESIDENCE, "level": "\u4f4f\u5b85\u533a",
                               "location": "121.435,31.205"}]}
    point = quality.resolve_amap_pickup(request_json=fetch, country="China", city="Other", address=RESIDENCE,
                                        city_code="", geocode_limiter=None, poi_limiter=None,
                                        plausible=lambda *args, **kwargs: True, to_wgs84=lambda lat, lng: (lat, lng))
    assert calls == ["/v3/geocode/geo"]
    assert (point["lat"], point["lng"]) == (31.205, 121.435)
    assert point["pickup_precision_status"] == "needs_review"


def test_building_number_cannot_match_a_larger_number_at_same_gate():
    address = ROAD_A + "123\u53f7(" + EAST_GATE + ")"
    candidate = poi(RESIDENCE + "(" + EAST_GATE + ")", address=ROAD_A + "1123\u53f7", type="\u51fa\u5165\u53e3")
    assert "requested_building_number_not_preserved" in quality.amap_candidate_issues(address, candidate, poi=True)


def test_same_compound_different_phase_cannot_supply_gate():
    address = RESIDENCE + "(2\u671f)(8\u53f7\u95e8)"
    candidate = poi(RESIDENCE + "(1\u671f)(8\u53f7\u95e8)", type="\u51fa\u5165\u53e3")
    assert "requested_branch_not_preserved" in quality.amap_candidate_issues(address, candidate, poi=True)


def test_named_residential_section_is_not_stripped_as_an_administrative_district():
    residence = "\u661f\u5149\u897f\u533a"
    assert quality._local_address(residence) == residence
    requested = residence + "(" + EAST_GATE + ")"
    wrong = poi(RESIDENCE + "(" + EAST_GATE + ")", type="\u51fa\u5165\u53e3")
    assert "requested_landmark_not_preserved" in quality.amap_candidate_issues(requested, wrong, poi=True)
