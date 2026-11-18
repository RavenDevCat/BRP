"""Bounded live-measurement repair of a Fleet candidate, without relaxing limits."""
from copy import deepcopy
import math


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


def _rank_splits(service, direction, other_routes, vehicle_pool, *, dwell_s=0.0):
    caps = sorted((int(v["capacity"]) for v in vehicle_pool), reverse=True)
    others = [_load(r) for r in other_routes]
    if len(others) + 2 > len(caps):
        return []
    loads, road = [0], [0.0]
    points = [p for _, p in service]
    for point in points:
        loads.append(loads[-1] + int(point.get("student_count") or 0))
    for point in points[:-1]:
        road.append(road[-1] + float(point.get("static_next_drive_s") or 0))
    static_known = (all(p.get("static_school_drive_s") is not None for p in points)
                    and all(p.get("static_next_drive_s") is not None for p in points[:-1]))
    ranks = []
    for split in range(1, len(points)):
        left, right = loads[split], loads[-1] - loads[split]
        needed = sorted([*others, left, right], reverse=True)
        if any(load > cap for load, cap in zip(needed, caps)):
            continue
        if static_known:
            a, b = (points[0], points[split]) if direction == "from_school" else (points[split-1], points[-1])
            t1 = road[split-1] + a["static_school_drive_s"] + split * dwell_s
            t2 = road[-1] - road[split] + b["static_school_drive_s"] + (len(points)-split) * dwell_s
            drive_score = (max(t1, t2), t1+t2)
        else:
            drive_score = (0, 0)
        ranks.append((*drive_score, abs(left-right), abs(len(points)-2*split), split))
    return [rank[-1] for rank in sorted(ranks)]


def _split_children(parent, service, school, direction, cut):
    children = []
    for part in (service[:cut], service[cut:]):
        nodes, stops = zip(*part)
        children.append({
            "cluster_id": parent["cluster_id"], "source_cluster_id": parent.get("source_cluster_id", parent["cluster_id"]),
            "solver": "global_ortools_live_split", "service_direction": direction,
            "order": [0, *nodes] if direction == "from_school" else [*nodes, 0],
            "ordered_points": deepcopy([school, *stops] if direction == "from_school" else [*stops, school]),
            "leg_details": [], "warnings": [],
        })
    return children


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

        direction = parent.get("service_direction", result["summary"].get("service_direction", "to_school"))
        points, order = parent["ordered_points"], parent["order"]
        school = points[0] if direction == "from_school" else points[-1]
        service = list(zip(order[1:], points[1:])) if direction == "from_school" else list(zip(order[:-1], points[:-1]))
        index = routes.index(parent)
        dwell_s = (parent.get("stop_service_time_s") or 0) / len(service)
        ranked = _rank_splits(service, direction, routes[:index] + routes[index+1:], vehicle_pool, dwell_s=dwell_s)
        if not ranked:
            parent["refinement_reason"] = "no_capacity_feasible_cut"
            blocked.add(id(parent))
            continue
        if not budget_available():
            parent["refinement_reason"] = "measurement_budget_exhausted"
            blocked.add(id(parent))
            continue
        best = None
        for cut in ranked[:3]:
            if not budget_available():
                break
            children = _split_children(parent, service, school, direction, cut)
            measure(children)
            gates = [r.get("final_route_traffic_gate") or {} for r in children]
            statuses = [g.get("status") for g in gates]
            verified = all(r.get("route_evidence", {}).get("status") == "verified"
                           and r["route_evidence"].get("complete") is not False
                           and isinstance(g.get("verified_total_duration_s"), (int, float))
                           and not isinstance(g["verified_total_duration_s"], bool)
                           and math.isfinite(g["verified_total_duration_s"]) and g["verified_total_duration_s"] >= 0
                           and status in {"passed", "failed"} for r, g, status in zip(children, gates, statuses))
            attempt = {"source_route": parent["cluster_id"], "cut": cut, "accepted": False,
                       "measured": verified, "child_statuses": statuses}
            attempts.append(attempt)
            if not verified:
                continue
            score = (statuses.count("failed"), max(g["verified_total_duration_s"] for g in gates))
            if best is None or score < best[0]:
                best = score, children, attempt
            if not score[0]:
                break
        if best is None:
            parent["refinement_reason"] = "split_not_verified_within_budget" if not budget_available() else "child_measurement_unavailable"
            blocked.add(id(parent))
            continue
        _, children, accepted = best
        tentative = routes[:index] + children + routes[index+1:]
        if not assign_vehicles(tentative, vehicle_pool):
            raise RuntimeError("Fleet capacity changed during split measurement")
        accepted["accepted"] = True
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
    reasons = {r.get("refinement_reason") for r in routes}
    if "no_capacity_feasible_cut" in reasons:
        result["refinement_note"] += " No capacity-feasible split for one or more routes with the available fleet."
    if reasons & {"split_not_verified_within_budget", "measurement_budget_exhausted", "child_measurement_unavailable"}:
        result["refinement_note"] += " One or more splits could not be verified within the measurement budget or available evidence."
    return result
