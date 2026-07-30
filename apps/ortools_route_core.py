from __future__ import annotations

from typing import Any, Iterable, Sequence


def register_matrix_transit(
    routing: Any,
    manager: Any,
    matrix: Sequence[Sequence[int | float]],
    *,
    zero_cost_to_nodes: Iterable[int] = (),
) -> int:
    zero_cost_to = frozenset(int(node) for node in zero_cost_to_nodes)

    def transit_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        if to_node in zero_cost_to:
            return 0
        return int(matrix[from_node][to_node])

    callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(callback_index)
    return callback_index


def add_route_time_dimension(
    routing: Any,
    transit_callback_index: int,
    max_duration_seconds: int,
    *,
    name: str = "Time",
) -> Any:
    routing.AddDimension(
        transit_callback_index,
        0,
        max(1, int(max_duration_seconds)),
        True,
        name,
    )
    return routing.GetDimensionOrDie(name)


def add_capacity_dimension(
    routing: Any,
    manager: Any,
    demands: Sequence[int],
    capacities: Sequence[int],
    *,
    name: str,
) -> Any:
    def demand_callback(index: int) -> int:
        return int(demands[manager.IndexToNode(index)])

    callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        callback_index,
        0,
        [int(capacity) for capacity in capacities],
        True,
        name,
    )
    return routing.GetDimensionOrDie(name)


def add_stop_count_dimension(
    routing: Any,
    manager: Any,
    service_nodes: Iterable[int],
    max_stops: int,
    *,
    name: str = "Stops",
) -> Any:
    counted_nodes = frozenset(int(node) for node in service_nodes)

    def stop_callback(from_index: int, to_index: int) -> int:
        del from_index
        return 1 if manager.IndexToNode(to_index) in counted_nodes else 0

    callback_index = routing.RegisterTransitCallback(stop_callback)
    routing.AddDimension(callback_index, 0, max(0, int(max_stops)), True, name)
    return routing.GetDimensionOrDie(name)


def build_guided_local_search_parameters(
    pywrapcp: Any,
    routing_enums_pb2: Any,
    *,
    first_solution_strategy: int,
    time_limit_seconds: int,
) -> Any:
    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = first_solution_strategy
    search.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search.time_limit.seconds = max(1, int(time_limit_seconds))
    return search
