"""Resolve provider-supplied school entrances without editing geocode caches."""
from copy import deepcopy
import math


def normalize_point(point):
    result = deepcopy(point)
    if (point.get("provider") != "amap" or not point.get("amap_poi_id")
            or point.get("pickup_override_revision")
            or point.get("pickup_precision_status") == "operator_confirmed"
            or point.get("pickup_entrance_source")):
        return result
    from amap_geocode_quality import _gate_tokens
    if _gate_tokens(str(point.get("requested_address") or point.get("address") or "")):
        return result
    # Residential entrances are already resolved by the shared geocoder. Schools
    # used to keep their campus centroid despite carrying an explicit entrance.
    if "\u5b66\u6821" not in str(point.get("amap_poi_type") or ""):
        return result
    try:
        lng, lat = map(float, str(point.get("amap_poi_entr_location") or "").split(","))
        center_lng, center_lat = map(float, str(point.get("amap_poi_location") or "").split(","))
        if not all(math.isfinite(v) for v in (lat, lng, center_lat, center_lng)):
            return result
        if abs(lat) > 90 or abs(lng) > 180 or abs(center_lat) > 90 or abs(center_lng) > 180:
            return result
        from google_final_validation import meters
        if meters((lat, lng), (center_lat, center_lng)) > 1000:
            return result
    except (TypeError, ValueError):
        return result
    from amap_driving import gcj02_to_wgs84
    plot_lat, plot_lng = gcj02_to_wgs84(lat, lng)
    result.update(lat=lat, lng=lng, plot_lat=plot_lat, plot_lng=plot_lng,
                  coordinate_system="GCJ02", pickup_entrance_source="provider_entr_location")
    return result


def _address(point):
    return str(point.get("requested_address") or point.get("address") or "")


def _match_address(point):
    from amap_geocode_quality import _gate_tokens
    address = _address(point)
    # Generic doorstep wording is not a landmark name; specific gates stay intact.
    return address.removesuffix("\u95e8\u53e3") if not _gate_tokens(address) else address


def _confirmed(point):
    return point.get("pickup_precision_status") == "operator_confirmed" and bool(point.get("pickup_override_revision"))


def _identity_issues(point):
    from amap_geocode_quality import annotate_amap_pickup, _gate_tokens
    if point.get("provider") != "amap" or _confirmed(point):
        return []
    # Do not reinterpret plain coordinates or turn every legacy warning into a block.
    has_identity = any(point.get(key) for key in ("formatted_address", "geocode_quality_version",
        "pickup_precision_issues", "pickup_resolution_status")) or _gate_tokens(_address(point))
    if not has_identity:
        return []
    checked = annotate_amap_pickup(point, _match_address(point))
    issues = [value for value in checked.get("pickup_precision_issues", [])
              if value != "coarse_or_unknown_geocode_precision"]
    if point.get("pickup_resolution_status") == "reference_only" and not issues:
        issues.append("reference_only")
    return issues


def _lookup(point, check_canceled, counted):
    from amap_geocode_quality import resolve_amap_pickup, GeocodePrecisionError
    import client_runtime as runtime
    from google_final_validation import ValidationUnavailable
    country, city = point.get("country", "China"), point.get("city", "")
    city_code = runtime._amap_city_param(country, city)
    if not city_code:
        raise GeocodePrecisionError("Known city required for pickup resolution")
    failures = []
    canceled = []
    attempts = 0
    def request(endpoint, params, limiter):
        nonlocal attempts
        params = {**params, ("keywords" if endpoint == "/v3/place/text" else "address"): _address(point)}
        # The existing resolver makes at most one POI and one geocode query.
        try:
            check_canceled()
        except Exception as exc:
            canceled.append(exc)
            raise
        if canceled:
            raise canceled[0]
        if attempts >= 2:
            raise ValidationUnavailable("google_pickup_lookup_unavailable")
        attempts += 1
        counted()
        try:
            response = runtime.amap_request_json(endpoint, params, limiter)
            if endpoint == "/v3/place/text":
                from amap_geocode_quality import _compact, _gate_tokens
                address = _match_address(point)
                # An exact compound address outranks units inside that compound;
                # otherwise retain all candidates so ambiguity is not hidden.
                exact = [p for p in response.get("pois", [])
                         if _compact(p.get("address")) == _compact(address)]
                if exact and not _gate_tokens(address):
                    response = {**response, "pois": exact}
            return response
        except Exception as exc:
            failures.append(type(exc).__name__)
            raise
    try:
        result = resolve_amap_pickup(request_json=request, country=country, city=city,
            address=_match_address(point), city_code=city_code,
            geocode_limiter=runtime.AMAP_GEOCODE_LIMITER, poi_limiter=runtime.AMAP_PLACES_LIMITER,
            plausible=runtime.is_plausible_geocode_result, to_wgs84=runtime.gcj02_to_wgs84)
    except GeocodePrecisionError:
        if canceled:
            raise canceled[0]
        if failures:
            raise ValidationUnavailable("google_pickup_lookup_unavailable") from None
        raise
    if canceled:
        raise canceled[0]
    if failures and _identity_issues(result):
        raise ValidationUnavailable("google_pickup_lookup_unavailable")
    return result


class PickupResolver:
    """Task-local service-point resolution; never writes shared geocode caches."""
    def __init__(self, check_canceled=lambda: None, lookup=None):
        self.check_canceled = check_canceled
        self.lookup = lookup or _lookup
        self.cache = {}
        self.api_calls = 0

    def _count(self):
        self.api_calls += 1

    def resolve(self, point):
        import json
        from amap_geocode_quality import GeocodePrecisionError, GEOCODE_PROVENANCE_FIELDS
        from google_final_validation import ValidationUnavailable
        self.check_canceled()
        original = normalize_point(point)
        issues = _identity_issues(original)
        if not issues:
            return original
        fields = ("country", "city", "provider", "lat", "lng", "plot_lat", "plot_lng",
                  "formatted_address", *GEOCODE_PROVENANCE_FIELDS)
        key = json.dumps([_address(original), {k:original.get(k) for k in fields}], sort_keys=True)
        if key not in self.cache:
            try:
                candidate = self.lookup(original, self.check_canceled, self._count)
                merged = {**original, **candidate}
                remaining = _identity_issues(merged)
                if remaining or candidate.get("pickup_resolution_status") == "reference_only":
                    raise GeocodePrecisionError("Pickup identity remains unconfirmed")
                # Preserve row ownership and service counts; copy only location provenance.
                location_fields = (*GEOCODE_PROVENANCE_FIELDS, "lat", "lng", "plot_lat", "plot_lng",
                    "provider", "coordinate_system", "formatted_address", "geocode_status", "warning")
                changes = {k:candidate[k] for k in location_fields if k in candidate}
                self.cache[key] = {"changes": changes}
            except GeocodePrecisionError:
                self.cache[key] = {"issues": issues}
        saved = self.cache[key]
        if "issues" in saved:
            raise ValidationUnavailable("google_pickup_identity_unresolved", details={
                "address": _address(original), "identity_issues": saved["issues"],
                "requested_coordinate": [original.get("plot_lat"), original.get("plot_lng")]})
        result = {**original, **deepcopy(saved["changes"])}
        result["pickup_resolution_evidence"] = {
            "policy": "google-service-point-v1", "source": "targeted_amap_resolution",
            "original_coordinate": [original.get("plot_lat"), original.get("plot_lng")],
            "original_issues": issues}
        return normalize_point(result)

    def resolve_points(self, points):
        from google_final_validation import ValidationUnavailable
        from amap_geocode_quality import _gate_tokens, _without_gates, _compact
        resolved = []
        for index, point in enumerate(points):
            try:
                resolved.append(self.resolve(point))
            except ValidationUnavailable as exc:
                exc.details["point_index"] = index
                raise
        points = resolved
        # Detect conflicting gate identities before either can be mistaken for a zero leg.
        seen = {}
        for index, point in enumerate(points):
            gates = _gate_tokens(_address(point))
            coordinate = (point.get("plot_lat", point.get("lat")), point.get("plot_lng", point.get("lng")))
            identity = _compact(_without_gates(_address(point)))
            key = (identity, coordinate)
            if index and None not in coordinate:
                prior = points[index-1]
                prior_coordinate = (prior.get("plot_lat", prior.get("lat")), prior.get("plot_lng", prior.get("lng")))
                same_name = bool(_address(point)) and _compact(_address(point)) == _compact(_address(prior))
                same_poi = bool(point.get("amap_poi_id")) and point.get("amap_poi_id") == prior.get("amap_poi_id")
                gate_conflict = bool(gates and _gate_tokens(_address(prior)) and gates != _gate_tokens(_address(prior)))
                confirmed_same_place = _confirmed(point) and _confirmed(prior)
                if coordinate == prior_coordinate and (gate_conflict or not (same_name or same_poi or confirmed_same_place)):
                    raise ValidationUnavailable("google_pickup_identity_conflict", details={
                        "point_index": index, "other_point_index": index-1,
                        "address": _address(point), "other_address": _address(prior),
                        "requested_coordinate": list(coordinate)})
            previous = seen.get(key)
            if gates and previous and previous[1] != gates and None not in coordinate:
                raise ValidationUnavailable("google_pickup_identity_conflict", details={
                    "point_index": index, "other_point_index": previous[0],
                    "address": _address(point), "other_address": _address(points[previous[0]]),
                    "requested_coordinate": list(coordinate)})
            if gates:
                seen[key] = (index, gates)
        return resolved


def describe_failure(exc, points, *, route_id=None):
    details = dict(getattr(exc, "details", {}) or {})
    details.setdefault("code", str(exc))
    index = details.get("point_index")
    if isinstance(index, int) and 0 <= index < len(points):
        point = points[index]
        details.update(address=point.get("address"), is_school=bool(point.get("is_depot")),
                       stop_sequence=point.get("stop_sequence"),
                       pickup_entrance_source=point.get("pickup_entrance_source"))
    if route_id is not None:
        details["route_id"] = str(route_id)
    exc.details = details
