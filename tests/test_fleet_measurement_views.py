from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/client"))
sys.path.insert(0, str(ROOT / "apps/backend"))
import demand_routing as fleet
import backend_service as backend
from route_measurement_view import route_display_metrics


def source(direction="to_school"):
    points = [dict(address=name, lat=31.2 + index * .001, lng=121.4 + index * .001,
                   student_count=count, country="China", provider="amap", coordinate_system="WGS84",
                   scheduled_time_label="OLD", scheduled_time_minutes=1)
              for index, (name, count) in enumerate((("A", 3), ("Zero waypoint", 0), ("School", 0)))]
    if direction == "from_school":
        points.reverse()
    legs = [{"duration_s": duration, "distance_m": distance,
             "geometry": [[points[i]["lng"], points[i]["lat"]], [points[i+1]["lng"], points[i+1]["lat"]]]}
            for i, (duration, distance) in enumerate(((120, 400), (180, 600)))]
    route = {"cluster_id": "R1", "duration_s": 90, "distance_m": 12345,
             "order": [1, 2, 0], "ordered_points": points,
             "selected_vehicle": {"display_name": "Bus", "student_capacity": 24},
             "leg_details": [{"duration_s": 45, "distance_m": 6000, "geometry": [[30, 120], [30.1, 120.1]]}] * 2,
             "stop_service_time_s": 120,
             "route_evidence": {"status": "verified", "complete": True, "provider": "amap",
                 "duration_s": 300, "distance_m": 1000, "called_at": "2026-01-01T00:00:00Z", "legs": legs},
             "final_route_traffic_gate": {"status": "passed", "verified_drive_duration_s": 300,
                 "verified_total_duration_s": 420, "verified_distance_m": 1000}}
    return {"school": points[-1] if direction == "to_school" else points[0],
            "routes": [route], "summary": {"route_count": 1, "service_direction": direction,
                "max_route_duration_minutes": 10, "total_duration_min": 1.5, "total_distance_km": 12.345},
            "route_rows": [{"cluster_id": "R1", "duration_min": 1.5, "distance_km": 12.345}]}


@pytest.mark.parametrize("direction,expected", [("to_school", ["07:53", "07:56", "08:00"]), ("from_school", ["15:40", "15:42", "15:46"])])
def test_saved_total_dwell_and_schedules_agree_across_map_table_and_workbook(direction, expected):
    original = source(direction)
    before = deepcopy(original)
    view = fleet.fleet_route_display_view(original)
    assert view == fleet.fleet_route_display_view(view)
    assert view["routes"][0]["duration_s"] == 90  # Solver reference is not rewritten.
    assert view["summary"]["total_duration_min"] == 7
    assert view["summary"]["total_distance_km"] == 1
    assert view["route_rows"][0]["duration_min"] == 7
    assert fleet.route_preview_to_dataframe(original).iloc[0]["duration_min"] == 7
    data = fleet.build_route_preview_map_data(original)
    route = data["routes"][0]
    assert route["duration_s"] == 420 and route["distance_m"] == 1000
    assert route_display_metrics(route)["duration_s"] == 420
    assert data["summary"]["duration_s"] == 420
    assert [stop["scheduled_time_label"] for stop in data["stops"]] == expected
    waypoint = next(stop for stop in data["stops"] if stop["requested_address"] == "Zero waypoint")
    assert waypoint["passenger_count"] == 0 and not waypoint["is_depot"]
    assert route["geometry"] == [] and len(route["geometry_segments"]) == 2
    wb = openpyxl.load_workbook(BytesIO(fleet.build_generated_plan_workbook_bytes(original)))
    row = list(wb["Route Measurements"].values)[1]
    assert row[:5] == ("R1", 7, 1, 5, 2)
    assignments = list(wb["current_plan_assignments"].values)
    assert [row[-1] for row in assignments[1:]] == expected
    assert original == before


@pytest.mark.parametrize("change", [
    "unverified", "partial", "provider_missing", "missing_dwell", "contradictory_total",
])
def test_missing_or_invalid_measurements_do_not_reuse_planning_values(change):
    original = source()
    route = original["routes"][0]
    if change == "unverified":
        route["route_evidence"]["status"] = "needs_review"
    elif change == "partial":
        route["route_evidence"]["complete"] = False
    elif change == "provider_missing":
        route["route_evidence"] = {}
        route["evidence_status"] = "unavailable"
        route["final_route_traffic_gate"] = {"status": "unavailable"}
    elif change == "missing_dwell":
        route.pop("stop_service_time_s")
    else:
        route["final_route_traffic_gate"]["verified_total_duration_s"] = 421
    before = deepcopy(original)
    view = fleet.fleet_route_display_view(original)
    assert view == fleet.fleet_route_display_view(view)
    assert view["summary"]["total_duration_min"] is None
    assert view["summary"]["route_measurement_review_count"] == 1
    assert view["route_rows"][0]["duration_min"] is None
    data = fleet.build_route_preview_map_data(original)
    assert data["routes"][0]["duration_s"] is None
    assert data["routes"][0]["evidence_status"] == "needs_review"
    assert data["routes"][0]["final_route_traffic_gate"]["status"] == "unavailable"
    assert data["summary"]["duration_s"] is None
    assert all(stop["scheduled_time_minutes"] is None and stop["cumulative_duration_s"] is None for stop in data["stops"])
    wb = openpyxl.load_workbook(BytesIO(fleet.build_generated_plan_workbook_bytes(original)))
    assert wb["Route Measurements"]["B2"].value is None
    assert "needs review" in wb["Route Measurements"]["H2"].value
    assert all(row[-1] is None for row in list(wb["current_plan_assignments"].values)[1:])
    if change == "provider_missing":
        assert data["routes"][0]["geometry"] == [] and data["routes"][0]["geometry_segments"] == []
    assert original == before


def test_no_false_stop_schedules_from_valid_route_total_with_missing_legs():
    original = source()
    original["routes"][0]["route_evidence"]["legs"].pop()
    data = fleet.build_route_preview_map_data(original)
    assert data["routes"][0]["duration_s"] == 420
    assert all(stop["scheduled_time_minutes"] is None for stop in data["stops"])


def test_unknown_route_prevents_partial_fleet_sum_and_keeps_true_zero():
    original = source()
    broken = deepcopy(original["routes"][0])
    broken["cluster_id"] = "R2"
    broken["route_evidence"]["status"] = "needs_review"
    original["routes"].append(broken)
    view = fleet.fleet_route_display_view(original)
    assert view["summary"]["total_duration_min"] is None and view["summary"]["total_distance_km"] is None
    original = source()
    route = original["routes"][0]
    route["stop_service_time_s"] = 0
    route["route_evidence"].update(duration_s=0, distance_m=0)
    for leg in route["route_evidence"]["legs"]:
        leg.update(duration_s=0, distance_m=0)
    route["final_route_traffic_gate"].update(verified_drive_duration_s=0, verified_total_duration_s=0, verified_distance_m=0)
    view = fleet.fleet_route_display_view(original)
    assert view["summary"]["total_duration_min"] == 0 and view["summary"]["total_distance_km"] == 0


def test_legacy_values_are_labelled_and_not_remeasured():
    original = source()
    route = original["routes"][0]
    route.pop("route_evidence")
    route.pop("final_route_traffic_gate")
    view = fleet.fleet_route_display_view(original)
    assert view["summary"]["total_duration_min"] == 1.5
    assert view["summary"]["route_measurement_reference_count"] == 1
    assert view["routes"][0]["display_metrics"]["source"] == "planning_reference"
    assert "Planning reference" in view["route_rows"][0]["measurement_note"]


def test_historical_read_refreshes_presentations_not_source_or_provider(monkeypatch):
    original = source()
    original["rows"] = original.pop("route_rows")
    original.update(map_data={"routes": [{"duration_s": 99999}]}, map_html="OLD HTML", workbook_base64="OLD EXCEL")
    record = {"run_id": "saved", "global_plan_result": original}
    before = deepcopy(record)
    monkeypatch.setattr(backend, "FreshRouteProvider", lambda *a, **k: pytest.fail("No routing on history read"))
    monkeypatch.setattr(fleet, "compute_osrm_route_leg_details", lambda *a, **k: pytest.fail("No OSRM on history read"))
    result = backend._hydrate_fleet_planner_history_record(record)["global_plan_result"]
    assert result["map_data"]["routes"][0]["duration_s"] == 420
    assert result["rows"][0]["duration_min"] == 7
    assert result["workbook_base64"] != "OLD EXCEL" and "map_html" not in result
    assert record == before


def test_failed_history_read_does_not_return_stale_map_or_workbook(monkeypatch):
    original = source()
    original.update(map_data={"routes": [{"duration_s": 99999}]}, map_html="OLD HTML", workbook_base64="OLD EXCEL")
    before = deepcopy(original)
    monkeypatch.setattr(fleet, "fleet_route_display_view", lambda *_: (_ for _ in ()).throw(ValueError("invalid saved input")))
    result = backend._ensure_fleet_planner_map_data(original)
    assert not {"map_data", "map_html", "workbook_base64"} & result.keys()
    assert result["summary"]["total_duration_min"] is None
    assert result["route_rows"][0]["duration_min"] is None
    assert original == before


def test_live_attachment_records_existing_dwell_rule_and_preserves_solver_order(monkeypatch):
    original = source()
    evidence = deepcopy(original["routes"][0]["route_evidence"])
    route = original["routes"][0]
    route.pop("stop_service_time_s")
    route.pop("route_evidence")
    route.pop("final_route_traffic_gate")
    before_order = deepcopy(route["order"])
    class Provider:
        def __init__(self, *a, **k):
            self.state = {}
        def route(self, points, **kwargs):
            assert [p["address"] for p in points] == ["A", "Zero waypoint", "School"]
            return deepcopy(evidence)
    monkeypatch.setattr(backend, "FreshRouteProvider", Provider)
    backend._attach_fleet_route_measurements(original, fleet)
    assert route["stop_service_time_s"] == 120
    assert route["raw_osrm_duration_s"] == 90
    assert route["order"] == before_order
    assert original["summary"]["total_duration_min"] == 7
    assert original["route_rows"][0]["duration_min"] == 7


def test_legacy_html_uses_the_same_measurement_contract_without_provider_calls(monkeypatch):
    original = source()
    before = deepcopy(original)
    captured = {}
    def render(points, routes, output, **kwargs):
        captured["routes"] = routes
        Path(output).write_text(fleet.runtime.build_map_summary_html(points, routes), encoding="utf-8")
    monkeypatch.setattr(fleet.runtime, "render_map", render)
    html = fleet.build_route_preview_map_html(original)
    assert route_display_metrics(captured["routes"][0])["duration_s"] == 420
    assert f"Duration: {fleet.runtime.seconds_to_human(420)}" in html
    assert original == before


@pytest.mark.parametrize("valid", [True, False])
def test_actual_folium_html_export_keeps_saved_measurements_and_no_live_io(monkeypatch, valid):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("Export cannot call providers"))
    original = source()
    if not valid:
        original["routes"][0]["route_evidence"].update(status="needs_review", complete=False)
    html = fleet.build_route_preview_map_html(original)
    assert "leaflet" in html.lower()
    if valid:
        assert fleet.runtime.seconds_to_human(420) in html
    else:
        assert "Not available" in html and "needs review" in html
