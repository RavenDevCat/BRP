from copy import deepcopy
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps"), str(ROOT / "apps/backend")]
from route_measurement_view import route_display_metrics, scenario_display_summary, measured_route_views, measurement_note
from route_evidence import EVIDENCE_VERSION


CASES = [
    ({"time_s": 9999, "distance_m": 8888, "stop_service_time_s": 60,
      "route_evidence": {"status": "verified", "complete": True, "duration_s": 600, "distance_m": 1500}},
     {"duration_s": 660, "distance_m": 1500, "source": "measurement"}),
    ({"time_s": 9999, "distance_m": 8888, "route_evidence": {"status": "needs_review", "complete": False},
      "final_route_traffic_gate": {"status": "passed", "verified_total_duration_s": 400}},
     {"duration_s": None, "distance_m": None, "source": "unavailable"}),
    ({"stop_service_time_s": 0, "time_s": 999, "distance_m": 100,
      "route_evidence": {"status": "verified", "complete": True, "duration_s": 0, "distance_m": 0}},
     {"duration_s": 0, "distance_m": 0, "source": "measurement"}),
    ({"route_evidence": {"status": "verified", "complete": True, "duration_s": 600, "distance_m": 1500}},
     {"duration_s": None, "distance_m": 1500, "source": "measurement"}),
    ({"time_s": 999, "distance_m": 888, "final_route_traffic_gate": {"status": "unavailable"}},
     {"duration_s": None, "distance_m": None, "source": "unavailable"}),
    ({"time_s": 999, "distance_m": 888, "final_route_traffic_gate": {"status": "failed", "verified_total_duration_s": 80, "verified_distance_m": 90}},
     {"duration_s": 80, "distance_m": 90, "source": "historical_measurement"}),
    ({"time_s": 0, "duration_s": 999, "distance_m": 0, "total_distance_m": 999},
     {"duration_s": 0, "distance_m": 0, "source": "planning_reference"}),
    ({"time_s": " ", "distance_m": True}, {"duration_s": None, "distance_m": None, "source": "planning_reference"}),
    ({"time_s": [], "distance_m": -1}, {"duration_s": None, "distance_m": None, "source": "planning_reference"}),
    ({"stop_service_time_s": 60, "route_evidence": {"status": "verified", "complete": True, "duration_s": 600, "distance_m": 1500},
      "final_route_traffic_gate": {"status": "passed", "verified_total_duration_s": 9999}},
     {"duration_s": None, "distance_m": None, "source": "unavailable"}),
    ({"stop_service_time_s": 60, "route_evidence": {"status": "verified", "complete": True, "duration_s": 600, "distance_m": 1500, "issues": [{"code": "inconsistent"}]}},
     {"duration_s": None, "distance_m": None, "source": "unavailable"}),
    ({"stop_service_time_s": 60, "route_evidence": {"status": "verified", "complete": True,
      "duration_s": 600, "distance_m": 1500, "issues": [],
      "warnings": [{"code": "provider_distance_disagreement", "leg_index": 0}]}},
     {"duration_s": 660, "distance_m": 1500, "source": "measurement"}),
    ({"route_evidence": {"status": "verified", "complete": True,
      "duration_s": 600, "distance_m": 1500, "issues": [],
      "warnings": [{"code": "provider_distance_disagreement", "leg_index": 0}]}},
     {"duration_s": None, "distance_m": 1500, "source": "measurement"}),
]


@pytest.mark.parametrize("route,expected", CASES)
def test_display_values_preserve_unknown_zero_and_original_references(route, expected):
    original = deepcopy(route)
    assert route_display_metrics(route) == expected
    assert route == original


def test_shared_frontend_backend_display_contract():
    if not shutil.which("node") or not (ROOT / "apps/web/node_modules/typescript").exists():
        pytest.skip("Frontend contract verification requires the installed web TypeScript toolchain.")
    script = """
const fs = require('node:fs');
const ts = require('./apps/web/node_modules/typescript');
const source = fs.readFileSync('./apps/web/src/lib/route-measurements.ts', 'utf8');
const output = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const m = {exports:{}};
new Function('exports', 'require', 'module', output)(m.exports, require, m);
const assert = require('node:assert/strict');
const {measuredTotalDuration, compareAvailableDurations, sumAvailable, maxAvailable} = m.exports;
const measured = seconds => ({final_route_traffic_gate: {status: 'passed', verified_total_duration_s: seconds, verified_distance_m: 100}});
assert.equal(measuredTotalDuration([]), null);
assert.equal(measuredTotalDuration([measured(80), {}]), null);
assert.equal(measuredTotalDuration([{time_s: 0, distance_m: 0}]), null);
assert.equal(measuredTotalDuration([measured(0)]), 0);
assert.equal(measuredTotalDuration([measured(80), measured(40)]), 120);
assert.equal(sumAvailable([1, NaN]), null);
assert.equal(maxAvailable([1, Infinity]), null);
assert.equal(compareAvailableDurations(null, null), 0);
assert.equal(compareAvailableDurations(NaN, 0), 1);
assert.deepEqual([null, 80, 0, 120].sort(compareAvailableDurations), [0, 80, 120, null]);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(route => m.exports.routeDisplayMetrics(route))));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, input=json.dumps([row[0] for row in CASES]),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [row[1] for row in CASES]


@pytest.mark.parametrize("route,expected", CASES)
def test_legacy_views_keep_the_same_contract_and_never_mutate_saved_routes(route, expected):
    saved = {"limit_stop_node": 0, "limit_stop_order": 0, "limit_stop_elapsed_s": 600, **deepcopy(route)}
    original = deepcopy(saved)
    view = measured_route_views([saved])[0]
    assert route_display_metrics(view) == expected
    assert saved == original
    assert measured_route_views([view])[0] == view
    if expected["source"] != "planning_reference" or expected["duration_s"] is None or expected["distance_m"] is None:
        assert not any(key in view for key in ("limit_stop_node", "limit_stop_order", "limit_stop_elapsed_s"))
    else:
        assert view["limit_stop_order"] == 0
    if expected["duration_s"] is None or expected["distance_m"] is None:
        assert "Travel time unavailable" in measurement_note(view)


@pytest.mark.parametrize("module_name", ["client_runtime", "BusingProblem"])
@pytest.mark.parametrize("route,expected", CASES)
def test_legacy_html_totals_follow_the_shared_contract(monkeypatch, module_name, route, expected):
    monkeypatch.syspath_prepend(str(ROOT / "apps/client"))
    module = importlib.import_module(module_name)
    points = [{"address": "Synthetic pickup", "passenger_count": 1}, {"address": "Synthetic school", "is_depot": True}]
    saved = {"vehicle_id": 1, "bus_type_name": "Bus", "load": 1, "bus_capacity": 24,
             "nodes": [0, 1], **deepcopy(route)}
    before = deepcopy(saved)
    markup = module.build_map_summary_html(points, [saved])
    duration = module.seconds_to_human(expected["duration_s"]) if expected["duration_s"] is not None else "Not available"
    distance = f"{expected['distance_m'] / 1000:.1f} km" if expected["distance_m"] is not None else "Not available"
    assert f"<div>Duration: {duration}</div>" in markup
    assert f"<div>Distance: {distance}</div>" in markup
    assert saved == before


def measured_source():
    from test_audit_measurement_review import source
    original = source()
    current = original["result"]["structured_results"]["current_plan"]
    for route in current["routes"]:
        points = [current["points"][node] for node in route["nodes"]]
        legs = [{"duration_s": 120, "distance_m": 600, "geometry": [[a["lng"], a["lat"]], [b["lng"], b["lat"]]]}
                for a, b in zip(points, points[1:])]
        route["route_evidence"] = {"evidence_version": EVIDENCE_VERSION, "status": "verified", "complete": True,
            "duration_s": 120 * len(legs), "distance_m": 600 * len(legs), "legs": legs,
            "geometry": [[point["lng"], point["lat"]] for point in points]}
        route["final_route_traffic_gate"].update(verified_drive_duration_s=120 * len(legs),
            verified_total_duration_s=120 * len(legs) + route["stop_service_time_s"], verified_distance_m=600 * len(legs))
    return original


def test_historical_partial_measurement_does_not_invent_stop_schedules(monkeypatch):
    import backend_service as backend
    monkeypatch.setattr(backend, "_fetch_amap_display_geometry", lambda *a, **k: pytest.fail("No routing on read"))
    monkeypatch.setattr(backend, "_fetch_amap_display_geometry_by_leg", lambda *a, **k: pytest.fail("No routing on read"))
    monkeypatch.setattr(backend, "_load_amap_display_cache_unlocked", lambda: {})
    original = measured_source()
    route = original["result"]["structured_results"]["current_plan"]["routes"][0]
    route.pop("route_evidence")
    route["final_route_traffic_gate"].pop("verified_distance_m")
    payload, error = backend._build_job_map_payload(original, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    displayed = payload["routes"][0]
    assert displayed["duration_s"] == 120 and displayed["distance_m"] is None
    assert displayed["am_arrival_gate"]["status"] == "unavailable"
    for stop in payload["stops"]:
        if stop["route_id"] == displayed["id"]:
            assert stop["scheduled_time_minutes"] is None
            assert stop["cumulative_duration_s"] is None


def test_map_uses_total_measured_duration_not_planning_or_drive_only(monkeypatch):
    import backend_service as backend
    monkeypatch.setattr(backend, "_amap_display_geometry_for_route", lambda *a, **k: pytest.fail("Saved map must not call routing"))
    original = measured_source()
    before = deepcopy(original)
    payload, error = backend._build_job_map_payload(original, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    assert [route["duration_s"] for route in payload["routes"]] == [120, 300]
    assert payload["summary"]["duration_s"] == 300 and payload["summary"]["distance_m"] == 1800
    assert original == before


def test_unknown_map_values_are_not_zero_or_old_planning_measurements(monkeypatch):
    import backend_service as backend
    monkeypatch.setattr(backend, "_amap_display_geometry_for_route", lambda *a, **k: pytest.fail("No routing on read"))
    original = measured_source()
    original["result"]["structured_results"]["current_plan"]["routes"][0]["route_evidence"].update(status="needs_review", complete=False)
    payload, error = backend._build_job_map_payload(original, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    route = payload["routes"][0]
    assert route["duration_s"] is None and route["distance_m"] is None
    assert route["verified_total_duration_s"] is None and route["am_arrival_gate"]["status"] == "unavailable"
    assert route["raw_duration_s"] == 300
    assert payload["summary"]["duration_s"] is None and payload["summary"]["distance_m"] is None
    assert payload["summary"]["measurement_unavailable_route_count"] == 1
    for stop in payload["stops"]:
        if stop["route_id"] == route["id"]:
            assert stop["cumulative_duration_s"] is None and stop["cumulative_distance_m"] is None
            assert stop["scheduled_time_minutes"] is None


def test_read_adapter_keeps_original_assessment_and_adds_measured_display_values():
    import backend_service as backend
    original = measured_source()
    before = deepcopy(original)
    adapted = backend._adapt_legacy_scenario_statuses_for_read(original)
    assessment = adapted["result"]["current_plan_assessment"]
    assert assessment["avg_route_duration_s"] == 9999
    assert assessment["display_summary"]["avg_route_duration_s"] == 210
    assert assessment["display_summary"]["overlong_route_count"] == 0
    assert assessment["route_summaries"][1]["display_metrics"]["duration_s"] == 300
    assert original == before


def test_missing_dwell_does_not_generate_a_verified_schedule():
    import backend_service as backend
    original = measured_source()
    original["result"]["structured_results"]["current_plan"]["routes"][0]["stop_service_time_s"] = None
    payload, error = backend._build_job_map_payload(original, "current_plan", "current_plan", attach_impact=False)
    assert error is None
    first = payload["routes"][0]
    assert first["duration_s"] is None and first["distance_m"] == 600
    assert first["evidence_status"] == "needs_review"
    assert first["am_arrival_gate"]["status"] == "unavailable"
    assert all(stop["scheduled_time_minutes"] is None for stop in payload["stops"] if stop["route_id"] == first["id"])


def test_incomplete_summary_does_not_average_only_successful_routes():
    assert scenario_display_summary([CASES[0][0], CASES[1][0]]) == {
        "avg_route_duration_s": None, "avg_route_distance_m": None, "measurement_unavailable_route_count": 1}
