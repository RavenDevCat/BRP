import importlib


def _simple_matrix(size: int) -> list[list[int]]:
    matrix = [[0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i != j:
                matrix[i][j] = 60
    return matrix


def _point(index: int, passengers: int = 1) -> dict:
    return {
        "address": "School" if index == 0 else f"Stop {index}",
        "lat": 31.2 + index * 0.001,
        "lng": 121.4 + index * 0.001,
        "plot_lat": 31.2 + index * 0.001,
        "plot_lng": 121.4 + index * 0.001,
        "passenger_count": passengers,
        "is_depot": index == 0,
    }


def _configure_solver(monkeypatch):
    planner = importlib.import_module("BusingProblem")
    monkeypatch.setattr(planner, "BUS_TYPE_CONFIGS", [{"name": "Large Bus", "capacity": 42, "max_count": 8}])
    monkeypatch.setattr(planner, "VEHICLE_FIXED_COST", {"Large Bus": 0})
    monkeypatch.setattr(planner, "MIN_LOAD_TARGET", {"Large Bus": 0})
    monkeypatch.setattr(planner, "MIN_LOAD_PENALTY", {"Large Bus": 0})
    monkeypatch.setattr(planner, "MAX_STOPS_PER_ROUTE", 10)
    monkeypatch.setattr(planner, "COMFORT_LOAD_FACTOR", 0.85)
    monkeypatch.setattr(planner, "SERVICE_DIRECTION", "From School")
    monkeypatch.setattr(planner, "RESERVED_EXPRESS_BUSES", 0)
    monkeypatch.setattr(planner, "EXPRESS_THRESHOLD_KM", 9999.0)
    monkeypatch.setattr(planner, "MAX_ROUTE_DURATION_SECONDS", 3600)
    monkeypatch.setattr(planner, "ROUTE_DURATION_GRACE_SECONDS", 0)
    return planner


def test_solver_caps_routes_at_ten_service_stops(monkeypatch):
    planner = _configure_solver(monkeypatch)
    points = [_point(0)] + [_point(index) for index in range(1, 23)]
    matrix = _simple_matrix(len(points))

    routes = planner.solve_routes(points, matrix, matrix)

    assert len(routes) >= 3
    assert max(route["stop_count"] for route in routes) <= 10
    assert all(route["max_stops"] == 10 for route in routes)


def test_solver_uses_comfort_capacity_not_physical_capacity(monkeypatch):
    planner = _configure_solver(monkeypatch)
    points = [_point(0), _point(1, 30), _point(2, 30), _point(3, 30)]
    matrix = _simple_matrix(len(points))

    routes = planner.solve_routes(points, matrix, matrix)

    assert len(routes) >= 3
    assert all(route["comfort_capacity"] == 35 for route in routes)
    assert all(route["load"] <= route["comfort_capacity"] for route in routes)
