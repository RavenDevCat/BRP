"""Opt-in final validation. No import-time I/O and no legacy-provider fallback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

import requests
try:
    from .quota_store_sqlite import SqliteQuotaStore
    from .route_evidence import EVIDENCE_VERSION
    from .google_routes_transport import ENDPOINT, FIELDS, post_routes, relay_url
except ImportError:
    from quota_store_sqlite import SqliteQuotaStore
    from route_evidence import EVIDENCE_VERSION
    from google_routes_transport import ENDPOINT, FIELDS, post_routes, relay_url

POLICY_VERSION = "google-final-v3"
from google_routes_quota import DEFAULT_LIMITS, MONTHLY_LIMIT, quota_periods
TZ = ZoneInfo("Asia/Shanghai")


class ValidationUnavailable(RuntimeError):
    """No verified Google result; never equivalent to a constraint violation."""
    def __init__(self, code, *, details=None):
        super().__init__(code)
        self.details = {"code": code, **(details or {})}


def validate_config(payload):
    mode = payload.get("final_time_validation_mode", "legacy")
    if mode not in {"legacy", "google"}:
        raise ValueError("final_time_validation_mode must be legacy or google.")
    if mode == "google":
        if payload.get("timing_policy", "arrival_anchored") not in {"arrival_anchored", "fixed_departure"}:
            raise ValueError("timing_policy must be arrival_anchored or fixed_departure.")
        value = payload.get("validation_service_date", "")
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("validation_service_date must be YYYY-MM-DD.") from None
    return mode


def availability():
    checks = (("BRP_GOOGLE_FINAL_ENABLED", "google_rollout_disabled"),
              ("BRP_GOOGLE_FINAL_DATA_USE_APPROVED", "google_data_use_not_approved"))
    for name, reason in checks:
        if os.environ.get(name, "").lower() != "true":
            return {"available": False, "reason": reason}
    try:
        relay = relay_url()
    except ValueError:
        return {"available": False, "reason": "google_relay_configuration_invalid"}
    if not relay and not os.environ.get("BRP_GOOGLE_ROUTES_API_KEY", "").strip():
        return {"available": False, "reason": "google_key_missing"}
    if not os.environ.get("BRP_GOOGLE_FINAL_QUOTA_DB", "").strip():
        return {"available": False, "reason": "google_budget_store_missing"}
    return {"available": True, "reason": None}


def monthly_budget():
    require_available()
    now = datetime.now(TZ)
    store = SqliteQuotaStore(Path(os.environ["BRP_GOOGLE_FINAL_QUOTA_DB"]))
    used = store.get_usage("google_routes", "compute_routes_pro", "month", now.strftime("%Y-%m"))["attempted"]
    return {"month": now.strftime("%Y-%m"), "timezone": "Asia/Shanghai",
            "limit": MONTHLY_LIMIT, "used": used, "remaining": max(0, MONTHLY_LIMIT-used)}


def require_available():
    state = availability()
    if not state["available"]:
        raise ValidationUnavailable(state["reason"])


def prepare_submission(payload, service_date=None):
    """Called by the server, not the browser, before any upload preparation."""
    if validate_config(payload) != "google":
        return payload
    require_available()
    result = dict(payload)
    result["validation_service_date"] = str(result.get("validation_service_date") or service_date or "")
    result.setdefault("timing_policy", "arrival_anchored" if result.get("service_direction", "To School") == "To School" else "fixed_departure")
    start, end = service_window(result)
    if end <= datetime.now(timezone.utc):
        raise ValidationUnavailable("google_service_window_in_past")
    result["validation_budget_id"] = uuid.uuid4().hex
    return result


def service_window(config):
    get = config.get if isinstance(config, dict) else lambda key, default=None: getattr(config, key, default)
    try:
        day = date.fromisoformat(get("validation_service_date", ""))
        def clock(value):
            hour, minute = map(int, value.split(":"))
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
        start, end = clock(get("time_window_start")), clock(get("time_window_end"))
    except (ValueError, TypeError, AttributeError):
        raise ValidationUnavailable("google_service_date_or_window_invalid") from None
    if start >= end:
        raise ValidationUnavailable("google_service_window_invalid")
    return start, end


def meters(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def seconds(value):
    if not isinstance(value, str) or not value.endswith("s"):
        raise ValidationUnavailable("google_duration_missing")
    result = float(value[:-1])
    if not math.isfinite(result) or result < 0:
        raise ValidationUnavailable("google_duration_invalid")
    return result


def location(value):
    item = value.get("latLng", {})
    lat, lng = float(item["latitude"]), float(item["longitude"])
    if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat) > 90 or abs(lng) > 180:
        raise ValidationUnavailable("google_location_invalid")
    return lat, lng


def parse_response(payload, points):
    try:
        if payload.get("fallbackInfo"):
            raise ValidationUnavailable("google_routing_fallback")
        route = payload["routes"][0]
        legs = route["legs"]
        if len(legs) != len(points)-1:
            raise ValidationUnavailable("google_leg_count_mismatch")
        measured = []
        for index, leg in enumerate(legs):
            start, end = location(leg["startLocation"]), location(leg["endLocation"])
            for endpoint, point_index, returned in (("start", index, start), ("end", index+1, end)):
                gap = meters(returned, points[point_index])
                if gap > 100:
                    raise ValidationUnavailable("google_pickup_snap_mismatch", details={
                        "leg_index": index, "point_index": point_index, "endpoint": endpoint,
                        "requested_coordinate": list(points[point_index]), "returned_coordinate": list(returned),
                        "snap_distance_m": round(gap, 2), "limit_m": 100})
            geometry = leg["polyline"]["geoJsonLinestring"]["coordinates"]
            if len(geometry) < 2:
                raise ValidationUnavailable("google_geometry_missing")
            for lng, lat in geometry:
                location({"latLng": {"latitude": lat, "longitude": lng}})
            distance = float(leg["distanceMeters"])
            if not math.isfinite(distance) or distance < 0:
                raise ValidationUnavailable("google_distance_invalid")
            if meters(tuple(reversed(geometry[0])), start) > 30 or meters(tuple(reversed(geometry[-1])), end) > 30:
                raise ValidationUnavailable("google_geometry_endpoint_mismatch")
            measured.append({"duration_s": seconds(leg["duration"]), "distance_m": distance,
                             "start": start, "end": end, "geometry": geometry})
        duration = seconds(route["duration"])
        distance = float(route["distanceMeters"])
        if not math.isfinite(distance) or distance < 0:
            raise ValidationUnavailable("google_distance_invalid")
        if abs(sum(x["duration_s"] for x in measured)-duration) > max(2, len(legs)):
            raise ValidationUnavailable("google_duration_reconciliation")
        if abs(sum(x["distance_m"] for x in measured)-distance) > max(2, len(legs)):
            raise ValidationUnavailable("google_distance_reconciliation")
        return measured
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValidationUnavailable("google_response_invalid") from exc


class GoogleRoutesClient:
    def __init__(self, budget_id, store, limits=DEFAULT_LIMITS, *, transport=None,
                 now=lambda: datetime.now(timezone.utc), check_canceled=lambda: None):
        if (not budget_id or len(limits) != 3
                or any(type(limit) is not int or not 0 <= limit <= MONTHLY_LIMIT for limit in limits)
                or limits[2] == 0):
            raise ValidationUnavailable("google_budget_invalid")
        self.budget_id, self.store, self.limits = budget_id, store, limits
        self.transport, self.now, self.check_canceled = transport, now, check_canceled
        self.calls = 0
        self.protected_calls = 0
        self.request_headroom = 0

    def periods(self):
        return quota_periods(self.budget_id, self.now(), self.limits)

    def can_afford(self, count, reserve=0):
        return all(limit <= 0 or self.store.get_usage("google_routes", "compute_routes_pro", kind, key)["attempted"]
                   + count + reserve <= limit for kind, key, limit in self.periods())

    def route(self, points, departure):
        self.check_canceled()
        now = self.now()
        if departure <= now:
            raise ValidationUnavailable("google_departure_in_past")
        if self.transport is None:
            require_available()
        periods = quota_periods(self.budget_id, now, self.limits)
        def waypoint(point):
            return {"location": {"latLng": {"latitude": point[0], "longitude": point[1]}}}
        if not 2 <= len(points) <= 27:
            raise ValidationUnavailable("google_waypoint_limit")
        body = {"origin": waypoint(points[0]), "destination": waypoint(points[-1]),
                "intermediates": [waypoint(x) for x in points[1:-1]], "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE_OPTIMAL", "departureTime": departure.isoformat(),
                "optimizeWaypointOrder": False, "polylineEncoding": "GEO_JSON_LINESTRING"}
        self.store.reserve_rate_limit("google-final-routes", 2.0)
        self.check_canceled()
        try:
            self.store.reserve_usage("google_routes", "compute_routes_pro", periods,
                                     headroom=self.request_headroom,
                                     sku_estimate="compute_routes_pro", provider_label="Google Routes")
        except RuntimeError as exc:
            raise ValidationUnavailable("google_budget_cap: " + str(exc)) from None
        self.calls += 1
        success = False
        try:
            reserve_attempt = getattr(self, "reserve_attempt", None)
            if reserve_attempt is not None and not reserve_attempt():
                raise ValidationUnavailable("google_review_budget_or_claim_unavailable")
            if self.transport is not None:
                payload = self.transport(body)
            else:
                response = post_routes(body, self.budget_id)
                if response.status_code != 200:
                    raise ValidationUnavailable(f"google_http_{response.status_code}")
                payload = response.json()
            result = parse_response(payload, points)
            self.check_canceled()
            success = True
            return result
        except requests.RequestException:
            raise ValidationUnavailable("google_transport_failed") from None
        finally:
            self.store.mark_usage_result("google_routes", "compute_routes_pro",
                                         [(a, b) for a, b, _ in periods], succeeded=success)


@dataclass(frozen=True)
class Measurement:
    departure: datetime
    legs: list
    dwell_s: float

    @property
    def drive_s(self):
        return sum(x["duration_s"] for x in self.legs)

    @property
    def arrival(self):
        return self.departure + timedelta(seconds=self.drive_s+self.dwell_s)


class ValidationSession:
    """One budget scope for current baseline, candidate search and repairs."""
    def __init__(self, client, max_rounds=4):
        self.client, self.max_rounds = client, max_rounds

    def __deepcopy__(self, memo):
        return self

    def measure(self, points, departure, dwell):
        legs, clock = [], departure
        if not any(dwell) and len(points) <= 27:
            legs = self.client.route(points, clock)
        else:
            # Rolling leg departures include dwell. Never stitch a fabricated road.
            for i in range(len(points)-1):
                clock += timedelta(seconds=dwell[i])
                try:
                    leg = self.client.route(points[i:i+2], clock)[0]
                except ValidationUnavailable as exc:
                    if "point_index" in exc.details:
                        exc.details["point_index"] += i
                    exc.details["leg_index"] = i
                    raise
                if legs and meters(legs[-1]["end"], leg["start"]) > 30:
                    raise ValidationUnavailable("google_cross_request_join_mismatch", details={
                        "point_index": i, "leg_index": i, "endpoint": "join",
                        "requested_coordinate": list(points[i]),
                        "previous_end_coordinate": list(legs[-1]["end"]),
                        "returned_coordinate": list(leg["start"]),
                        "snap_distance_m": round(meters(legs[-1]["end"], leg["start"]), 2), "limit_m": 30})
                legs.append(leg)
                clock += timedelta(seconds=leg["duration_s"])
        return Measurement(departure, legs, sum(dwell))

    @staticmethod
    def call_count(points, dwell):
        return 1 if not any(dwell) and len(points) <= 27 else len(points)-1

    def validate(self, points, dwell, earliest, latest, estimate_s, to_school, grace_seconds=0):
        if len(points) < 2 or len(dwell) != len(points) or any(x < 0 or not math.isfinite(x) for x in dwell):
            raise ValidationUnavailable("google_itinerary_invalid")
        if not math.isfinite(estimate_s) or estimate_s < 0:
            raise ValidationUnavailable("google_estimate_invalid")
        candidate = max(earliest, latest-timedelta(seconds=estimate_s)) if to_school else earliest
        visited, best, late_bound = set(), None, None
        calls = self.call_count(points, dwell)
        for _ in range(min(4, self.max_rounds)):
            self.client.check_canceled()
            if candidate in visited:
                break
            # Optional later departures cannot consume the remaining mandatory work
            # or the allowance for one corrective round. Each request still charges atomically.
            reserve = self.client.protected_calls + (calls if best is not None else 0)
            if not self.client.can_afford(calls, reserve):
                if best is not None:
                    return best
                raise ValidationUnavailable("google_budget_insufficient_for_complete_round")
            visited.add(candidate)
            previous_headroom = self.client.request_headroom
            self.client.request_headroom = reserve
            try:
                result = self.measure(points, candidate, dwell)
            except ValidationUnavailable:
                self.client.check_canceled()
                if best is not None:
                    return best
                raise
            finally:
                self.client.request_headroom = previous_headroom
            if not to_school:
                return result
            early_s = (latest-result.arrival).total_seconds()
            if early_s >= 0:
                if best is None or result.departure > best.departure:
                    best = result
                if early_s <= 180:
                    return best
                candidate = result.departure + timedelta(seconds=early_s-120)
            else:
                late_bound = candidate if late_bound is None else min(late_bound, candidate)
                if candidate == earliest:
                    return best or result
                candidate = max(earliest, result.departure+timedelta(seconds=early_s))
            if best is not None and late_bound is not None and not best.departure < candidate < late_bound:
                candidate = best.departure + (late_bound-best.departure)/2
        if best is not None:
            return best
        raise ValidationUnavailable("google_departure_validation_not_converged")


def session_for(config):
    session = getattr(config, "_google_validation_session", None)
    if session is None:
        require_available()
        client = GoogleRoutesClient(config.validation_budget_id,
                                    SqliteQuotaStore(Path(os.environ["BRP_GOOGLE_FINAL_QUOTA_DB"])))
        session = ValidationSession(client)
        config._google_validation_session = session
    return session


def attach_gate(planner, scenario, points, config, input_records, scenario_label, *,
                check_canceled=None, measurement_provider=None, grace_seconds=0):
    """Isolated adapter to the existing final-gate and native-leg contracts."""
    if measurement_provider is not None:
        raise ValidationUnavailable("google_cannot_use_amap_measurement_provider")
    if any(str(row.get("country", "")).upper() != "CHINA" for row in input_records):
        raise ValidationUnavailable("google_country_not_enabled")
    earliest, latest = service_window(config)
    session = session_for(config)
    if check_canceled is not None:
        session.client.check_canceled = check_canceled
    before = session.client.calls
    to_school = config.service_direction == "To School"
    arrival_anchored = to_school and config.timing_policy != "fixed_departure"
    grace_seconds = 0  # Google success never includes a late-arrival grace period.
    gate = {"enabled": True, "scenario": scenario_label, "provider": "google_routes",
            "gate_type": "arrival_window" if to_school else "route_duration",
            "service_direction": config.service_direction, "country": "China", "city": "",
            "traffic_policy": {"provider": "google_routes", "final_validation_enabled": True},
            "checked_route_count": 0, "failed_route_count": 0, "failed_route_ids": [],
            "unavailable_route_count": 0, "cache_hits": 0, "max_estimated_arrival_delay_minutes": 0,
            "max_time_window_overrun_minutes": 0, "target_duration_minutes": (latest-earliest).total_seconds()/60,
            "validation_service_date": config.validation_service_date, "policy_version": POLICY_VERSION,
            "timing_policy": config.timing_policy}
    updates = []
    pending_calls = sum(session.call_count(route.get("nodes") or [],
        [float(config.stop_service_minutes)*60 if i > 0 and int(node) != 0 else 0
         for i, node in enumerate(route.get("nodes") or [])]) for route in scenario.get("routes") or [])
    if not session.client.can_afford(pending_calls):
        raise ValidationUnavailable("google_budget_insufficient_for_scenario")
    session.client.protected_calls = pending_calls
    def minute(value):
        return (value-earliest.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()/60
    for index, route in enumerate(scenario.get("routes") or []):
        session.client.check_canceled()
        nodes = list(route.get("nodes") or [])
        from google_pickup_points import normalize_point, describe_failure
        request_points = [normalize_point(points[int(node)]) for node in nodes]
        coords = []
        for point in request_points:
            # Plot coordinates are normalized WGS84 by the existing input pipeline.
            if point.get("plot_lat") is None or point.get("plot_lng") is None:
                raise ValidationUnavailable("google_wgs84_coordinates_required")
            coords.append(location({"latLng": {"latitude": point["plot_lat"], "longitude": point["plot_lng"]}}))
        stop_dwell = float(config.stop_service_minutes)*60
        # Audit starts after origin boarding; only subsequent service stops add dwell.
        dwell = [stop_dwell if index > 0 and int(node) != 0 else 0 for index, node in enumerate(nodes)]
        saved_dwell = float(route.get("stop_service_time_s", sum(dwell)))
        session.client.protected_calls -= session.call_count(coords, dwell)
        if abs(saved_dwell-sum(dwell)) > 1:
            raise ValidationUnavailable("google_dwell_contract_mismatch")
        try:
            result = session.validate(coords, dwell, earliest, latest, float(route.get("time_s", 0)), arrival_anchored)
        except ValidationUnavailable as exc:
            describe_failure(exc, request_points, route_id=route.get("route_id") or route.get("id") or f"Bus {index+1}")
            raise

        overrun = max(0, (result.arrival-latest).total_seconds())
        passed = overrun <= grace_seconds
        route_id = str(route.get("route_id") or route.get("id") or f"Bus {index+1}")
        verification = {"status": "passed" if passed else "failed", "passes": passed,
            "provider": "google_routes", "verified_source": "google_routes", "route_id": route_id,
            "provider_departure_time": result.departure.isoformat(),
            "verified_drive_duration_s": result.drive_s, "verified_total_duration_s": result.drive_s+result.dwell_s,
            "verified_distance_m": sum(x["distance_m"] for x in result.legs),
            "verified_departure_minutes": minute(result.departure), "verified_arrival_minutes": minute(result.arrival),
            "verified_departure_label": result.departure.strftime("%H:%M"),
            "verified_arrival_label": result.arrival.strftime("%H:%M"),
            "time_window_overrun_minutes": overrun/60, "estimated_arrival_delay_minutes": overrun/60,
            "gate_type": gate["gate_type"], "validation_service_date": config.validation_service_date,
            "policy_version": POLICY_VERSION, "grace_minutes": 0, "timing_policy": config.timing_policy}
        evidence = {"provider": "google_routes", "source": "google_routes", "status": "verified",
                    "evidence_version": EVIDENCE_VERSION, "complete": True, "issues": [],
                    "geometry": result.legs[0]["geometry"] if len(result.legs) == 1 else [],
                    "legs": deepcopy(result.legs),
                    "leg_durations_s": [leg["duration_s"] for leg in result.legs],
                    "leg_distances_m": [leg["distance_m"] for leg in result.legs],
                    "duration_s": result.drive_s, "distance_m": verification["verified_distance_m"],
                    "geometry_segments": [x["geometry"] for x in result.legs],
                    "called_at": session.client.now().isoformat(), "departure_time": result.departure.isoformat(),
                    "policy_version": POLICY_VERSION, "requested_waypoints": deepcopy(request_points)}
        updates.append((route, verification, evidence, result))
        gate["checked_route_count"] += 1
        if not passed:
            gate["failed_route_count"] += 1
            gate["failed_route_ids"].append(route_id)
        gate["max_estimated_arrival_delay_minutes"] = max(gate["max_estimated_arrival_delay_minutes"], overrun/60)
    # Publish an entire scenario atomically, never half Google and half legacy.
    for route, verification, evidence, result in updates:
        for node, point in zip(route.get("nodes") or [], evidence["requested_waypoints"]):
            points[int(node)].update(point)
        route["final_route_traffic_gate"], route["route_evidence"] = verification, evidence
        route["traffic_time_source"] = "google_routes"
        if to_school:
            route["arrival_reverse_check"] = {**verification, "available": True,
                "before_earliest_departure": not verification["passes"],
                "scheduled_departure_minutes": verification["verified_departure_minutes"],
                "scheduled_arrival_minutes": verification["verified_arrival_minutes"],
                "required_departure_minutes": minute(latest-timedelta(seconds=result.drive_s+result.dwell_s))}
    gate["api_calls"] = session.client.calls-before
    gate["max_time_window_overrun_minutes"] = gate["max_estimated_arrival_delay_minutes"]
    gate["status"] = "failed" if gate["failed_route_count"] else "passed" if updates else "unavailable"
    scenario["traffic_gate"] = gate
    scenario["final_time_validation_mode"] = "google"
    return gate
