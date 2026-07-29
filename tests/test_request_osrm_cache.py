from __future__ import annotations

import BusingProblem as planner


def test_request_osrm_caches_are_exact_and_return_copies(monkeypatch) -> None:
    planner._BRP_RUNTIME_PROFILE = {}
    planner._BRP_OSRM_MATRIX_CACHE = {}
    planner._BRP_OSRM_LEG_CACHE = {}
    planner.OSRM_BASE_URL = "http://osrm.test"
    planner.STOP_SERVICE_SECONDS = 60
    points = [
        {"address": "School", "lat": 31.0, "lng": 121.0, "plot_lat": 31.0, "plot_lng": 121.0},
        {"address": "Stop", "lat": 31.1, "lng": 121.1, "plot_lat": 31.1, "plot_lng": 121.1},
    ]
    matrix_requests = 0

    def fake_osrm_request_json(*_args, **_kwargs):
        nonlocal matrix_requests
        matrix_requests += 1
        return {
            "durations": [[0, 10], [11, 0]],
            "distances": [[0, 100], [110, 0]],
        }

    monkeypatch.setattr(planner, "osrm_request_json", fake_osrm_request_json)
    first_time, first_distance = planner.build_osrm_full_matrix(points)
    first_time[0][1] = 999
    first_distance[0][1] = 999
    second_time, second_distance = planner.build_osrm_full_matrix(points)
    assert matrix_requests == 1
    assert second_time[0][1] == 70
    assert second_distance[0][1] == 100

    leg_requests = 0

    def fake_leg(_origin, _destination):
        nonlocal leg_requests
        leg_requests += 1
        return 100, 10, [(31.0, 121.0), (31.1, 121.1)], {"coordinate_source": "plot"}

    monkeypatch.setattr(planner, "_osrm_driving_direction_with_metadata_impl", fake_leg)
    first_leg = planner.osrm_driving_direction_with_metadata(points[0], points[1])
    first_leg[2].append((0.0, 0.0))
    first_leg[3]["coordinate_source"] = "changed"
    second_leg = planner.osrm_driving_direction_with_metadata(points[0], points[1])
    assert leg_requests == 1
    assert second_leg[2] == [(31.0, 121.0), (31.1, 121.1)]
    assert second_leg[3] == {"coordinate_source": "plot"}
