"""Shared pickup precision and matching; network access is dependency-injected."""
from __future__ import annotations

import re
import math
from typing import Any

GEOCODE_QUALITY_VERSION = "amap-pickup-review-v2"
GEOCODE_PROVENANCE_FIELDS = (
    "geocode_quality_version", "geocode_level", "adcode", "amap_poi_id",
    "amap_poi_name", "amap_poi_address", "amap_poi_type",
    "pickup_precision_status", "pickup_precision_issues",
)
PRECISE_LEVELS = {"\u95e8\u724c\u53f7", "\u5174\u8da3\u70b9", "\u9053\u8def\u4ea4\u53c9\u53e3", "poi"}
PRECISE_LEVELS.update({"\u95e8\u5740", "\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9", "\u9053\u8def\u4ea4\u53c9\u8def\u53e3"})


class GeocodePrecisionError(RuntimeError):
    pass


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())


def _local_address(value: str) -> str:
    text = value
    for _ in range(3):
        prefix = re.match(r"^[\u4e00-\u9fff]{2,8}?[\u7701\u5e02\u533a\u53bf]", text)
        if not prefix or re.search(r"[\u8def\u8857\u9053\u5df7]|\u5c0f\u533a|\u793e\u533a", prefix.group()):
            break
        text = text[prefix.end():]
    return re.sub(r"\u516c\u4ea4\d+\u8def", "", text)


def _road_tokens(value: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,12}?(?:\u5927\u9053|\u5927\u8857|\u516c\u8def|\u80e1\u540c|\u8def|\u8857|\u9053|\u5df7)", _local_address(value))


def amap_candidate_issues(requested: str, candidate: dict[str, Any], *, poi: bool = False) -> list[str]:
    level = "poi" if poi else str(candidate.get("geocode_level") or candidate.get("level") or "")
    issues = []
    if level not in PRECISE_LEVELS:
        issues.append("coarse_or_unknown_geocode_precision")
    name = str(candidate.get("amap_poi_name") or candidate.get("name") or "")
    formatted = str(candidate.get("formatted_address") or "")
    address = str(candidate.get("amap_poi_address") or (candidate.get("address") if not candidate.get("provider") else "") or "") if poi else ""
    text = _compact(name + formatted + address)
    roads = _road_tokens(requested)
    if any(_compact(road) not in text for road in roads):
        issues.append("requested_road_not_preserved")
    requested_compact = _compact(requested)
    is_bus_stop = bool(re.search(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0", requested_compact))
    if poi and is_bus_stop and any(_compact(road) not in _compact(name) for road in roads):
        issues.append("requested_bus_stop_name_not_preserved")
    if is_bus_stop and not re.search(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0", text + str(candidate.get("type") or candidate.get("amap_poi_type") or "")):
        issues.append("requested_bus_stop_not_preserved")
    if not is_bus_stop:
        for number in re.findall(r"\d+(?:\u53f7|\u5f04)", requested_compact):
            if number not in text:
                issues.append("requested_building_number_not_preserved")
                break
    entrances = re.findall(r"(?:\u4e1c\u5357|\u4e1c\u5317|\u897f\u5357|\u897f\u5317|\u4e1c|\u897f|\u5357|\u5317|\u6b63|\u4fa7|\u540e)(?:\u95e8|\u5165\u53e3|\u51fa\u53e3)", requested_compact)
    if any(entrance not in text for entrance in entrances):
        issues.append("requested_entrance_not_preserved")
    if poi:
        poi_type = str(candidate.get("amap_poi_type") or candidate.get("type") or "")
        if "\u505c\u8f66\u573a" in name + poi_type and "\u505c\u8f66" not in requested_compact:
            issues.append("parking_poi_is_not_requested_pickup")
    if poi or not re.search(r"\d+(?:\u53f7|\u5f04)", requested_compact):
        # Match the full named landmark, not a generic three-character overlap
        # or a tenant's parenthetical shopping-centre address.
        residual = _local_address(re.split(r"[\uff08(]", requested)[0])
        for road in roads:
            residual = residual.replace(road, "")
        residual = re.sub(r"[\uff08(].*?[\uff09)]|\d+(?:\u53f7|\u5f04)|\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0|\u4ea4\u53c9\u8def?\u53e3|\u4ea4\u6c47\u5904", "", residual)
        residual = _compact(residual)
        anchor = _compact(re.split(r"[\uff08(]", name)[0]) if poi else text
        if not is_bus_stop and len(residual) >= 2 and residual not in anchor:
            issues.append("requested_landmark_not_preserved")
        if poi and not is_bus_stop:
            for branch in re.findall(r"[\uff08(]([^\uff09)]+)", requested):
                if _compact(branch) not in text:
                    issues.append("requested_branch_not_preserved")
    return list(dict.fromkeys(issues))


def select_amap_pickup_candidate(requested: str, candidates: list[dict[str, Any]], *, poi: bool = False) -> dict[str, Any] | None:
    accepted = [candidate for candidate in candidates if not amap_candidate_issues(requested, candidate, poi=poi)]
    unique = {}
    for candidate in accepted:
        key = str(candidate.get("id") or candidate.get("amap_poi_id") or
                  (candidate.get("name") or candidate.get("amap_poi_name"),
                   candidate.get("location") or (candidate.get("lat"), candidate.get("lng"))))
        unique.setdefault(key, candidate)
    if len(unique) > 1:
        raise GeocodePrecisionError("Multiple pickup locations match this address. Specify the stop, building number or entrance; no candidate was selected automatically.")
    return next(iter(unique.values()), None)


def reusable_amap_geocode(point: dict[str, Any], requested: str) -> bool:
    if str(point.get("provider") or "").lower() != "amap":
        return True
    # Cache schema age and pickup precision are not coordinate-resolution failures.
    # City/address plausibility is checked by both callers before reuse.
    try:
        lat, lng = float(point["lat"]), float(point["lng"])
    except (KeyError, TypeError, ValueError):
        return False
    return (point.get("cache_status") != "failed" and math.isfinite(lat) and math.isfinite(lng)
            and abs(lat) <= 90 and abs(lng) <= 180 and (lat, lng) != (0, 0))


def annotate_amap_pickup(point: dict[str, Any], requested: str, *, ambiguous: bool = False) -> dict[str, Any]:
    result = dict(point)
    if str(result.get("provider") or "").lower() != "amap":
        return result
    issues = amap_candidate_issues(requested, result, poi=result.get("geocode_level") == "poi")
    if ambiguous:
        issues.append("multiple_provider_candidates")
    result.update({"geocode_quality_version": GEOCODE_QUALITY_VERSION,
                   "pickup_precision_status": "needs_review" if issues else "matched",
                   "pickup_precision_issues": list(dict.fromkeys(issues))})
    if issues:
        result["geocode_status"] = "needs_review"
        result["warning"] = "Coordinates are resolved; check the pickup entrance or road side. The saved location has not been moved."
    return result


def require_amap_pickup_precision(points: list[dict[str, Any]]) -> None:
    unresolved = [index for index, point in enumerate(points)
                  if not reusable_amap_geocode(point, str(point.get("requested_address") or point.get("address") or ""))]
    if unresolved:
        raise GeocodePrecisionError(
            "Pickup coordinates are unavailable at route point index(es): "
            + ", ".join(map(str, unresolved))
            + ". Re-prepare these addresses; no stop was skipped."
        )


def resolve_amap_pickup(*, request_json, country: str, city: str, address: str,
                        city_code: str, geocode_limiter, poi_limiter,
                        plausible, to_wgs84) -> dict[str, Any]:
    """Resolve coordinates; keep pickup precision separate from geocoding success."""
    def convert(candidate: dict[str, Any], poi: bool) -> dict[str, Any] | None:
        try:
            lng, lat = map(float, str(candidate["location"]).split(","))
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(lat) and math.isfinite(lng) and abs(lat) <= 90 and abs(lng) <= 180):
            return None
        name = str(candidate.get("name") or "").strip() if poi else ""
        actual_address = str(candidate.get("address") or "").strip() if poi else ""
        formatted = str(candidate.get("formatted_address") or "").strip()
        if poi:
            parts = [str(candidate.get(k) or "").strip() for k in ("pname", "cityname", "adname")]
            formatted = "".join(dict.fromkeys([*parts, actual_address, name]))
        adcode = str(candidate.get("adcode") or "").strip()
        if not plausible(country, city, lat, lng, formatted, adcode, requested_address=address):
            return None
        plot_lat, plot_lng = to_wgs84(lat, lng)
        return {"provider": "amap", "country": country.strip(), "city": city.strip(),
                "address": address.strip(), "lat": lat, "lng": lng,
                "plot_lat": plot_lat, "plot_lng": plot_lng,
                "formatted_address": formatted, "adcode": adcode,
                "geocode_level": "poi" if poi else str(candidate.get("level") or ""),
                "amap_poi_id": str(candidate.get("id") or "") if poi else "",
                "amap_poi_name": name, "amap_poi_address": actual_address,
                "amap_poi_type": str(candidate.get("type") or "") if poi else "",
                "geocode_quality_version": GEOCODE_QUALITY_VERSION}

    # Keep the provider's structured address location, even when its entrance
    # needs review. POI search is only a fallback for truly unresolved addresses.
    for poi in (False, True):
        params = ({"keywords": address.strip(), "citylimit": "true", "offset": 10, "page": 1}
                  if poi else {"address": address.strip()})
        if city_code:
            params["city"] = city_code
        elif poi:
            raise GeocodePrecisionError("A known city is required to disambiguate pickup POIs.")
        try:
            response = request_json("/v3/place/text" if poi else "/v3/geocode/geo", params,
                                    poi_limiter if poi else geocode_limiter)
        except Exception:
            continue
        candidates = [point for raw in response.get("pois" if poi else "geocodes") or []
                      if (point := convert(dict(raw), poi)) is not None]
        if not poi and candidates:
            return annotate_amap_pickup(candidates[0], address, ambiguous=len(candidates) > 1)
        try:
            chosen = select_amap_pickup_candidate(address, candidates, poi=poi)
        except GeocodePrecisionError:
            # Same-name bus stops may represent opposite road sides. Preserve
            # provider ranking and expose ambiguity, never choose by route length.
            chosen = next(candidate for candidate in candidates if not amap_candidate_issues(address, candidate, poi=poi))
            return annotate_amap_pickup(chosen, address, ambiguous=True)
        if chosen is not None:
            return annotate_amap_pickup(chosen, address)
    raise GeocodePrecisionError("No unique, precise pickup matches this address. Road/area centroids, unrelated POIs and unrequested parking locations are not accepted; specify the stop, building number or entrance.")
