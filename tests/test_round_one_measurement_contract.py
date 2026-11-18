"""Offline regressions at the actual provider and result boundaries."""
from copy import deepcopy
import base64
from io import BytesIO
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
for folder in ("apps", "apps/client", "apps/backend"):
    sys.path.insert(0, str(ROOT / folder))
import backend_job_runner as worker
import client_runtime as runtime
import demand_routing as fleet
import distance_tool as tool
import planner_core


@pytest.mark.parametrize("variant", ["legacy", "manual", "wgs"])
def test_osrm_actual_request_coordinates(monkeypatch, variant):
    raw = {"lat": 31.2, "lng": 121.4, "provider": "amap", "country": "China", "city": "Shanghai"}
    lat, lng = runtime.gcj02_to_wgs84(raw["lat"], raw["lng"])
    if variant == "manual":
        raw.update(coordinate_system="GCJ02", plot_lat=lat, plot_lng=lng)
    if variant == "wgs":
        raw.update(coordinate_system="WGS84", lat=lat, lng=lng)
    saved = deepcopy(raw)
    urls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"code":"Ok","durations":[[0]],"distances":[[0]]}
    def capture(url, **kwargs):
        urls.append(url)
        return Response()
    monkeypatch.setattr(tool.requests, "get", capture)
    monkeypatch.setattr(tool, "maybe_ensure_osrm_base_url", lambda _: None)
    tool.compute_osrm_metrics_from_origin(raw, [raw])
    tool.compute_osrm_route_leg_metrics([raw, raw])
    fleet._build_osrm_matrix([raw, raw])
    assert len(urls) == 4 and all(url.endswith(f"/{lng},{lat};{lng},{lat}") for url in urls)
    assert raw == saved


def costs(points, monkeypatch, metric, bus="diesel"):
    calls = []
    def measure(ps):
        calls.extend((a["id"], b["id"]) for a,b in zip(ps, ps[1:]))
        return [metric for _ in ps[1:]]
    monkeypatch.setattr(tool, "compute_osrm_route_leg_metrics", measure)
    rows = [dict(route_id="R1", source_excel_row=i, stop_sequence=i, address=str(i), bus_type=bus,
                 status="ok" if valid else "geocode_failed", point={"id":i} if valid else None)
            for i, valid in enumerate(points, 1)]
    route, legs = tool.build_current_plan_route_cost_dataframe(rows, rows, diesel_price_per_liter=8,
                                                              fuel_efficiency_km_per_liter=2)
    return route, legs, calls


def test_missing_stop_not_bridged_or_exported_as_zero(monkeypatch):
    route, legs, calls = costs([1,0,1,1], monkeypatch, {"distance_m":1500,"duration_s":60})
    assert calls == [(3,4)]
    row = route.iloc[0]
    assert pd.isna(row.route_distance_km) and pd.isna(row.estimated_one_way_fuel_cost)
    assert row.measured_distance_km == 1.5 and row.measured_legs == 1 and row.expected_legs == 3
    assert list(legs.measurement_status) == ["unresolved_stop","unresolved_stop","measured"]
    exported = pd.read_excel(BytesIO(tool.build_download_excel_bytes(route)))
    assert pd.isna(exported.iloc[0].route_distance_km) and exported.iloc[0].measured_distance_km == 1.5


@pytest.mark.parametrize("metric,complete", [({"distance_m":None,"duration_s":None},False),
    ({"distance_m":0,"duration_s":0},True), ({"distance_m":float("nan"),"duration_s":3},False)])
def test_null_leg_not_zero_but_verified_zero_allowed(monkeypatch, metric, complete):
    route, legs, _ = costs([1,1], monkeypatch, metric)
    assert (not pd.isna(route.iloc[0].route_distance_km)) == complete
    if complete:
        assert route.iloc[0].route_distance_km == route.iloc[0].estimated_one_way_fuel_cost == 0
    assert (legs.iloc[0].measurement_status == "measured") == complete


def test_electric(monkeypatch):
    route, _, _ = costs([1,1], monkeypatch, {"distance_m":2000,"duration_s":60}, "electric bus")
    row = route.iloc[0]
    assert row.route_distance_km == 2 and pd.isna(row.estimated_one_way_fuel_cost)
    assert row.diesel_cost_status == "skipped_electric_bus"


def test_workbook_to_cost_response_preserves_unknown_totals(monkeypatch):
    import backend_service as service
    book = BytesIO()
    pd.DataFrame({"route":["R1"]*4, "stop":list("ABCD")}).to_excel(book, index=False)
    monkeypatch.setattr(tool, "geocode_records_for_distance_tool", lambda rows: ([
        {**r, "status":"ok" if r["address"] != "B" else "geocode_failed", "point":{"id":r["address"]}}
        for r in rows], False))
    monkeypatch.setattr(tool, "compute_osrm_route_leg_metrics", lambda ps: [{"distance_m":1000, "duration_s":60}])
    saved = []
    monkeypatch.setattr(service, "_save_distance_checker_job", saved.append)
    result = service._handle_current_plan_route_cost({"file_base64":base64.b64encode(book.getvalue()).decode(),
        "route_column":"route","address_column":"stop","fuel_efficiency_km_per_liter":2,"diesel_price_per_liter":8})
    summary = result["summary"]
    assert summary["total_one_way_distance_km"] is summary["estimated_one_way_fuel_cost"] is None
    assert summary["routes_with_incomplete_measurement"] == 1 and summary["measured_leg_count"] == 1
    assert result["route_results"] == saved[0]["route_results"]
    assert [r["distance_km"] for r in result["leg_results"]] == [None,None,1]


@pytest.mark.parametrize("exc,kind", [(TimeoutError(), "provider_unavailable"),
    (runtime.GeocodeAccessError("provider_quota","budget"), "provider_quota"),
    (runtime.GeocodeAccessError("provider_configuration","configuration"), "provider_configuration")])
def test_access_errors_not_cached_as_no_match(exc, kind):
    assert runtime._geocode_failure_kind(exc) == kind
    assert 0 < runtime._NEGATIVE_CACHE_TTL[kind] < runtime._NEGATIVE_CACHE_TTL["no_match"]


def test_legacy_negative_is_retried_on_access_only(monkeypatch):
    from amap_geocode_quality import GeocodePrecisionError
    addr = "Route QA synthetic"
    key = runtime.geocode_cache_key("China","Shanghai",addr)
    old = {"cache_status":"failed", "attempted_providers":["amap"], "geocode_quality_version":runtime.GEOCODE_QUALITY_VERSION}
    cache = {key:dict(old), "unrelated":dict(old)}
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", cache)
    monkeypatch.setattr(runtime, "geocode_provider_order", lambda *_:["amap"])
    calls = []
    def no_match(*args):
        calls.append(1)
        raise GeocodePrecisionError("No match")
    monkeypatch.setattr(runtime, "run_geocode_provider", no_match)
    for _ in range(2):
        assert runtime.resolve_geocoded_point("China","Shanghai",addr)[0] is None
    assert calls == [1] and cache[key]["failure_kind"] == "no_match" and cache["unrelated"] == old


def test_transient_geocode_recovers(monkeypatch):
    clock = [10000.]
    calls = []
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    monkeypatch.setattr(runtime, "GEOCODE_CACHE", {})
    monkeypatch.setattr(runtime, "geocode_provider_order", lambda *_:["amap"])
    def request(endpoint, *args):
        calls.append(endpoint)
        if clock[0] == 10000.: raise TimeoutError("temporary transport error")
        return {"geocodes":[{"location":"121.43,31.2","level":"门牌号","formatted_address":"上海市测试路123号","adcode":"310104"}]}
    monkeypatch.setattr(runtime, "amap_request_json", request)
    args = ("China","Shanghai","测试路123号")
    assert runtime.resolve_geocoded_point(*args)[0] is None
    count = len(calls)
    entry = next(iter(runtime.GEOCODE_CACHE.values()))
    assert entry["failure_kind"] == "provider_unavailable"
    assert runtime.resolve_geocoded_point(*args)[0] is None and len(calls) == count
    clock[0] = entry["retry_after"] + 1
    assert runtime.resolve_geocoded_point(*args)[0] is not None


@pytest.mark.parametrize("country,expected", [("CN","amap"),("South Korea","kakao_navi"),("Thailand","none")])
def test_task_country_not_returned_evidence_selects_policy(country, expected):
    result = {"structured_results":{"current_plan":{"routes":[{"route_id":"R1"}]}}}
    provider = planner_core.scheduled_traffic_provider([{"country":country}])
    assert provider == expected
    assert bool(worker._scheduled_final_traffic_validation_error(result, expected_provider=provider)) == (expected != "none")
    if expected != "none":
        wrong = "amap" if expected != "amap" else "kakao_navi"
        result["scheduled_run_traffic_refresh"] = {"provider":wrong,"status":"ready","api_calls":1}
        result["structured_results"]["current_plan"]["traffic_gate"] = {"provider":wrong,"status":"passed","checked_route_count":1}
        assert worker._scheduled_final_traffic_validation_error(result, expected_provider=provider)
