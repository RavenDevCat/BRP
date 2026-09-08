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


def test_ambiguous_bus_stop_does_not_select_first_or_shortest():
    with pytest.raises(quality.GeocodePrecisionError, match="Multiple"):
        quality.select_amap_pickup_candidate(REQUEST, [poi(), poi(location="121.431,31.200")], poi=True)


def test_distinct_poi_ids_at_same_location_are_still_ambiguous():
    with pytest.raises(quality.GeocodePrecisionError, match="Multiple"):
        quality.select_amap_pickup_candidate(REQUEST, [poi(id="one"), poi(id="two")], poi=True)


def test_old_prepared_points_require_review_before_any_provider_request(monkeypatch):
    analysis = importlib.import_module("direct_school_analysis")
    monkeypatch.setattr(core, "load_legacy_planner", lambda: type("Planner", (), {"AMAP_KEY": "test"})())
    monkeypatch.setattr(core, "_amap_route_stats", lambda *_: pytest.fail("must not request routes for unverified pickups"))
    points = [{"provider": "amap", "address": REQUEST, "lat": 31.2, "lng": 121.43},
              {"provider": "amap", "address": "school", "lat": 31.21, "lng": 121.44}]
    snapshot = [dict(point) for point in points]
    provider = analysis.FreshRouteProvider("amap", departure_time=None, api_call_limit=10)
    with pytest.raises(quality.GeocodePrecisionError, match="Re-prepare"):
        provider.route(points)
    assert provider.state["api_calls"] == 0
    assert provider.state["last_route_evidence"]["status"] == "needs_review"
    state = {}
    with pytest.raises(quality.GeocodePrecisionError):
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
    with pytest.raises(quality.GeocodePrecisionError):
        core._route_amap_points([point, point], {"nodes": [0, 1]}, state)
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
def test_both_clients_share_precision_filter_and_city_bounded_poi(monkeypatch, backend):
    module = core.load_legacy_planner() if backend else runtime
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        assert params["city"] == "310000"
        if endpoint == "/v3/geocode/geo":
            return {"geocodes": [{"formatted_address": "\u4e0a\u6d77\u5e02" + ROAD_A,
                                   "level": "\u9053\u8def", "location": "121.43,31.20", "adcode": "310105"}]}
        assert params["citylimit"] == "true"
        return {"pois": [poi("\u67ab\u6811\u8def" + ROAD_B + BUS), poi()]}
    monkeypatch.setattr(module, "amap_request_json", fetch)
    point = module.amap_geocode_query("China", "Shanghai", REQUEST)
    assert calls == ["/v3/geocode/geo", "/v3/place/text"]
    assert point["amap_poi_name"] == REQUEST
    assert point["geocode_quality_version"] == quality.GEOCODE_QUALITY_VERSION
    assert quality.reusable_amap_geocode(point, REQUEST)
    point["amap_poi_name"] = "wrong stop"
    point["formatted_address"] = "wrong stop"
    assert not quality.reusable_amap_geocode(point, REQUEST)


def test_old_amap_cache_without_precision_cannot_bypass_new_check(monkeypatch):
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
    assert warning is None and changed and len(calls) == 1
    assert runtime.GEOCODE_CACHE["unrelated"] == {"keep": True}
    assert point["geocode_quality_version"] == quality.GEOCODE_QUALITY_VERSION
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
