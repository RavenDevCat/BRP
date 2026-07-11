import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))
backend_service = importlib.import_module("backend_service")
planner_core = importlib.import_module("planner_core")


def test_time_impact_limit_payload_normalizes_user_input():
    payload = backend_service._planner_config_payload({"time_impact_limit_minutes": 20})

    assert payload["time_impact_limit_minutes"] == 20


def test_time_impact_summary_uses_user_limit():
    stops = [
        {
            "passenger_count": 3,
            "time_impact": {
                "comparison_available": True,
                "adverse_delta_minutes": 18,
            },
        }
    ]

    summary = backend_service._time_impact_summary(stops, acceptance_threshold=20)

    assert summary["acceptance_threshold_minutes"] == 20
    assert summary["within_acceptance_stop_count"] == 1
    assert summary["over_acceptance_stop_count"] == 0


def test_time_impact_scenario_label_falls_back_to_config_limit():
    job_record = {
        "config": {"time_impact_limit_minutes": 20},
        "result": {"structured_results": {"time_constrained": {}}},
    }

    assert (
        backend_service._job_map_scenario_label(
            job_record,
            job_record["result"],
            "time_constrained",
        )
        == "Strict Plan"
    )


def test_time_constrained_replaces_paused_free_baseline_in_summary():
    summary = planner_core.summarize_structured_results(
        {
            "original": planner_core._build_skipped_scenario_result("free paused"),
            "time_constrained": {
                "stop_count": 12,
                "bus_count": 4,
                "bus_mix": {"30-fbus": 4},
            },
            "subway": {},
            "nearby": {},
        },
        uploaded_address_count=12,
    )

    assert summary["original_valid_stops"] == 12
    assert summary["original_vehicle_count"] == 4
    assert summary["original_bus_mix"] == {"30-fbus": 4}


def test_further_most_map_keys_are_retired():
    assert "further_most" not in backend_service.MAP_ARTIFACT_KEYS
    assert "further_most_nearby" not in backend_service.MAP_ARTIFACT_KEYS


def test_to_school_route_limit_respects_time_window():
    config = planner_core.PlannerConfig(
        service_direction="To School",
        max_route_duration_minutes=122,
        time_window_start="06:30",
        time_window_end="08:00",
    )

    assert planner_core.effective_route_duration_limit_minutes(config) == 90


def test_route_budget_does_not_shorten_user_time_window():
    config = planner_core.PlannerConfig(
        service_direction="To School",
        max_route_duration_minutes=20,
        time_window_start="06:30",
        time_window_end="08:00",
    )

    assert planner_core.effective_route_duration_limit_minutes(config) == 90


def test_from_school_does_not_inherit_default_am_window():
    config = planner_core.PlannerConfig(
        service_direction="From School",
        from_school_departure_time="15:40",
        max_route_duration_minutes=122,
    )

    assert planner_core.effective_route_duration_limit_minutes(config) == 120
    assert planner_core._from_school_time_window(config) == (15 * 60 + 40, 17 * 60 + 40)


def test_from_school_route_limit_respects_custom_time_window():
    config = planner_core.PlannerConfig(
        service_direction="From School",
        from_school_departure_time="15:40",
        max_route_duration_minutes=122,
        time_window_start="15:40",
        time_window_end="17:10",
    )

    assert planner_core.effective_route_duration_limit_minutes(config) == 90


def test_vehicle_ladder_starts_at_required_saving_target(monkeypatch):
    seen_targets: list[int] = []

    def fake_compute(_planner, _points, _label, *, reduced_vehicle_limit=None, **_kwargs):
        seen_targets.append(int(reduced_vehicle_limit or 0))
        if reduced_vehicle_limit == 3:
            return {
                "bus_count": 3,
                "routes": [{"route_id": "Bus 1"} for _ in range(3)],
                "traffic_gate": {"status": "passed"},
                "feasibility_report": {"status": "passed", "failure_reasons": []},
            }
        return {
            "bus_count": int(reduced_vehicle_limit or 0),
            "routes": [],
            "traffic_gate": {"status": "failed", "failed_route_count": 1},
            "feasibility_report": {"status": "failed", "failure_reasons": ["arrival_window"]},
        }

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)

    result = planner_core._solve_vehicle_ladder_scenario(
        object(),
        [{"is_depot": True}, {"passenger_count": 1}],
        "15-minute test",
        current_route_count=5,
        minimum_vehicle_reduction=2,
    )

    assert seen_targets[:2] == [3, 2]
    assert result["bus_count"] == 3
    assert result["vehicle_saving_target"]["status"] == "passed"
    assert result["vehicle_ladder_search"]["attempts"][0]["all_constraints_passed"] is True


def test_vehicle_ladder_never_relaxes_the_hard_vehicle_saving_target(monkeypatch):
    seen_targets: list[int] = []

    def fake_compute(_planner, _points, _label, *, reduced_vehicle_limit=None, **_kwargs):
        target = int(reduced_vehicle_limit or 0)
        seen_targets.append(target)
        if target == 4:
            return {
                "bus_count": 4,
                "routes": [{"route_id": "Bus 1"} for _ in range(4)],
                "traffic_gate": {"status": "passed"},
                "feasibility_report": {"status": "passed", "failure_reasons": []},
            }
        return {
            "bus_count": target,
            "routes": [{"route_id": "Bus 1"} for _ in range(target)],
            "traffic_gate": {"status": "failed", "failed_route_count": 1},
            "feasibility_report": {"status": "failed", "failure_reasons": ["arrival_window"]},
        }

    monkeypatch.setattr(planner_core, "_compute_scenario_without_render", fake_compute)

    result = planner_core._solve_vehicle_ladder_scenario(
        object(),
        [{"is_depot": True}, {"passenger_count": 1}],
        "15-minute test",
        current_route_count=5,
        minimum_vehicle_reduction=2,
    )

    assert seen_targets == [3, 2, 1]
    assert 4 not in seen_targets
    assert result["bus_count"] <= 3
    assert result["traffic_gate"]["status"] == "failed"
    assert result["vehicle_saving_target"]["status"] == "passed"
    assert result["feasibility_report"]["status"] == "failed"
