"""Shared pickup precision and matching; network access is dependency-injected."""
from __future__ import annotations

import re
import math
import unicodedata
from typing import Any

GEOCODE_QUALITY_VERSION = "amap-pickup-review-v6"
PICKUP_REVIEW_WARNING = "Coordinates are resolved; check the pickup entrance or road side. The saved location has not been moved."
GEOCODE_PROVENANCE_FIELDS = (
    "geocode_quality_version", "geocode_level", "adcode", "amap_poi_id",
    "amap_poi_name", "amap_poi_address", "amap_poi_type",
    "pickup_precision_status", "pickup_precision_issues",
    "pickup_override_revision", "pickup_override_confirmed_at",
    "pickup_entrance_source", "amap_poi_location", "amap_poi_entr_location",
    "pickup_resolution_status",
    "amap_parent_poi_id",
)
PRECISE_LEVELS = {"\u95e8\u724c\u53f7", "\u5174\u8da3\u70b9", "\u9053\u8def\u4ea4\u53c9\u53e3", "poi"}
PRECISE_LEVELS.update({"\u95e8\u5740", "\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9", "\u9053\u8def\u4ea4\u53c9\u8def\u53e3"})


class GeocodePrecisionError(RuntimeError):
    pass


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", str(value or "")).lower())


GATE_PATTERN = re.compile(
    r"(?:\u4e1c\u5357|\u4e1c\u5317|\u897f\u5357|\u897f\u5317|\u4e1c|\u897f|\u5357|\u5317|\u6b63|\u4fa7|\u540e|"
    r"\u7b2c?[0-9\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+\u53f7?|[a-z])"
    r"(?:\u5927\u95e8|\u95e8|\u5165\u53e3|\u51fa\u53e3|\u53e3)(?:\u53e3)?(?=$|[\s(),;/\u3001\uff0c\uff1b]|\u5916)"
)
RESIDENTIAL_PATTERN = re.compile(r"\u5c0f\u533a|\u4f4f\u5b85|\u516c\u5bd3|\u82d1|\u82b1\u56ed|\u522b\u5885|\d+\u5f04")


def _gate_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def _gate_tokens(value: str) -> set[str]:
    tokens = set()
    digits = "\u96f6\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d"
    for match in GATE_PATTERN.finditer(_gate_text(value)):
        token = match.group().removeprefix("\u7b2c")
        number = re.match(r"[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+", token)
        if number:
            raw = number.group()
            if len(raw) == 1 and raw != "\u5341":
                canonical = str(digits.index(raw))
            elif re.fullmatch(r"[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d]?\u5341[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d]?", raw):
                tens, units = raw.split("\u5341")
                canonical = str((digits.index(tens) if tens else 1) * 10 + (digits.index(units) if units else 0))
            else:
                canonical = raw
            token = canonical + token[number.end():]
        token = token.replace("\u53f7", "").replace("\u5927\u95e8", "\u95e8").replace("\u95e8\u53e3", "\u95e8").replace("\u5165\u53e3", "\u95e8").replace("\u51fa\u53e3", "\u95e8")
        tokens.add(re.sub(r"\u53e3$", "\u95e8", token))
    return tokens


def _without_gates(value: str) -> str:
    return GATE_PATTERN.sub("", _gate_text(value))


def _residential_pickup(requested: str, candidate: dict[str, Any]) -> bool:
    identity = _without_gates(requested) + " " + str(candidate.get("amap_poi_name") or candidate.get("name") or candidate.get("formatted_address") or "")
    for road in _road_tokens(identity):
        identity = identity.replace(road, "")
    poi_type = str(candidate.get("amap_poi_type") or candidate.get("type") or "")
    level = str(candidate.get("geocode_level") or candidate.get("level") or "")
    return bool(RESIDENTIAL_PATTERN.search(identity) or any(word in poi_type + level + identity
                for word in ("\u4f4f\u5b85\u533a", "\u5546\u52a1\u4f4f\u5b85", "\u697c\u5b87", "\u5927\u53a6", "\u5927\u697c")))


def _named_gate_poi(candidate: dict[str, Any]) -> bool:
    name = str(candidate.get("amap_poi_name") or candidate.get("name") or "")
    poi_type = str(candidate.get("amap_poi_type") or candidate.get("type") or "")
    return bool(_gate_tokens(name) or "\u51fa\u5165\u53e3" in poi_type)


def _local_address(value: str) -> str:
    text = value
    for _ in range(3):
        prefix = re.match(r"^[\u4e00-\u9fff]{2,8}?[\u7701\u5e02\u533a\u53bf]", text)
        if not prefix or re.search(r"[\u8def\u8857\u9053\u5df7\u82d1\u56ed]|\u5c0f\u533a|\u793e\u533a|\u6821\u533a|[\u4e1c\u897f\u5357\u5317]\u533a", prefix.group()):
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
    if poi and is_bus_stop and len(roads) >= 2:
        positions = [_compact(name).find(_compact(road)) for road in roads]
        if all(position >= 0 for position in positions) and positions != sorted(positions):
            issues.append("requested_bus_stop_road_order_not_preserved")
    if is_bus_stop and not re.search(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0", text + str(candidate.get("type") or candidate.get("amap_poi_type") or "")):
        issues.append("requested_bus_stop_not_preserved")
    if not is_bus_stop:
        for number in re.findall(r"\d+(?:\u53f7|\u5f04)", _without_gates(requested)):
            if not re.search(r"(?<!\d)" + re.escape(number), text):
                issues.append("requested_building_number_not_preserved")
                break
    entrances = _gate_tokens(requested)
    if not entrances.issubset(_gate_tokens(name if poi else formatted)):
        issues.append("requested_entrance_not_preserved")
    if entrances and (not poi or not _named_gate_poi(candidate)):
        issues.append("pickup_entrance_unconfirmed")
    if not is_bus_stop and _residential_pickup(requested, candidate):
        entrance_source = candidate.get("pickup_entrance_source")
        if not (poi and _named_gate_poi(candidate)) and entrance_source != "provider_entr_location":
            issues.append("residential_entrance_unconfirmed")
    if poi:
        poi_type = str(candidate.get("amap_poi_type") or candidate.get("type") or "")
        if "\u505c\u8f66\u573a" in name + poi_type and "\u505c\u8f66" not in requested_compact:
            issues.append("parking_poi_is_not_requested_pickup")
    if poi or not re.search(r"\d+(?:\u53f7|\u5f04)", requested_compact):
        # Match the full named landmark, not a generic three-character overlap
        # or a tenant's parenthetical shopping-centre address.
        residual = _local_address(re.split(r"[\uff08(]", _without_gates(requested))[0])
        for road in roads:
            residual = residual.replace(road, "")
        residual = re.sub(r"[\uff08(].*?[\uff09)]|\d+(?:\u53f7|\u5f04)|\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0|\u4ea4\u53c9\u8def?\u53e3|\u4ea4\u6c47\u5904", "", residual)
        residual = _compact(residual)
        anchor = _compact(re.split(r"[\uff08(]", name)[0]) if poi else text
        if not is_bus_stop and len(residual) >= 2 and residual not in anchor:
            issues.append("requested_landmark_not_preserved")
        if poi and not is_bus_stop:
            for branch in re.findall(r"[\uff08(]([^\uff09)]+)", requested):
                if _compact(_without_gates(branch)) not in text:
                    issues.append("requested_branch_not_preserved")
    return list(dict.fromkeys(issues))


def select_amap_pickup_candidate(requested: str, candidates: list[dict[str, Any]], *, poi: bool = False) -> dict[str, Any] | None:
    accepted = [candidate for candidate in candidates if not amap_candidate_issues(requested, candidate, poi=poi)]
    if poi:
        # A numbered compound's own address is stronger evidence than a POI
        # whose name mentions it but whose address is merely a nearby junction.
        numbers = re.findall(r"\d+(?:\u53f7|\u5f04)", _without_gates(requested))
        exact_sites = [candidate for candidate in accepted if numbers and _residential_pickup("", candidate)
                       and candidate.get("pickup_entrance_source") == "provider_entr_location"
                       and all(re.search(r"(?<!\d)" + re.escape(number),
                                         str(candidate.get("amap_poi_address") or candidate.get("address") or ""))
                               for number in numbers)]
        if exact_sites:
            accepted = exact_sites
    unique = {}
    for candidate in accepted:
        identity = str(candidate.get("id") or candidate.get("amap_poi_id") or
                  (candidate.get("name") or candidate.get("amap_poi_name"),
                   candidate.get("location") or (candidate.get("lat"), candidate.get("lng")))).strip()
        key = (identity, candidate.get("lat"), candidate.get("lng"),
               str(candidate.get("location") or "").strip())
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
    if result.get("pickup_precision_status") == "operator_confirmed" and result.get("pickup_override_revision"):
        return result
    issues = amap_candidate_issues(requested, result, poi=result.get("geocode_level") == "poi")
    if ambiguous or "multiple_provider_candidates" in (result.get("pickup_precision_issues") or []):
        issues.append("multiple_provider_candidates")
    result.update({"geocode_quality_version": GEOCODE_QUALITY_VERSION,
                   "pickup_precision_status": "needs_review" if issues else "matched",
                   "pickup_precision_issues": list(dict.fromkeys(issues))})
    if issues:
        result["geocode_status"] = "needs_review"
        result["warning"] = PICKUP_REVIEW_WARNING
    elif result.get("warning") == PICKUP_REVIEW_WARNING:
        result["warning"] = ""
        if result.get("geocode_status") == "needs_review":
            result["geocode_status"] = "ok"
        if result.get("pickup_resolution_status") == "reference_only":
            result["pickup_resolution_status"] = "matched"
    return result


def require_amap_pickup_precision(points: list[dict[str, Any]]) -> None:
    reference_only = [index for index, point in enumerate(points)
                      if point.get("pickup_resolution_status") == "reference_only"
                      and point.get("pickup_precision_status") != "operator_confirmed"]
    if reference_only:
        raise GeocodePrecisionError(
            "Pickup identity or entrance requires confirmation at route point index(es): "
            + ", ".join(map(str, reference_only))
            + ". The visible coordinate is a reference, not a confirmed service stop; no stop was skipped."
        )
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
    def resolved(point: dict[str, Any], *, ambiguous: bool = False) -> dict[str, Any]:
        result = annotate_amap_pickup(point, address, ambiguous=ambiguous)
        result["pickup_resolution_status"] = "reference_only" if result["pickup_precision_issues"] else "matched"
        return result

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
        entrance_source = ""
        entrance_location = str(candidate.get("entr_location") or "") if poi else ""
        if poi and _named_gate_poi(candidate):
            entrance_source = "named_gate_poi"
        elif poi and not _gate_tokens(address) and _residential_pickup("", candidate):
            try:
                entrance_lng, entrance_lat = map(float, entrance_location.split(","))
            except (TypeError, ValueError):
                pass
            else:
                if (math.isfinite(entrance_lat) and math.isfinite(entrance_lng)
                        and abs(entrance_lat) <= 90 and abs(entrance_lng) <= 180
                        and plausible(country, city, entrance_lat, entrance_lng, formatted, adcode, requested_address=address)):
                    lat, lng = entrance_lat, entrance_lng
                    entrance_source = "provider_entr_location"
        plot_lat, plot_lng = to_wgs84(lat, lng)
        return {"provider": "amap", "country": country.strip(), "city": city.strip(),
                "address": address.strip(), "lat": lat, "lng": lng,
                "plot_lat": plot_lat, "plot_lng": plot_lng,
                "formatted_address": formatted, "adcode": adcode,
                "geocode_level": "poi" if poi else str(candidate.get("level") or ""),
                "amap_poi_id": str(candidate.get("id") or "").strip() if poi else "",
                "amap_parent_poi_id": str(candidate.get("parent_id") or "").strip() if poi else "",
                "amap_poi_name": name, "amap_poi_address": actual_address,
                "amap_poi_type": str(candidate.get("type") or "") if poi else "",
                "amap_poi_location": str(candidate.get("location") or "") if poi else "",
                "amap_poi_entr_location": entrance_location,
                "pickup_entrance_source": entrance_source,
                "geocode_quality_version": GEOCODE_QUALITY_VERSION}

    # Named bus stops need their station identity, not an intersection geocode.
    # Preserve a usable geocode if POI lookup fails or leaves road-side ambiguity.
    named_bus_stop = bool(re.search(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0", _compact(address)))
    entrance_required = bool(_gate_tokens(address) or _residential_pickup(address, {}))
    prefer_poi = (named_bus_stop or entrance_required) and bool(city_code)
    ambiguous_poi: dict[str, Any] | None = None
    fallback_geocode: dict[str, Any] | None = None
    fallback_poi: dict[str, Any] | None = None
    for poi in ((True, False) if prefer_poi else (False, True)):
        params = ({"keywords": address.strip(), "citylimit": "true", "offset": 10, "page": 1, "extensions": "all"}
                  if poi else {"address": address.strip()})
        if poi and _gate_tokens(address):
            params["children"] = 1
        if city_code:
            params["city"] = city_code
        elif poi:
            if fallback_geocode is not None:
                break
            raise GeocodePrecisionError("A known city is required to disambiguate pickup POIs.")
        try:
            response = request_json("/v3/place/text" if poi else "/v3/geocode/geo", params,
                                    poi_limiter if poi else geocode_limiter)
        except Exception:
            continue
        raw_candidates = list(response.get("pois" if poi else "geocodes") or [])
        if poi and _gate_tokens(address):
            # Gates can be subordinate POIs. Only the child's own coordinate is usable.
            for parent in list(raw_candidates):
                for child in parent.get("children") or []:
                    if not isinstance(child, dict) or not _named_gate_poi(child):
                        continue
                    name = str(child.get("name") or "")
                    parent_name = str(parent.get("name") or "")
                    if _compact(parent_name) not in _compact(name):
                        name = parent_name + "(" + name + ")"
                    raw_candidates.append({**{key: parent[key] for key in ("pname", "cityname", "adname", "adcode", "address") if key in parent},
                                           **child, "name": name, "parent_id": str(parent.get("id") or "")})
        candidates = [point for raw in raw_candidates
                      if (point := convert(dict(raw), poi)) is not None]
        if not poi and candidates:
            fallback_geocode = resolved(candidates[0], ambiguous=len(candidates) > 1 or ambiguous_poi is not None)
            entrance_required = entrance_required or _residential_pickup(address, candidates[0])
            # A POI-level coordinate is not evidence of a numbered address's doorway.
            entrance_required = entrance_required or (str(candidates[0].get("geocode_level")) == "\u5174\u8da3\u70b9"
                and bool(re.search(r"\d+(?:\u53f7|\u5f04)", _without_gates(address))))
            if not entrance_required and fallback_geocode["pickup_resolution_status"] == "matched":
                return fallback_geocode
            continue
        eligible = candidates
        if poi and re.search(r"\d+(?:\u53f7|\u5f04)", _without_gates(address)):
            entrance_required = entrance_required or any(_residential_pickup("", candidate) for candidate in candidates)
        if poi and entrance_required:
            eligible = [candidate for candidate in candidates if candidate.get("pickup_entrance_source")
                        in {"named_gate_poi", "provider_entr_location"}]
        try:
            chosen = select_amap_pickup_candidate(address, eligible, poi=poi)
        except GeocodePrecisionError:
            # Same-name bus stops may represent opposite road sides. Preserve
            # provider ranking and expose ambiguity, never choose by route length.
            chosen = next(candidate for candidate in eligible if not amap_candidate_issues(address, candidate, poi=poi))
            if prefer_poi or entrance_required:
                ambiguous_poi = chosen
                continue
            return resolved(chosen, ambiguous=True)
        if chosen is not None:
            return resolved(chosen)
        if entrance_required:
            # Retain only the requested landmark as a visible reference, not an
            # unrelated tenant or a route-length-based choice of pickup gate.
            reference_issues = {"coarse_or_unknown_geocode_precision", "pickup_entrance_unconfirmed",
                                "residential_entrance_unconfirmed", "requested_entrance_not_preserved"}
            fallback_poi = next((candidate for candidate in candidates
                                 if set(amap_candidate_issues(address, candidate, poi=poi)) <= reference_issues), None)
    if fallback_geocode is not None:
        result = resolved(fallback_geocode, ambiguous=ambiguous_poi is not None)
        result["pickup_resolution_status"] = "reference_only"
        return result
    if ambiguous_poi is not None:
        return resolved(ambiguous_poi, ambiguous=True)
    if fallback_poi is not None:
        return {**resolved(fallback_poi), "pickup_resolution_status": "reference_only"}
    raise GeocodePrecisionError("No unique, precise pickup matches this address. Road/area centroids, unrelated POIs and unrequested parking locations are not accepted; specify the stop, building number or entrance.")
