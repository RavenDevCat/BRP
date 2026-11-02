"""Bounded live-measurement repair of a Fleet candidate, without relaxing limits."""
from copy import deepcopy


def _load(route):
    direction = route.get("service_direction", "to_school")
    points = route.get("ordered_points") or []
    stops = points[1:] if direction == "from_school" else points[:-1]
    return sum(int(point.get("student_count", 0) or 0) for point in stops)


def assign_vehicles(routes, vehicle_pool):
    available = list(vehicle_pool)
    selected = {}
    for index in sorted(range(len(routes)), key=lambda i: _load(routes[i]), reverse=True):
        load = _load(routes[index])
        choices = [(int(v["capacity"]), int(v.get("fixed_cost", 0)), j)
                   for j, v in enumerate(available) if int(v["capacity"]) >= load]
        if not choices:
            return False
        vehicle = deepcopy(available.pop(min(choices)[2]))
        vehicle.update(student_capacity=vehicle["capacity"],
                       load_factor=load / max(1, vehicle["capacity"]),
                       empty_seats=vehicle["capacity"] - load)
        selected[index] = vehicle
    for index, vehicle in selected.items():
        routes[index]["selected_vehicle"] = vehicle
    return True


def refine_measured_plan(plan, vehicle_pool, measure, *, max_splits=32, budget_available=lambda: True):
    """Split failed multi-stop routes and retain the last fully measured candidate."""
    result = deepcopy(plan)
    routes = result["routes"]
    attempts = []
    blocked = set()
    splits = 0
    while splits < max_splits:
        candidates = [route for route in routes
                      if route.get("final_route_traffic_gate", {}).get("status") == "failed"
                      and len(route.get("ordered_points") or []) > 2 and id(route) not in blocked]
        if not candidates:
            break
        parent = max(candidates, key=lambda route: route["final_route_traffic_gate"].get("verified_total_duration_s", 0))
        if not budget_available():
            parent["refinement_reason"] = "measurement_budget_exhausted"
            break
        direction = parent.get("service_direction", result["summary"].get("service_direction", "to_school"))
        points, order = parent["ordered_points"], parent["order"]
        school = points[0] if direction == "from_school" else points[-1]
        service = list(zip(order[1:], points[1:])) if direction == "from_school" else list(zip(order[:-1], points[:-1]))
        cut = len(service) // 2
        children = []
        for part in (service[:cut], service[cut:]):
            nodes, stops = zip(*part)
            child = {
                "cluster_id": parent["cluster_id"],
                "source_cluster_id": parent.get("source_cluster_id", parent["cluster_id"]),
                "solver": "global_ortools_live_split", "service_direction": direction,
                "order": [0, *nodes] if direction == "from_school" else [*nodes, 0],
                "ordered_points": deepcopy([school, *stops] if direction == "from_school" else [*stops, school]),
                "leg_details": [], "warnings": [],
            }
            children.append(child)
        index = routes.index(parent)
        tentative = routes[:index] + children + routes[index + 1:]
        if not assign_vehicles(deepcopy(tentative), vehicle_pool):
            parent["refinement_reason"] = "available_fleet_exhausted"
            blocked.add(id(parent))
            continue
        measure(children)
        available = all(child.get("route_evidence", {}).get("status") == "verified"
                        and child.get("final_route_traffic_gate", {}).get("status") in {"passed", "failed"}
                        for child in children)
        attempts.append({"source_route": parent["cluster_id"], "accepted": available,
                         "child_statuses": [child.get("final_route_traffic_gate", {}).get("status") for child in children]})
        if not available:
            parent["refinement_reason"] = "child_measurement_unavailable"
            blocked.add(id(parent))
            continue
        assert assign_vehicles(tentative, vehicle_pool)
        routes[:] = tentative
        splits += 1
    rows = []
    exceptions = []
    for index, route in enumerate(routes, 1):
        route.setdefault("source_cluster_id", route["cluster_id"])
        route["cluster_id"] = f"G{index:02d}"
        if route.get("final_route_traffic_gate", {}).get("status") == "failed" and len(route["ordered_points"]) == 2:
            route["refinement_reason"] = "direct_trip_exceeds_limit"
            exceptions.append(route["cluster_id"])
        vehicle = route["selected_vehicle"]
        metrics = route.get("display_metrics") or {}
        duration, distance = metrics.get("duration_s"), metrics.get("distance_m")
        rows.append({"cluster_id": route["cluster_id"], "solver": route.get("solver"),
                     "service_direction": route.get("service_direction"), "students": _load(route),
                     "stops": len(route["ordered_points"]) - 1, "vehicle": vehicle.get("display_name"),
                     "duration_min": round(duration / 60, 1) if duration is not None else None,
                     "distance_km": round(distance / 1000, 2) if distance is not None else None,
                     "load_factor_pct": round(_load(route) / max(1, vehicle["capacity"]) * 100, 1),
                     "warnings": "; ".join(route.get("warnings") or [])})
    result["route_rows"] = rows
    result["summary"].update(route_count=len(routes), candidate_vehicle_count=len(vehicle_pool))
    result["refinement"] = {"split_count": splits, "attempts": attempts,
                            "direct_exception_routes": exceptions, "max_splits": max_splits,
                            "unresolved_routes": [route["cluster_id"] for route in routes
                                if route.get("final_route_traffic_gate", {}).get("status") != "passed"]}
    result["refinement_note"] = f"Live refinement: {splits} splits; {len(result['refinement']['unresolved_routes'])} routes remain unverified or over limit."
    return result
