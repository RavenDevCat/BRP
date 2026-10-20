"""Return measurements derived from immutable morning audit scenario snapshots."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from paired_return import MAX_DELAY_S, fingerprint, reverse_payload, run_return_measurement

SCENARIOS = ("current_plan", "time_constrained", "exception_preserving")


def is_audit_return(record):
    meta = record.get("metadata") or {}
    return meta.get("measurement_kind") == "pm_return" and meta.get("job_kind") == "route_audit"


def related_pair_records(record, get_job):
    """Resolve only reciprocal, same-owner/scope links, never an arbitrary job ID."""
    meta = record.get("metadata") or {}
    ids = meta.get("paired_job_ids") or {}
    if not meta.get("pair_id"):
        return [record]
    if ids.get(meta.get("period")) != record["job_id"]:
        raise ValueError("Invalid paired history identity.")
    records = {record["job_id"]: record}
    for job_id in ids.values():
        other = get_job(job_id)
        if other is None:
            continue
        other_meta = other.get("metadata") or {}
        if (other_meta.get("pair_id") != meta["pair_id"]
                or other_meta.get("paired_job_ids") != ids
                or other_meta.get("job_queue_scope") != meta.get("job_queue_scope")
                or str(other.get("owner_email") or "").lower() != str(record.get("owner_email") or "").lower()):
            raise ValueError("Paired history identity mismatch.")
        records[job_id] = other
    return list(records.values())


def scenario_payload(morning, key):
    scenario = ((morning.get("result") or {}).get("structured_results") or {}).get(key) or {}
    if not scenario.get("routes"):
        raise ValueError("Morning scenario has no route solution.")
    points = {int(p.get("node_id", index)): p for index, p in enumerate(scenario.get("points") or [])}
    if not points:
        raise ValueError("Morning scenario has no point snapshot.")
    stops, route_ids = [], set()
    for index, route in enumerate(scenario["routes"]):
        route_id = str(route.get("route_id") or f"Bus {route.get('vehicle_id', index + 1)}")
        if route_id in route_ids:
            raise ValueError("Duplicate morning scenario route identity.")
        route_ids.add(route_id)
        route_stops = []
        for sequence, node in enumerate(route.get("nodes") or []):
            point = deepcopy(points[int(node)])
            stop = {
                "route_id": route_id, "stop_id": f"{route_id}__{sequence}",
                "stop_sequence": sequence, "source_node_id": int(node),
                "address": point["address"], "country": point.get("country"), "city": point.get("city"),
                "passenger_count": int(point.get("passenger_count") or 0),
                "is_depot": bool(point.get("is_depot")),
                "source_point": point,
                "source_display_route_id": route.get("display_route_id"),
                "source_route": {
                    "route_index": index,
                    "display_route_id": route.get("display_route_id"),
                    "source_route_id": route.get("source_route_id"),
                    "exception_role": route.get("exception_role"),
                    "route_change_type": route.get("route_change_type"),
                    "vehicle_id": route.get("vehicle_id"),
                    "bus_type_name": route.get("bus_type_name") or "",
                    "bus_capacity": route.get("bus_capacity"),
                },
            }
            route_stops.append(stop)
        if sum(s["passenger_count"] for s in route_stops if not s["is_depot"]) != int(route["load"]):
            raise ValueError("Morning route rider assignments do not match its point snapshot.")
        stops.extend(route_stops)
    source = morning["prepared_payload"]
    prepared = {
        "input_records": deepcopy(source["input_records"]),
        "original_points": deepcopy(list(points.values())),
        "current_plan": {"service_direction": "To School", "stops": stops},
    }
    return reverse_payload(prepared), scenario.get("scenario_status") or (
        "passed" if scenario.get("traffic_feasible") else "not_verified")


def bind_morning_scenarios(record, get_job, now=None):
    """Return False while waiting; freeze all scenarios once the parent completes."""
    if not is_audit_return(record):
        return True
    meta = record["metadata"]
    related_pair_records(record, get_job)
    morning_id = (meta.get("paired_job_ids") or {}).get("am")
    morning = get_job(morning_id) if morning_id else None
    if morning is None:
        raise ValueError("Morning audit is missing; return scenarios cannot be measured.")
    if morning.get("status") in {"failed", "canceled"}:
        raise ValueError(f"Morning audit {morning['status']}; return scenarios cannot be measured.")
    if morning.get("status") != "succeeded":
        meta["return_dependency_status"] = "waiting_for_morning"
        return False
    if meta.get("return_source_version"):
        return True
    finished = datetime.fromisoformat(morning["finished_at"])
    scheduled = datetime.fromisoformat(record.get("scheduled_start_at") or meta["scheduled_start_at"])
    eligible = max(finished, scheduled)
    expires = eligible + timedelta(seconds=MAX_DELAY_S)
    if (now or datetime.now(timezone.utc)) > expires:
        raise ValueError("Return measurement window expired after morning completion; past traffic cannot be replayed.")
    snapshots = {}
    for key in SCENARIOS:
        try:
            prepared, status = scenario_payload(morning, key)
            snapshots[key] = {"prepared_payload": prepared, "source_status": status}
        except (ValueError, KeyError, TypeError) as exc:
            snapshots[key] = {"source_status": "unavailable", "error": str(exc)}
    version = fingerprint(snapshots)
    record["prepared_payload"] = {"return_scenarios": snapshots}
    meta.update(return_source_job_id=morning_id, return_source_version=version,
                return_source_finished_at=morning["finished_at"], return_dependency_status="ready",
                return_delayed=finished > scheduled, schedule_expires_at=expires.isoformat())
    return True


def run_audit_return(record, *, checkpoint=None, check_canceled=None, provider_factory=None, now=None):
    import direct_school_analysis as analysis
    snapshots = record["prepared_payload"].get("return_scenarios") or {}
    if set(snapshots) != set(SCENARIOS):
        raise ValueError("Return scenarios are not bound to a completed morning audit.")
    config = dict(record["metadata"]["analysis_config"])
    clock = now or (lambda: datetime.now(timezone.utc))
    provider = (provider_factory or analysis.FreshRouteProvider)(
        "amap", departure_time=None, api_call_limit=int(config.get("provider_call_limit", 500)))
    meta = record["metadata"]
    result = {
        "analysis_kind": "pm_return", "analysis_version": 2, "status": "running",
        "started_at": clock().isoformat(), "service_direction": "From School",
        "timing_basis": "current_traffic_snapshot", "source_job_id": meta["return_source_job_id"],
        "source_version": meta["return_source_version"], "source_finished_at": meta["return_source_finished_at"],
        "scheduled_start_at": record["scheduled_start_at"], "delayed": meta.get("return_delayed", False),
        "scenarios": {}, "progress": {},
        "parameters": deepcopy(config),
    }

    def update(key, partial):
        result["scenarios"][key] = {**partial, "source_status": snapshots[key]["source_status"],
                                    "scenario_key": key}
        result["progress"] = {
            "completed": sum(s.get("progress", {}).get("completed", 0) for s in result["scenarios"].values()),
            "total": sum(len({s["route_id"] for s in item.get("prepared_payload", {}).get("current_plan", {}).get("stops", [])})
                         for item in snapshots.values()),
            "provider_api_calls": provider.state.get("api_calls", 0),
            "in_run_reuse_count": provider.state.get("cache_hits", 0),
        }
        if checkpoint:
            checkpoint(deepcopy(result))

    for key in SCENARIOS:
        if check_canceled:
            check_canceled()
        snapshot = snapshots[key]
        if snapshot.get("error"):
            update(key, {"analysis_kind": "pm_return", "status": "unavailable", "error": snapshot["error"],
                         "started_at": result["started_at"], "routes": [],
                         "summary": {"route_count": 0, "completed_route_count": 0, "under_one_hour_count": 0, "rider_count": 0},
                         "progress": {"completed": 0, "total": 0, "provider_api_calls": 0}})
            continue
        measured = run_return_measurement(snapshot["prepared_payload"], config, provider=provider,
            checkpoint=lambda partial, key=key: update(key, partial), check_canceled=check_canceled, now=now)
        update(key, measured)
    result["status"] = "complete" if all(s["status"] == "complete" for s in result["scenarios"].values()) else "partial"
    result["completed_at"] = clock().isoformat()
    return result
