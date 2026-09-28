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


def _query_address(point):
    import re
    import unicodedata
    address = unicodedata.normalize("NFKC", _address(point))
    # Parenthetical proximity hints describe surroundings, not a second stop.
    return re.sub(r"\(\s*\u8fd1[^()]+\)", "", address).strip()


def _match_address(point):
    from amap_geocode_quality import _gate_tokens
    address = _query_address(point)
    # Generic doorstep wording is not a landmark name; specific gates stay intact.
    return address.removesuffix("\u95e8\u53e3") if not _gate_tokens(address) else address


def _provider_query_address(point):
    import re
    from amap_geocode_quality import _gate_tokens
    import client_runtime as runtime
    config = runtime._china_city_config(point.get('city', ''))
    query = _query_address(point)
    # Preserve the named gate but avoid brackets that some geocoder queries
    # interpret as an optional annotation and drop from the returned identity.
    query = re.sub(r'\(([^()]*)\)', lambda m: m[1] if _gate_tokens(m[1]) else m[0], query)
    if query.endswith('\u95e8\u53e3'):
        query = query[:-1] if _gate_tokens(query) else query[:-2]
    if config:
        native = next((s for s in config['aliases'] if s.endswith('\u5e02')), '')
        if native and not query.startswith(native.removesuffix('\u5e02')):
            prefix = re.match(re.escape(native) + r'[^\s]{2,5}?[\u533a\u53bf]',
                              str(point.get('formatted_address') or ''))
            query = (prefix[0] if prefix else native) + query
    return query


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
    normalized = {**point}
    for key in ('amap_poi_name', 'amap_poi_address', 'formatted_address'):
        normalized[key] = _phase_notation(str(point.get(key) or ''))
    parent_address = _verified_parent_address(point)
    if parent_address:
        normalized['amap_poi_address'] += ' ' + parent_address
    alias = point.get('amap_poi_alias')
    if isinstance(alias, str) and alias.strip() and point.get('amap_poi_id') and not _gate_tokens(alias):
        # The provider's explicit alias is identity evidence, not a fuzzy match
        # or permission to change entrance coordinates.
        for key in ('amap_poi_name', 'amap_poi_address', 'formatted_address'):
            normalized[key] += ' ' + _phase_notation(alias)
    requested = _phase_notation(_match_address(point))
    name = _phase_notation(str(point.get('amap_poi_name') or ''))
    from amap_geocode_quality import _road_tokens
    if name and not _road_tokens(name) and requested.startswith(name) and _road_tokens(requested[len(name):]):
        # Keep a leading compound name out of the following street token.
        requested = name + ' ' + requested[len(name):]
    checked = annotate_amap_pickup(normalized, requested)
    issues = [value for value in checked.get("pickup_precision_issues", [])
              if value != "coarse_or_unknown_geocode_precision"]
    if _precise_named_gate(point, issues):
        return []
    if _bus_landmark_missing(normalized):
        issues.append('requested_bus_landmark_not_preserved')
    if point.get("pickup_resolution_status") == "reference_only" and not issues:
        issues.append("reference_only")
    return issues


def _phase_notation(value):
    import re
    digits = '\u96f6\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d'
    value = re.sub(r'([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d])\u671f',
                   lambda m: str(digits.index(m[1]))+'\u671f', value)
    return re.sub(r'(\d+)\u53f7\u56ed', lambda m: m[1]+'\u671f', value)


def _precise_named_gate(point, issues):
    from amap_geocode_quality import _gate_tokens
    requested = _match_address(point)
    gates = _gate_tokens(requested)
    return bool(gates and point.get('geocode_level') in {
        '\u5174\u8da3\u70b9', '\u95e8\u5740', '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9'
    } and gates == _gate_tokens(str(point.get('formatted_address') or ''))
        and set(issues) <= {'pickup_entrance_unconfirmed', 'residential_entrance_unconfirmed'}
        and '\u5bf9\u9762' not in requested)


def _bus_landmark_missing(point):
    import re
    from amap_geocode_quality import _compact, _local_address, _road_tokens
    requested = _match_address(point)
    if not re.search(r'\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0', requested):
        return False
    residual = re.sub(r'\([^()]*\)', '', _local_address(requested))
    for road in _road_tokens(requested):
        residual = residual.replace(road, '')
    residual = _compact(re.sub(r'\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0|\u95e8\u53e3', '', residual))
    text = _compact(str(point.get('amap_poi_name') or point.get('formatted_address') or ''))
    return bool(residual and residual not in text)


def _prefer_exact_site(address, pois):
    import re
    from amap_geocode_quality import _compact, _local_address, _road_tokens, _gate_tokens
    if _gate_tokens(address) or re.search(r'\u516c\u4ea4|\u7ad9\u53f0|\d+(?:\u53f7|\u5f04)', address):
        return pois
    name = _local_address(address)
    roads = _road_tokens(address)
    for road in roads:
        name = name.replace(road, '')
    exact = [p for p in pois if _compact(name) and _compact(p.get('name')) == _compact(name)
             and all(_compact(road) in _compact(p.get('address')) for road in roads)]
    return exact or pois


def _prefer_intersection(address, pois):
    import re
    from amap_geocode_quality import _compact, _road_tokens
    roads = _road_tokens(address)
    if len(roads) != 2 or not re.search(r'\u8def\u53e3|\u4ea4\u53c9\u53e3', address) or '\u516c\u4ea4' in address:
        return pois
    junctions = [p for p in pois if '\u8def\u53e3\u540d' in str(p.get('type') or '')
                 or '\u4ea4\u53c9\u53e3' in str(p.get('name') or '')]
    # Preserve the primary street named by the user; do not substitute a shop,
    # bus platform, or the reverse street-order POI at the same junction.
    ordered = [p for p in junctions if all(_compact(r) in _compact(p.get('name')) for r in roads)
               and _compact(p.get('name')).find(_compact(roads[0])) < _compact(p.get('name')).find(_compact(roads[1]))]
    return ordered or junctions


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
    captured = []
    def request(endpoint, params, limiter):
        nonlocal attempts
        if endpoint != '/v3/place/detail':
            params = {**params, ("keywords" if endpoint == "/v3/place/text" else "address"): _provider_query_address(point)}
        if endpoint == "/v3/place/text":
            import re
            from amap_geocode_quality import _gate_tokens
            address = _match_address(point)
            if re.search(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0", address):
                params['types'] = '150700'
            elif _gate_tokens(address):
                params['types'] = '150501' if '\u5730\u94c1' in address else '991000|991400'
        # One POI, one geocode, and at most two exact parent-POI lookups.
        try:
            check_canceled()
        except Exception as exc:
            canceled.append(exc)
            raise
        if canceled:
            raise canceled[0]
        if attempts >= 4:
            raise ValidationUnavailable("google_pickup_lookup_unavailable")
        attempts += 1
        counted()
        try:
            response = runtime.amap_request_json(endpoint, params, limiter)
            if endpoint == '/v3/geocode/geo':
                import json
                # Repeated identical rows are one candidate, not ambiguity.
                values = {json.dumps(p, sort_keys=True, ensure_ascii=False): p
                          for p in response.get('geocodes', [])}
                response = {**response, 'geocodes': list(values.values())}
            if endpoint in {"/v3/place/text", "/v3/place/detail"}:
                from amap_geocode_quality import _compact, _gate_tokens
                address = _match_address(point)
                # Empty-id text fallbacks are not entrance or bus-platform POIs.
                response = {**response, 'pois': [p for p in response.get('pois', []) if p.get('id')]}
                if endpoint == '/v3/place/detail':
                    response['pois'] = [p for p in response['pois'] if p['id'] == params['id']]
                response['pois'] = _prefer_exact_site(address, response['pois'])
                response['pois'] = _prefer_intersection(address, response['pois'])
                # An exact compound address outranks units inside that compound;
                # otherwise retain all candidates so ambiguity is not hidden.
                exact = [p for p in response.get("pois", [])
                         if _compact(p.get("address")) == _compact(address)]
                if exact and not _gate_tokens(address):
                    response = {**response, "pois": exact}
            captured.append((endpoint, response))
            return response
        except runtime.AMapProviderError as exc:
            if exc.infocode == "30001":
                # A failed data response is not a successful lookup. The other
                # endpoint may still resolve this address; otherwise it stays unresolved.
                raise GeocodePrecisionError("AMap address data response failed (30001)") from exc
            failures.append(type(exc).__name__)
            raise
        except Exception as exc:
            failures.append(type(exc).__name__)
            raise
    def recover():
        candidate = _select_captured_pickup(point, captured)
        if candidate is not None:
            return candidate
        parent = _parent_poi_id(point, captured)
        visited = set()
        for _ in range(2):
            if not parent or parent in visited:
                break
            visited.add(parent)
            try:
                response = request('/v3/place/detail', {'id':parent, 'extensions':'all'}, runtime.AMAP_PLACES_LIMITER)
            except GeocodePrecisionError:
                return None
            except Exception:
                if canceled:
                    raise canceled[0]
                raise ValidationUnavailable('google_pickup_lookup_unavailable') from None
            candidate = _select_captured_pickup(point, captured)
            if candidate is not None:
                return candidate
            parents = {p.get('parent') for p in response.get('pois', [])
                       if isinstance(p.get('parent'), str) and p['parent'].isalnum()}
            parent = next(iter(parents)) if len(parents) == 1 else None
        return None
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
        candidate = recover()
        if candidate is not None:
            return candidate
        raise
    if canceled:
        raise canceled[0]
    if failures:
        raise ValidationUnavailable("google_pickup_lookup_unavailable")
    if _identity_issues({**result, 'requested_address': _address(point)}):
        candidate = recover()
        if candidate is not None:
            return candidate
    return result


def _select_captured_pickup(point, captured):
    from amap_geocode_quality import convert_amap_candidate, GeocodePrecisionError
    from google_pickup_fallback import checked
    from google_final_validation import ValidationUnavailable
    import client_runtime as runtime
    matches = {True: {}, False: {}}
    for endpoint, response in captured:
        poi = endpoint in {'/v3/place/text', '/v3/place/detail'}
        for raw in response.get('pois' if poi else 'geocodes', []):
            if poi and not raw.get('id'):
                continue
            candidate = convert_amap_candidate(raw, poi, country=point.get('country', 'China'),
                city=point.get('city', ''), address=_match_address(point),
                plausible=runtime.is_plausible_geocode_result, to_wgs84=runtime.gcj02_to_wgs84)
            if candidate and poi:
                children = [deepcopy(child) for _, receipt in captured for child in receipt.get('pois', [])
                            if child.get('id') and child.get('parent') == raw.get('id')]
                if children:
                    candidate['amap_parent_address_evidence'] = children
            try:
                result = checked(point, candidate)
            except ValidationUnavailable:
                continue
            matches[poi][(result['lat'], result['lng'])] = result
    # A unique, identity-matched POI is more specific than the geocoder's
    # representation of the same junction. Multiple POIs remain ambiguous.
    accepted = matches[True] or matches[False]
    if len(accepted) > 1:
        raise GeocodePrecisionError('Multiple precise pickup coordinates remain; no automatic choice')
    return next(iter(accepted.values()), None)


def _verified_parent_address(point):
    import re
    from amap_geocode_quality import _compact, _gate_tokens, _road_tokens
    from google_final_validation import meters
    requested = _match_address(point)
    numbers = re.findall(r'\d+(?:\u53f7|\u5f04)', requested)
    roads = _road_tokens(requested)
    if (not numbers or not roads or _gate_tokens(requested)
            or re.search(r'\u516c\u4ea4|\u5bf9\u9762', requested)
            or '\u4f4f\u5b85\u533a' not in str(point.get('amap_poi_type') or '')
            or not point.get('amap_poi_entr_location')):
        return ''
    for child in point.get('amap_parent_address_evidence') or []:
        if not isinstance(child, dict) or not child.get('id') or child.get('parent') != point.get('amap_poi_id'):
            continue
        text = _compact(child.get('address'))
        if not all(_compact(r) in text for r in roads) or not all(re.search(r'(?<!\d)'+re.escape(n), text) for n in numbers):
            continue
        try:
            child_lng, child_lat = map(float, str(child.get('location')).split(','))
            parent_lng, parent_lat = map(float, str(point.get('amap_poi_location')).split(','))
            if (not all(math.isfinite(v) for v in (child_lat,child_lng,parent_lat,parent_lng))
                    or meters((child_lat,child_lng),(parent_lat,parent_lng)) > 750):
                continue
        except (TypeError, ValueError):
            continue
        return str(child['address'])
    return ''


def _parent_poi_id(point, captured):
    import re
    from amap_geocode_quality import _compact, _gate_tokens, _road_tokens
    address = _match_address(point)
    numbers = re.findall(r'\d+(?:\u53f7|\u5f04)', address)
    if not numbers or _gate_tokens(address) or re.search(r'\u516c\u4ea4|\u5bf9\u9762', address):
        return None
    roads = _road_tokens(address)
    parents = set()
    for endpoint, response in captured:
        if endpoint != '/v3/place/text':
            continue
        for raw in response.get('pois', []):
            parent = raw.get('parent')
            text = _compact(raw.get('address'))
            if (isinstance(parent, str) and re.fullmatch(r'[A-Za-z0-9]{5,40}', parent)
                    and all(_compact(road) in text for road in roads)
                    and all(re.search(r'(?<!\d)' + re.escape(n), text) for n in numbers)):
                parents.add(parent)
    return next(iter(parents)) if len(parents) == 1 else None


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
