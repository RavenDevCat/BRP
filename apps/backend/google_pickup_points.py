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
