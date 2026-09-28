"""Verified, provenance-preserving fallback for Google-mode service points."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

from filelock import FileLock
from json_cache_store import load_json_object, save_json_object
from google_pickup_points import normalize_point, _identity_issues, _lookup
import google_coordinates as coordinates

POLICY = 'verified-google-pickup-fallback-v3'
TTL = timedelta(days=7)
LOCATION_FIELDS = (
    'lat', 'lng', 'plot_lat', 'plot_lng', 'provider', 'coordinate_system',
    'formatted_address', 'geocode_status', 'geocode_source', 'warning',
)


def confirmed(point):
    import client_runtime as runtime
    from pickup_overrides import confirmed_pickup
    if not runtime.is_china_country(str(point.get('country') or 'China')):
        return None
    config = runtime._china_city_config(point.get('city', ''))
    if not config:
        return None
    return confirmed_pickup(str(config['amap_city']), str(point.get('requested_address') or point.get('address') or ''))


def cached(point):
    import client_runtime as runtime
    values = load_json_object(Path(runtime.CACHE_DIR) / 'geocode_cache.json')
    for key in runtime.geocode_cache_lookup_keys(point.get('country', 'China'), point.get('city', ''),
                                               point.get('requested_address') or point.get('address', '')):
        value = values.get(key)
        if isinstance(value, dict) and value.get('provider') == 'amap':
            return deepcopy(value)
    return None


def checked(point, candidate, *, operator=False):
    from amap_geocode_quality import GEOCODE_PROVENANCE_FIELDS, PRECISE_LEVELS
    from amap_driving import gcj02_to_wgs84
    from google_final_validation import ValidationUnavailable
    import client_runtime as runtime
    if not isinstance(candidate, dict) or candidate.get('provider') != 'amap':
        raise ValidationUnavailable('google_pickup_identity_unresolved')
    try:
        lat, lng = candidate['lat'], candidate['lng']
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in (lat, lng))
                or abs(lat) > 90 or abs(lng) > 180
                or candidate.get('coordinate_system', 'GCJ02') not in {'GCJ02', 'GCJ-02'}
                or not runtime.is_plausible_geocode_result(point.get('country', 'China'), point.get('city', ''),
                    lat, lng, candidate.get('formatted_address', ''), candidate.get('adcode', ''))):
            raise ValueError('Invalid fallback coordinate')
    except (KeyError, TypeError, ValueError):
        raise ValidationUnavailable('google_pickup_identity_unresolved', details={'reason': 'invalid_fallback_coordinate'}) from None
    # Input row metadata cannot self-certify a correction. Only the registry path
    # may supply operator=True; every provider candidate is rechecked by identity.
    if operator:
        if not candidate.get('pickup_override_revision'):
            raise ValidationUnavailable('google_pickup_identity_unresolved')
    else:
        candidate = {k: v for k, v in candidate.items() if not k.startswith('pickup_override_')}
        if candidate.get('pickup_precision_status') == 'operator_confirmed':
            candidate['pickup_precision_status'] = 'unverified'
        value = {**candidate, 'address': point.get('address'),
                 'requested_address': point.get('requested_address') or point.get('address')}
        issues = _identity_issues(value)
        if not (candidate.get('formatted_address') or candidate.get('amap_poi_name')):
            issues.append('provider_identity_missing')
        if '\u5bf9\u9762' in str(point.get('requested_address') or point.get('address') or ''):
            issues.append('roadside_pickup_unconfirmed')
        if candidate.get('geocode_level') == 'poi' and not candidate.get('amap_poi_id'):
            issues.append('provider_poi_identity_missing')
        if candidate.get('geocode_level') not in PRECISE_LEVELS:
            issues.append('coarse_or_unknown_geocode_precision')
        # Re-evaluate precision under the current policy instead of retaining a
        # stale reference-only flag after a precise, named gate is established.
        if issues:
            raise ValidationUnavailable('google_pickup_identity_unresolved', details={'identity_issues': sorted(set(issues))})
        if candidate.get('pickup_resolution_status') == 'reference_only':
            candidate = {**candidate, 'pickup_resolution_status': 'matched',
                         'pickup_precision_status': 'matched', 'pickup_precision_issues': [],
                         'pickup_entrance_source': 'provider_named_gate_geocode'}
    location = {k: deepcopy(candidate[k]) for k in (*LOCATION_FIELDS, *GEOCODE_PROVENANCE_FIELDS) if k in candidate}
    for prefix in ('amap_', 'pickup_', 'geocode_', 'google_'):
        point = {k: v for k, v in point.items() if not k.startswith(prefix)}
    result = normalize_point({**point, **location})
    result['plot_lat'], result['plot_lng'] = gcj02_to_wgs84(result['lat'], result['lng'])
    result.update(coordinate_system='GCJ02', plot_coordinate_system='WGS84',
                  google_coordinate_profile=coordinates.profile_for(result.get('country', 'China'), result.get('city', '')),
                  geocode_status='ok')
    result.pop('warning', None)
    return result


class VerifiedPickupFallback:
    def __init__(self, path, check_canceled, *, lookup=None, cache_lookup=None, confirmed_lookup=None,
                 now=lambda: datetime.now(timezone.utc)):
        self.path = Path(path)
        self.check_canceled = check_canceled
        self.lookup = lookup or _lookup
        self.cache_lookup = cache_lookup or cached
        self.confirmed_lookup = confirmed_lookup or confirmed
        self.now = now
        self.api_calls = 0
        self.failures = {}

    def _count(self):
        self.api_calls += 1

    def operator_point(self, point):
        self.check_canceled()
        candidate = self.confirmed_lookup(point)
        if candidate is None:
            return None
        result = checked(point, candidate, operator=True)
        result['location_resolution'] = {'policy': POLICY, 'source': 'operator_confirmed',
            'coordinate_provider': 'amap', 'timing_provider': 'google_routes',
            'revision': candidate['pickup_override_revision']}
        return result

    def resolve(self, point, google_failure):
        from amap_geocode_quality import GeocodePrecisionError, GEOCODE_PROVENANCE_FIELDS
        from google_final_validation import ValidationUnavailable
        import client_runtime as runtime
        self.check_canceled()
        key = runtime.geocode_cache_key(point.get('country', 'China'), point.get('city', ''),
            point.get('requested_address') or point.get('address', ''))
        if key in self.failures:
            raise ValidationUnavailable('google_pickup_identity_unresolved', details=deepcopy(self.failures[key]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock', timeout=180):
            entries = load_json_object(self.path)
            saved = entries.get(key, {})
            now = self.now()
            candidates = []
            try:
                if (saved.get('policy') == POLICY and saved.get('google_failure') == str(google_failure)
                        and now < datetime.fromisoformat(saved['expires_at']) <= now + TTL):
                    candidates.append(saved['point'])
            except (KeyError, TypeError, ValueError):
                pass
            candidates.append(self.cache_lookup(point))
            result = None
            reused_saved = False
            for index, candidate in enumerate(candidates):
                try:
                    result = checked(point, candidate)
                    reused_saved = index == 0 and len(candidates) == 2
                    break
                except ValidationUnavailable:
                    continue
            if result is None:
                self.check_canceled()
                try:
                    result = checked(point, self.lookup(point, self.check_canceled, self._count))
                except (GeocodePrecisionError, ValidationUnavailable) as exc:
                    if isinstance(exc, ValidationUnavailable) and str(exc) != 'google_pickup_identity_unresolved':
                        raise
                    details = {
                        'address': point.get('address'), 'google_failure': str(google_failure),
                        'fallback_source': 'amap', 'next_action': 'confirm_pickup_identity',
                        **getattr(exc, 'details', {})}
                    self.failures[key] = deepcopy(details)
                    raise ValidationUnavailable('google_pickup_identity_unresolved', details=details) from None
            self.check_canceled()
            # Provider candidates, not service counts or input ownership, are cached
            # separately from both original provider caches.
            stored = {k: deepcopy(result[k]) for k in (*LOCATION_FIELDS, *GEOCODE_PROVENANCE_FIELDS) if k in result}
            if not reused_saved:
                entries[key] = {'policy': POLICY, 'google_failure': str(google_failure), 'point': stored,
                                'resolved_at': now.isoformat(), 'expires_at': (now + TTL).isoformat()}
                save_json_object(self.path, entries)
        result['location_resolution'] = {'policy': POLICY, 'source': 'verified_amap_fallback',
            'coordinate_provider': 'amap', 'timing_provider': 'google_routes',
            'google_failure': str(google_failure), 'identity_status': 'matched',
            'entrance_source': result.get('pickup_entrance_source') or 'provider_location',
            'route_endpoint_status': 'not_measured'}
        return result
