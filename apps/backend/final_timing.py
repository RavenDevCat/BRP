"""Task-scoped native timing shared by final decision adapters, never by map reads."""
from copy import deepcopy
from types import SimpleNamespace

import google_final_validation as google

CONFIG_FIELDS = ("final_time_validation_mode", "validation_service_date", "validation_budget_id",
                 "service_direction", "time_window_start", "time_window_end", "stop_service_minutes", "timing_policy")


def is_google(config):
    return google.validate_config(config or {}) == "google"


def require_china(country):
    if str(country or "").strip().upper() not in {"CHINA", "CN", "中国", "中华人民共和国"}:
        raise google.ValidationUnavailable("google_country_not_enabled")


def snapshot(config):
    return {key: deepcopy(config[key]) for key in CONFIG_FIELDS if key in config}


def prepare(config, *, service_date=None):
    return google.prepare_submission(dict(config or {}), service_date)


def review_context(config, store, record, token, check_active):
    context = FinalTimingContext({**config, "validation_budget_id": "review-"+record["review_id"]},
                                 check_canceled=check_active)
    client = context.session.client
    client.calls = int(record.get("api_calls") or 0)
    client.reserve_attempt = lambda: store.reserve_route_measurement_calls(
        record["review_id"], token, 1, enforce_limit=False)
    context.state["api_calls"] = client.calls
    return context


class FinalTimingContext:
    """One session shared across all candidates, baselines and removals in a run."""
    provider = "google_routes"

    def __init__(self, config, *, session=None, check_canceled=None):
        self.config = snapshot(config)
        if not is_google(self.config) or not self.config.get("validation_budget_id"):
            raise google.ValidationUnavailable("google_budget_identity_missing")
        self.earliest, self.latest = google.service_window(self.config)
        self.session = session or google.session_for(SimpleNamespace(**self.config))
        if check_canceled:
            self.session.client.check_canceled = check_canceled
        self.state = {"api_calls": 0, "cache_hits": 0}

    def route(self, points, *, reference_legs=None, dwell_s=None):
        self.state.pop("last_route_evidence", None)
        from google_pickup_points import describe_failure
        try:
            points = self.session.pickups.resolve_points(points)
        finally:
            self.state["pickup_resolution_api_calls"] = self.session.pickups.api_calls
            self.state["google_geocode_api_calls"] = self.session.pickups.api_calls
        coords = []
        for point in points:
            require_china(point.get("country", "China"))
            if point.get("plot_lat") is not None and point.get("plot_lng") is not None:
                lat, lng = point["plot_lat"], point["plot_lng"]
            elif str(point.get("coordinate_system", "")).upper() == "WGS84":
                lat, lng = point["lat"], point["lng"]
            elif str(point.get("coordinate_system", "")).upper() in {"GCJ02", "GCJ-02"} or point.get("provider") == "amap":
                from amap_driving import gcj02_to_wgs84
                lat, lng = gcj02_to_wgs84(float(point["lat"]), float(point["lng"]))
            else:
                raise google.ValidationUnavailable("google_wgs84_coordinates_required")
            coords.append(google.location({"latLng": {"latitude": lat, "longitude": lng}}))
        dwell = list(dwell_s) if dwell_s is not None else [0.0]*len(coords)
        estimate = sum(float(leg.get("duration_s") or 0) for leg in reference_legs or []) + sum(dwell)
        # Without a model estimate, start at the earliest allowed time, not at arrival.
        if not reference_legs:
            estimate = (self.latest-self.earliest).total_seconds()
        direction = str(self.config.get("service_direction", "To School")).lower().replace("_", " ")
        arrival_anchored = direction == "to school" and self.config.get("timing_policy") != "fixed_departure"
        try:
            measured = self.session.validate(coords, dwell, self.earliest, self.latest,
                                             estimate, arrival_anchored)
        except google.ValidationUnavailable as exc:
            describe_failure(exc, points)
            raise
        finally:
            self.state["api_calls"] = self.session.client.calls
            self.state["cache_hits"] = self.session.client.cache_hits
        legs = deepcopy(measured.legs)
        evidence = {"provider": self.provider, "source": self.provider, "status": "verified",
            "evidence_version": google.EVIDENCE_VERSION, "complete": True, "issues": [],
            "duration_s": measured.drive_s, "distance_m": sum(leg["distance_m"] for leg in legs),
            "legs": legs, "leg_durations_s": [leg["duration_s"] for leg in legs],
            "leg_distances_m": [leg["distance_m"] for leg in legs],
            "geometry": legs[0]["geometry"] if len(legs) == 1 else [],
            "geometry_segments": [leg["geometry"] for leg in legs],
            "called_at": min(leg.get("provider_called_at", self.session.client.now().isoformat()) for leg in legs),
            "departure_time": measured.departure.isoformat(), "arrival_time": measured.arrival.isoformat(),
            "departure_minutes": measured.departure.hour*60+measured.departure.minute+measured.departure.second/60,
            "arrival_minutes": (measured.arrival-self.earliest.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()/60,
            "stop_service_time_s": measured.dwell_s, "dwell_by_stop_s": dwell,
            "time_window_passes": measured.arrival <= self.latest,
            "policy_version": google.POLICY_VERSION, "validation_config": deepcopy(self.config),
            "requested_waypoints": deepcopy(points)}
        self.state["last_route_evidence"] = evidence
        return evidence


def insert_measurement(context, points, dwell):
    """Translate one native receipt to the established insertion contract."""
    evidence = context.route(points, dwell_s=dwell)
    return {"route_evidence": evidence, "provider_verified": True,
            "duration_s": evidence["duration_s"], "distance_m": evidence["distance_m"],
            "geometry": evidence["geometry"], "display_geometry": evidence["geometry"],
            "display_geometry_source": "google_routes", "warnings": [],
            "leg_durations_s": evidence["leg_durations_s"], "leg_distances_m": evidence["leg_distances_m"]}
