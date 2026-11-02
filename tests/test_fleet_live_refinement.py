from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "apps", ROOT / "apps/client", ROOT / "apps/backend"):
    sys.path.insert(0, str(folder))
import demand_global_optimizer as optimizer
from fleet_refinement import refine_measured_plan


CATALOG = [{"vehicle_type": "test", "display_name": "Van", "listed_seats": 14,
            "category": "van", "propulsion": "diesel", "available_count": 20, "enabled": True}]


def test_available_fleet_is_not_truncated_to_capacity_plus_two():
    assumptions = optimizer.get_planning_assumptions("CN", mode="comfort_saver", monitor_seats=1)
    pool = optimizer._candidate_vehicle_pool(40, assumptions, CATALOG)
    assert len(pool) == 20
    assert all(vehicle["capacity"] == 13 for vehicle in pool)


@pytest.mark.parametrize("direction", ["to_school", "from_school"])
@pytest.mark.parametrize("exception", [False, True])
def test_real_solver_includes_dwell_and_isolates_direct_exceptions(monkeypatch, direction, exception):
    school = {"status": "ok", "address": "School", "lat": 31.2, "lng": 121.4}
    points = [{**school, "address": str(i), "student_count": 1} for i in range(2)]
    matrix = [[0, 2400 if exception else 1800, 1800], [2400 if exception else 1800, 0, 0], [1800, 0, 0]]
    monkeypatch.setattr(optimizer, "_build_osrm_matrix", lambda _: (matrix, matrix))
    monkeypatch.setattr(optimizer, "_route_leg_details_for_order", lambda *_: [])
    builder = optimizer.build_guided_local_search_parameters
    monkeypatch.setattr(optimizer, "build_guided_local_search_parameters", lambda *a, **kw: builder(*a, **{**kw, "time_limit_seconds": 1}))
    result = optimizer.build_global_ortools_plan({"school": school, "demand_points": points},
        market="CN", mode="comfort_saver", monitor_seats=1, custom_catalog=CATALOG,
        service_direction=direction, max_route_duration_minutes=31)
    assert len(result["routes"]) == 2
    assert sorted(node for route in result["routes"] for node in route["order"] if node) == [1, 2]
    assert all(len(route["order"]) == 2 for route in result["routes"])
    assert sum(route["duration_s"] + route["stop_service_time_s"] > 31 * 60 for route in result["routes"]) == int(exception)


def plan(direction="to_school"):
    school = {"student_count": 0, "address": "School"}
    stops = [{"student_count": 1, "address": str(n)} for n in range(1, 5)]
    return {"summary": {"service_direction": direction}, "routes": [{
        "cluster_id": "G01", "service_direction": direction,
        "ordered_points": stops + [school] if direction == "to_school" else [school] + stops,
        "order": [1, 2, 3, 4, 0] if direction == "to_school" else [0, 1, 2, 3, 4],
        "selected_vehicle": {"capacity": 13}, "warnings": [],
        "route_evidence": {"status": "verified"},
        "final_route_traffic_gate": {"status": "failed", "verified_total_duration_s": 9000},
    }]}


def pool(count):
    return [{"capacity": 13, "display_name": "Van", "vehicle_index": i} for i in range(count)]


@pytest.mark.parametrize("direction", ["to_school", "from_school"])
def test_live_split_conserves_stops_and_keeps_direct_exception(direction):
    original = plan(direction)
    before = deepcopy(original)
    def measure(routes):
        for route in routes:
            route["route_evidence"] = {"status": "verified"}
            failed = 1 in route["order"] or len(route["order"]) > 3
            route["final_route_traffic_gate"] = {"status": "failed" if failed else "passed", "verified_total_duration_s": 5000 if failed else 2000}
    result = refine_measured_plan(original, pool(4), measure)
    assert original == before
    assert sorted(n for route in result["routes"] for n in route["order"] if n) == [1, 2, 3, 4]
    assert len(result["refinement"]["direct_exception_routes"]) == 1
    assert len(result["refinement"]["unresolved_routes"]) == 1
    assert len(result["routes"]) == 3
    assert len({r["selected_vehicle"]["vehicle_index"] for r in result["routes"]}) == 3


def test_live_refinement_obeys_fleet_and_budget_without_calls():
    def forbidden(_):
        raise AssertionError("No API call should occur")
    for vehicles, budget in [(pool(1), True), (pool(10), False)]:
        result = refine_measured_plan(plan(), vehicles, forbidden, budget_available=lambda: budget)
        assert len(result["routes"]) == 1
        assert result["refinement"]["split_count"] == 0


def test_failed_child_measurement_retains_measured_parent():
    result = refine_measured_plan(plan(), pool(4), lambda _: None)
    assert len(result["routes"]) == 1
    assert result["routes"][0]["route_evidence"]["status"] == "verified"
    assert result["refinement"]["attempts"][0]["accepted"] is False


def test_backend_refinement_shares_provider_budget_and_keeps_all_riders(monkeypatch):
    import backend_service as backend
    import demand_routing
    import final_timing
    original = plan()
    original['school'] = {'country': 'China'}
    original['summary']['max_route_duration_minutes'] = 80
    for route in original['routes']:
        route['ordered_points'] = [{**p, 'lat': 31.2, 'lng': 121.4} for p in route['ordered_points']]
    monkeypatch.setattr(final_timing, 'prepare', lambda _: {})
    monkeypatch.setattr(final_timing, 'is_google', lambda _: False)
    monkeypatch.setattr(backend, '_fleet_traffic_context', lambda *a, **kw: {'traffic_profile_name': 'AM Peak', 'traffic_profile_context': ''})
    monkeypatch.setattr(optimizer, 'build_global_ortools_plan', lambda *a, **kw: deepcopy(original))
    monkeypatch.setattr(backend, '_client_module', lambda name: optimizer if name == 'demand_global_optimizer' else demand_routing)
    monkeypatch.setattr(backend, '_route_plan_response', lambda result, **kw: result)
    instances = []
    class Provider:
        def __init__(self, *a, api_call_limit, **kw):
            self.state = {'api_calls': 0, 'api_call_limit': api_call_limit}
            instances.append(self)
        def route(self, points, **kw):
            self.state['api_calls'] += 1
            seconds = 6000 if len(points) > 3 else 1200
            legs = [{'duration_s': seconds / (len(points)-1), 'distance_m': 1000} for _ in points[1:]]
            return {'provider': 'amap', 'status': 'verified', 'complete': True,
                    'duration_s': seconds, 'distance_m': 1000*(len(points)-1), 'legs': legs}
    monkeypatch.setattr(backend, 'FreshRouteProvider', Provider)
    result = backend._handle_fleet_planner_global_plan({'market': 'CN', 'mode': 'comfort_saver',
        'monitor_seats': 1, 'vehicle_catalog': CATALOG, 'max_route_duration_minutes': 80,
        'geocode_result': {'school': original['school'], 'demand_points': [{'status': 'ok', 'student_count': 4}]}})
    assert len(instances) == 1
    assert result['summary']['provider_api_calls'] == 3
    assert len(result['routes']) == 2
    assert all(r['final_route_traffic_gate']['status'] == 'passed' for r in result['routes'])
    assert sum(p['student_count'] for r in result['routes'] for p in r['ordered_points'][:-1]) == 4
