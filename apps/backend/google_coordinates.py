"""Explicit Google wire/display frames for empirically verified regional data.

Shanghai fixtures show native Geocoding and Routes coordinates in the same GCJ02
frame. Keep that frame on the wire; expose WGS84 only to maps and matrix code.
This policy must not be inferred from whichever coordinate gives a shorter route.
"""
from copy import deepcopy
import math

from amap_driving import gcj02_to_wgs84, wgs84_to_gcj02

WGS84 = "wgs84-v1"
SHANGHAI = "google-shanghai-gcj02-v1"
PROFILES = {WGS84, SHANGHAI}


def profile_for(country, city):
    china = str(country).strip().upper() in {"CHINA", "CN", "中国", "中华人民共和国"}
    return SHANGHAI if china and str(city).strip().lower() in {"shanghai", "上海", "上海市"} else WGS84


def wire_system(profile):
    if profile not in PROFILES:
        raise ValueError("unknown_google_coordinate_profile")
    return "GCJ02" if profile == SHANGHAI else "WGS84"


def profile_for_points(points):
    profiles = {point.get("google_coordinate_profile", WGS84) for point in points}
    if len(profiles) != 1 or not profiles.issubset(PROFILES):
        raise ValueError("mixed_or_unknown_google_coordinate_profiles")
    return profiles.pop()


def coordinate(value):
    lat, lng = map(float, value)
    if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat) > 90 or abs(lng) > 180:
        raise ValueError("invalid_coordinate")
    return lat, lng


def to_wgs84(value, profile):
    wire_system(profile)
    point = coordinate(value)
    return gcj02_to_wgs84(*point) if profile == SHANGHAI else point


def to_wire(value, profile):
    wire_system(profile)
    point = coordinate(value)
    return wgs84_to_gcj02(*point) if profile == SHANGHAI else point


def normalize_response(payload, profile):
    wire_system(profile)
    if profile == WGS84:
        return payload
    normalized = deepcopy(payload)
    for route in normalized.get("routes", []):
        for leg in route["legs"]:
            for field in ("startLocation", "endLocation"):
                loc = leg[field]["latLng"]
                lat, lng = to_wgs84((loc["latitude"], loc["longitude"]), profile)
                loc.update(latitude=lat, longitude=lng)
            line = leg["polyline"]["geoJsonLinestring"]
            line["coordinates"] = [list(to_wgs84((lat, lng), profile)[::-1])
                                   for lng, lat in line["coordinates"]]
    return normalized
