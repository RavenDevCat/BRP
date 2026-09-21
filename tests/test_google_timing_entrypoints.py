from copy import deepcopy
from datetime import timedelta
import json

import pandas as pd
import pytest

from test_google_final_validation import client, NOW, POINTS, response, body_points
import final_timing as timing
import google_final_validation as google
import backend_service as backend
import api_app as api
import direct_school_analysis as direct

CONFIG = {"final_time_validation_mode": "google", "validation_service_date": "2030-01-02",
          "validation_budget_id": "test-task", "time_window_start": "06:30", "time_window_end": "08:00",
          "service_direction": "To School", "stop_service_minutes": 1}

@pytest.fixture
def context(client, monkeypatch):
    session = google.ValidationSession(client)
    monkeypatch.setattr(google, "session_for", lambda config: session)
    return timing.FinalTimingContext(CONFIG, session=session)

def points():
    return [{"id": str(i), "lat": lat, "lng": lng, "country": "China", "city": "Shanghai",
             "coordinate_system": "WGS84", "address": str(i), "is_depot": i == 2,
             "student_count": 2 if i == 0 else 0, "order": i} for i, (lat, lng) in enumerate(POINTS)]

def test_context_keeps_native_evidence_and_dwell(context):
    value = context.route(points(), dwell_s=[60, 60, 0])
    assert value["provider"] == "google_routes" and value["duration_s"] == 1200
    assert value["stop_service_time_s"] == 120 and value["time_window_passes"]
    assert value["leg_durations_s"] == [600, 600]
    assert value["validation_config"] == CONFIG
    assert value["geometry"] == [] and len(value["geometry_segments"]) == 2
    assert context.state["api_calls"] == 4
    assert (context.latest-google.datetime.fromisoformat(value["arrival_time"])).total_seconds() == 120

def test_declared_gcj_is_converted_once(context):
    from amap_driving import wgs84_to_gcj02
    values = points()
    for point in values:
        point["lat"], point["lng"] = wgs84_to_gcj02(point["lat"], point["lng"])
        point["coordinate_system"] = "GCJ02"
    measured = context.route(values)
    assert google.meters(measured["legs"][0]["start"], POINTS[0]) < 3

def test_unknown_coordinates_rejected_before_call(context):
    values = points()
    values[0].pop("coordinate_system")
    with pytest.raises(google.ValidationUnavailable, match="wgs84"):
        context.route(values)
    assert context.state["api_calls"] == 0

@pytest.mark.parametrize("direction", ["To School", "From School"])
def test_fleet_native_timing_reaches_map_and_schedule(context, monkeypatch, direction):
    fleet = backend._client_module("demand_routing")
    data = points()
    if direction == "From School": data.reverse()
    context.config["service_direction"] = direction
    route = {"cluster_id": "R1", "ordered_points": data, "duration_s": 60, "distance_m": 100,
             "selected_vehicle": {"student_capacity": 15}, "leg_details": []}
    plan = {"school": {"country": "China"}, "summary": {"service_direction": direction.lower().replace(" ", "_"),
            "max_route_duration_minutes": 60}, "routes": [route], "route_rows": [{"cluster_id": "R1"}]}
    monkeypatch.setattr(backend, "FreshRouteProvider", lambda *a, **k: pytest.fail("legacy provider reached"))
    backend._attach_fleet_route_measurements(plan, fleet, measurement_provider=context)
    view = fleet.fleet_route_display_view(plan)
    measured = view["routes"][0]
    assert measured["final_route_traffic_gate"]["provider"] == "google_routes"
    assert measured["display_metrics"]["duration_s"] == 1320
    assert measured["ordered_points"][0]["scheduled_time_minutes"] == measured["route_evidence"]["departure_minutes"]
    calls = context.state["api_calls"]
    assert fleet.build_route_preview_map_data(view)["routes"][0]["duration_s"] == 1320
    fleet.build_generated_plan_workbook_bytes(view)
    assert calls == context.state["api_calls"]

@pytest.mark.parametrize("kind", ["walk_to_stop", "insert_stop"])
def test_insert_submission_preserves_source_direction_and_off_settings(context, monkeypatch, kind):
    from test_route_insert_measurements import source
    base = {"service_direction": "From School", "time_window_start": "15:40", "time_window_end": "17:40"}
    enabled = kind == "insert_stop"
    requested = {**CONFIG, "final_time_validation_mode": "google" if enabled else "legacy"}
    monkeypatch.setattr(timing, "prepare", lambda config: {**config, "validation_budget_id": "new-budget"})
    monkeypatch.setattr(api, "_insert_geocode_stops", lambda *a, **k: ([], []))
    monkeypatch.setattr(api, "_refine_insert_proposals_with_osrm", lambda *a, **k: None)
    monkeypatch.setattr(api, "_insert_scenario_selections", lambda *a: [[]])
    class Captured(Exception): pass
    def capture(*args, **kwargs):
        actual = kwargs["suggested_config"]
        if enabled:
            assert actual["service_direction"] == "From School"
            assert actual["validation_budget_id"] == "new-budget"
            assert actual["time_window_start"] == CONFIG["time_window_start"]
        else:
            assert actual == base
        raise Captured()
    monkeypatch.setattr(api, "_insert_build_selected_plan", capture)
    with pytest.raises(Captured):
        api._build_route_insert_proposals({"config": {"service_direction": "To School"}}, {
            "_map_data": source(), "_suggested_config": base, "timing_config": requested, "new_stops": []})

@pytest.mark.parametrize("kind", ["walk_to_stop", "insert_stop"])
def test_insert_baseline_and_candidate_share_google_context(context, monkeypatch, kind):
    from test_route_insert_measurements import source, action
    monkeypatch.setattr(api, "_insert_route_measurement", lambda *a, **k: pytest.fail("legacy final call reached"))
    result, map_data = api._insert_build_selected_plan(source(), [action(kind)], country="China", constraints={},
                              suggested_config=CONFIG, measurement_cache={})
    row = result["affected_routes"][0]
    assert row["base_route_evidence"]["provider"] == row["route_evidence"]["provider"] == "google_routes"
    assert row["base_route_evidence"]["stop_service_time_s"] == 0
    assert row["route_evidence"]["stop_service_time_s"] == (60 if kind == "insert_stop" else 0)
    assert row["time_window_ok"] and result["feasible"]
    assert map_data["routes"][0]["duration_s"] == row["selected_duration_s"]
    assert map_data["routes"][0]["final_route_traffic_gate"]["provider"] == "google_routes"
    assert map_data["stops"][0]["scheduled_time_minutes"] == row["route_evidence"]["departure_minutes"]

@pytest.mark.parametrize("policy", ["arrival_anchored", "fixed_departure"])
def test_direct_school_full_classification_and_export_use_google(context, monkeypatch, policy):
    from test_direct_school_analysis import prepared_payload, fake_osrm
    monkeypatch.setattr(direct, "_osrm_leg", fake_osrm)
    monkeypatch.setattr(direct, "FreshRouteProvider", lambda *a, **k: pytest.fail("legacy provider reached"))
    result = direct.run_direct_school_analysis(prepared_payload(), {**CONFIG, "timing_policy": policy, "far_duration_minutes": 60})
    if policy == "fixed_departure":
        assert all(row["route_evidence"]["departure_minutes"] == 390 for row in result["stops"])
        assert result["routes"][0]["route_evidence"]["departure_minutes"] == 390
    assert result["provider"] == "google_routes" and result["status"] == "complete"
    assert result["summary"]["failed_count"] == 0
    assert result["routes"][0]["route_evidence"]["stop_service_time_s"] == 120
    assert all(row["provider"] == "google_routes" for row in result["stops"])
    assert all(row["latest_direct_departure"] == direct._clock_label(row["route_evidence"]["departure_minutes"])
               for row in result["stops"])
    assert result["route_window_analysis"][0]["final_duration_min"] == 22
    calls = context.session.client.calls
    direct.build_direct_school_workbook({"result": result})
    assert calls == context.session.client.calls

def test_direct_school_failure_is_task_level(context, monkeypatch):
    from test_direct_school_analysis import prepared_payload, fake_osrm
    monkeypatch.setattr(direct, "_osrm_leg", fake_osrm)
    context.session.client.transport = lambda body: {"routes": []}
    with pytest.raises(google.ValidationUnavailable, match="google_response_invalid"):
        direct.run_direct_school_analysis(prepared_payload(), CONFIG)

def test_removal_remeasures_native_route_with_zero_rider_waypoint(context, monkeypatch):
    rows = points()
    rows[1]["passenger_count"] = 0
    runtime = {"points": rows, "ordered": rows}
    before = direct._measure_active_route(context, runtime, [0, 1, 2], 1)
    after = direct._measure_active_route(context, runtime, [1, 2], 1)
    assert before["total_duration_min"] == 22 and after["total_duration_min"] == 11
    assert after["route_evidence"]["legs"][0]["start"] == POINTS[1]

def test_distance_time_changes_without_changing_cost_basis(context, monkeypatch):
    tool = backend._distance_tool_module()
    row = {"route_id": "R1", "stop_sequence": 0, "status": "ok", "point": points()[0]}
    with pytest.raises(ValueError, match="at least two"):
        tool.build_current_plan_route_cost_dataframe([row], [row], diesel_price_per_liter=10,
            fuel_efficiency_km_per_liter=2, timing_context=context)
    assert context.state["api_calls"] == 0
    _check_distance_time_cost(context, monkeypatch)

def _check_distance_time_cost(context, monkeypatch):
    tool = backend._distance_tool_module()
    rows = [{"source_excel_row": i+1, "route_id": "R1", "stop_sequence": i, "status": "ok", "point": p}
            for i, p in enumerate(points())]
    monkeypatch.setattr(tool, "compute_osrm_route_leg_metrics", lambda ps: [{"duration_s": 10, "distance_m": 2000}]*(len(ps)-1))
    route_df, leg_df = tool.build_current_plan_route_cost_dataframe(rows, rows, diesel_price_per_liter=10,
        fuel_efficiency_km_per_liter=2, timing_context=context)
    row = route_df.iloc[0]
    assert row["route_duration_min"] == 20 and row["estimated_one_way_fuel_cost"] == 20
    assert row["distance_cost_provider"] == "osrm" and row["time_provider"] == "google_routes"
    assert json.loads(row["timing_evidence"])["distance_m"] == 2000
    assert list(leg_df["duration_min"]) == [10, 10]

@pytest.mark.parametrize("handler,payload", [
    (backend._handle_fleet_planner_route_preview, {"timing_config": CONFIG, "market": "CN"}),
    (backend._handle_fleet_planner_global_plan, {"timing_config": CONFIG, "market": "CN"}),
    (backend._handle_reference_distance_check, {"timing_config": CONFIG}),
    (backend._handle_current_plan_route_cost, {"timing_config": CONFIG}),
    (lambda p: backend._handle_direct_school_submit(p, "test@example.test"), {"analysis_config": CONFIG}),
    (lambda p: api._build_route_insert_proposals({}, p), {"timing_config": CONFIG}),
])
def test_closed_deployment_rejects_before_upload_or_routing(monkeypatch, handler, payload):
    monkeypatch.delenv("BRP_GOOGLE_FINAL_ENABLED", raising=False)
    with pytest.raises(google.ValidationUnavailable, match="rollout_disabled"):
        handler(payload)


@pytest.mark.parametrize("tool", ["audit", "direct", "fleet_planner", "route_insert_advisor"])
@pytest.mark.parametrize("prior_calls", [0, 501])
def test_google_full_review_uses_same_provider_and_persistent_review_budget(context, monkeypatch, tmp_path, tool, prior_calls):
    import measurement_reviews as reviews
    from runtime_store_sqlite import SqliteRuntimeStore
    from test_full_measurement_review import osrm
    import test_full_measurement_review as full_tests
    import test_audit_measurement_review as audit_tests
    import test_side_measurement_review as side_tests
    monkeypatch.setattr(direct, "_osrm_leg", osrm)
    fields = {key: CONFIG[key] for key in ("final_time_validation_mode", "validation_service_date", "validation_budget_id")}
    store = SqliteRuntimeStore(tmp_path / "reviews.sqlite")
    if tool == "audit":
        original = audit_tests.source()
        for cfg in [original["config"], original["result"]["planner_config"], original["result"]["structured_results"]["planner_config"]]:
            cfg.update(fields)
        for scenario in original["result"]["structured_results"].values():
            if isinstance(scenario, dict):
                for point in scenario.get("points") or []:
                    point.update(plot_lat=point["lat"], plot_lng=point["lng"])
        row = audit_tests.create(store, original)
    elif tool == "direct":
        original = full_tests.source()
        original["result"]["parameters"].update(fields)
        original["result"]["provider"] = "google_routes"
        for point in original["prepared_payload"]["original_points"]:
            point.update(plot_lat=point["lat"], plot_lng=point["lng"])
        row = full_tests.create(store, original)
    else:
        original = side_tests.fleet_source() if tool == "fleet_planner" else side_tests.insert_source(monkeypatch)
        if tool == "fleet_planner":
            original["global_plan_result"]["summary"]["validation_config"] = CONFIG
        else:
            for scenario in original["route_insert_result"]["scenarios"]:
                for route in scenario["selected_plan"]["affected_routes"]:
                    route["measurement_inputs"]["config"].update(fields)
        row = side_tests.create(store, original, tool)
    saved = deepcopy(store.get_job("source") if tool in {"audit", "direct"} else store.get_side_tool_run(tool, original["run_id"]))
    # Google reviews ignore the legacy per-review cap, including resumed usage.
    with store.connect() as conn:
        conn.execute("UPDATE route_measurement_reviews SET api_calls=?, request_json=json_set(request_json, '$.provider_call_limit', 1) WHERE review_id=?",
                     (prior_calls, row["review_id"]))
    if tool in {"audit", "direct"}:
        with pytest.raises(ValueError, match="full same-provider"):
            reviews.build_review_request(original, ["any"], requested_by="test@example.test", request_key="partial")
    result = reviews.execute_saved_review(store, row["review_id"], "worker",
        provider_factory=lambda *a, **k: pytest.fail("Google review reached legacy provider"))
    assert result["status"] == "succeeded", json.dumps(result.get("result"), ensure_ascii=False)
    assert result["api_calls"] == context.session.client.calls > 0
    assert all(route["route_evidence"]["provider"] == "google_routes" for route in result["result"]["routes"])
    assert (store.get_job("source") if tool in {"audit", "direct"} else store.get_side_tool_run(tool, original["run_id"])) == saved
