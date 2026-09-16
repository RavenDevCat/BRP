"""Run the real OR-Tools pipeline with synthetic matrices and provider responses."""
from copy import deepcopy
import pytest

from test_google_final_validation import client, POINTS, response, body_points
import google_final_validation as google
import planner_core as core


@pytest.mark.parametrize("direction", ["To School", "From School"])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("slow_first_candidate", [False, True])
@pytest.mark.parametrize("timing_policy", ["arrival_anchored", "fixed_departure"])
def test_new_audit_real_solver_lifecycle(client, monkeypatch, tmp_path, direction, scheduled, slow_first_candidate, timing_policy):
    monkeypatch.setattr(google, "require_available", lambda: None)
    phase = {"slow": False, "injected": False}
    original_gate = core.attach_final_route_traffic_gate
    def observe_gate(*args, **kwargs):
        phase["slow"] = slow_first_candidate and str(args[5]).startswith("Strict") and not phase["injected"]
        try:
            return original_gate(*args, **kwargs)
        finally:
            phase["injected"] |= phase["slow"]
            phase["slow"] = False
    monkeypatch.setattr(core, "attach_final_route_traffic_gate", observe_gate)
    client.transport = lambda body: response(body_points(body), duration=3600 if phase["slow"] else 600)
    session = google.ValidationSession(client)
    monkeypatch.setattr(google, "session_for", lambda config: session)
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    legacy = core.load_legacy_planner()
    monkeypatch.setattr(legacy, "SOLVER_TIME_LIMIT_SECONDS", 1)
    def matrix(points):
        n = len(points)
        return ([[0 if a == b else 600 for b in range(n)] for a in range(n)],
                [[0 if a == b else 1000 for b in range(n)] for a in range(n)])
    monkeypatch.setattr(legacy, "build_osrm_full_matrix", matrix)
    monkeypatch.setattr(legacy, "seed_edge_metrics", matrix)
    monkeypatch.setattr(core, "load_legacy_planner", lambda: legacy)
    points = [{"node_id": i, "country": "China", "city": "Shanghai", "address": f"Synthetic {i}",
               "lat": lat, "lng": lng, "plot_lat": lat, "plot_lng": lng,
               "is_depot": i == 0, "passenger_count": 0 if i == 0 else 2,
               "original_members": [f"Synthetic {i}"]} for i,(lat,lng) in enumerate(POINTS)]
    order = [1,2,0] if direction == "To School" else [0,1,2]
    prepared = {"input_records": deepcopy(points), "original_points": points, "currency_code": "CNY",
        "current_plan": {"service_direction": direction,
            "stops": [{**point,"stop_id": f"s{i}"} for i,point in enumerate(points)],
            "assignments": [{"route_id": "R1", "stop_id": f"s{node}", "stop_sequence": index,
                             "bus_type": "Small Bus"} for index,node in enumerate(order)],
            "fleet": [{"bus_type": "Small Bus", "seat_count": 19}]}}
    config = core.PlannerConfig(final_time_validation_mode="google", validation_service_date="2030-01-02",
        validation_budget_id="test-task", service_direction=direction, minimum_vehicle_reduction=0,
        timing_policy=timing_policy,
        time_window_start="06:30" if direction == "To School" else "15:40",
        time_window_end="08:00" if direction == "To School" else "17:10",
        large_bus_max_count=0, mid_bus_max_count=0, small_bus_max_count=1, reserved_express_buses=0)
    result = core.run_backend_planner_with_prepared_data(prepared, config,
                                                       require_fresh_final_traffic=scheduled)
    for key in ("current_plan", "time_constrained", "exception_preserving"):
        scenario = result["structured_results"][key]
        assert scenario["routes"], (key, result["logs"])
        assert scenario["traffic_gate"]["status"] == "passed", (key, scenario, result["logs"])
        for route in scenario["routes"]:
            gate = route["final_route_traffic_gate"]
            assert gate["provider"] == "google_routes" and gate["passes"]
            assert route["route_evidence"]["complete"]
    assert result["planner_config"]["validation_budget_id"] == "test-task"
    assert client.calls > 0
    if slow_first_candidate:
        assert phase["injected"]
        assert result["structured_results"]["time_constrained"]["traffic_replan_attempts"]
    if scheduled:
        assert result["scheduled_run_traffic_refresh"]["status"] == "ready"
