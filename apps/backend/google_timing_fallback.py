"""Explicit current-traffic fallback for Google road endpoint coverage gaps."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import google_final_validation as google

RECOVERABLE = {'google_pickup_snap_mismatch', 'google_cross_request_join_mismatch'}
MIXED_NOTE = 'Includes AMap current-traffic timing; not a complete Google future-traffic forecast.'


def provenance(legs):
    sources = sorted({leg.get('timing_provider', 'google_routes') for leg in legs})
    fallback = [i for i, leg in enumerate(legs) if leg.get('timing_provider', 'google_routes') != 'google_routes']
    return {'timing_sources': sources, 'forecast_complete': not fallback,
            'fallback_policy_version': 'local-current-traffic-v1' if fallback else None,
            'fallback_leg_indexes': fallback, 'fallback_leg_count': len(fallback),
            'timing_note': MIXED_NOTE if fallback else '',
            'source': 'google_routes_with_amap_current_fallback' if fallback else 'google_routes'}


class LocalTimingFallback:
    def __init__(self, *, provider=None, check_canceled=lambda: None, api_call_limit=500,
                 now=lambda: datetime.now(timezone.utc)):
        self.provider = provider
        self.check_canceled = check_canceled
        self.api_call_limit = api_call_limit
        self.now = now
        self.points = {}
        self.cache = {}
        self.cached_at = {}
        self.reasons = {}

    def bind(self, points):
        # Called only after the shared location resolver has validated identity.
        self.points = {(float(p['plot_lat']), float(p['plot_lng'])): deepcopy(p) for p in points}

    @property
    def api_calls(self):
        return int(self.provider.state.get('api_calls', 0)) if self.provider is not None else 0

    def route(self, points, departure, reason):
        self.check_canceled()
        key = tuple(tuple(p) for p in points)
        if len(key) != 2 or any(p not in self.points for p in key):
            raise google.ValidationUnavailable('google_fallback_identity_unavailable')
        self.reasons[key] = deepcopy(reason)
        if key in self.cache and not 0 <= (self.now()-self.cached_at[key]).total_seconds() < 600:
            self.cache.pop(key)
        if key not in self.cache:
            if self.provider is None:
                from direct_school_analysis import FreshRouteProvider
                self.provider = FreshRouteProvider('amap', departure_time=None, api_call_limit=self.api_call_limit)
            try:
                receipt = self.provider.route([self.points[p] for p in key])
            except (InterruptedError, google.ValidationUnavailable):
                raise
            except Exception as exc:
                raise google.ValidationUnavailable('google_local_timing_unavailable',
                    details={'native_error_type': type(exc).__name__, 'original_google_failure': reason}) from None
            self.check_canceled()
            if receipt.get('status') != 'verified' or not receipt.get('complete') or len(receipt.get('legs', [])) != 1:
                raise google.ValidationUnavailable('google_local_timing_unverified')
            leg = deepcopy(receipt['legs'][0])
            geometry = leg.get('geometry') or []
            if len(geometry) < 2 or leg.get('coordinate_system') != 'WGS84':
                raise google.ValidationUnavailable('google_local_timing_geometry_invalid')
            for lng, lat in geometry:
                google.location({'latLng': {'latitude':lat, 'longitude':lng}})
            for name in ('duration_s', 'distance_m'):
                if not math.isfinite(float(leg.get(name, -1))) or float(leg.get(name, -1)) < 0:
                    raise google.ValidationUnavailable('google_local_timing_metrics_invalid')
            start, end = tuple(reversed(geometry[0])), tuple(reversed(geometry[-1]))
            if google.meters(start, key[0]) > 100 or google.meters(end, key[1]) > 100:
                raise google.ValidationUnavailable('google_local_timing_endpoint_mismatch')
            leg.update(start=start, end=end, timing_provider='amap', time_basis='current_traffic',
                       forecast_complete=False, provider_called_at=receipt.get('called_at'),
                       fallback_reason=deepcopy(reason), native_route_evidence=deepcopy(receipt))
            self.cache[key] = leg
            self.cached_at[key] = self.now()
        result = deepcopy(self.cache[key])
        result['requested_forecast_departure'] = departure.isoformat()
        # Reusing a current-traffic receipt does not make it a future forecast.
        return result


class HybridValidationSession(google.ValidationSession):
    def __init__(self, client, max_rounds=4, *, fallback=None):
        super().__init__(client, max_rounds)
        self.fallback = fallback or LocalTimingFallback(check_canceled=lambda: client.check_canceled())

    def bind_pickups(self, points):
        self.fallback.bind(points)

    @staticmethod
    def call_count(points, dwell):
        # A batch may need ordered edge remeasurement after a coverage failure.
        return len(points) if not any(dwell) and len(points) <= 27 else len(points)-1

    def _edge(self, points, departure, known=None):
        key = tuple(tuple(p) for p in points)
        reason = known or self.fallback.reasons.get(key)
        if reason:
            return self.fallback.route(points, departure, reason)
        try:
            leg = self.client.route(points, departure)[0]
            return {**leg, 'timing_provider':'google_routes', 'time_basis':'future_traffic', 'forecast_complete':True}
        except google.ValidationUnavailable as exc:
            if str(exc) not in RECOVERABLE:
                raise
            return self.fallback.route(points, departure, {'code':str(exc), **deepcopy(exc.details)})

    def measure(self, points, departure, dwell):
        known_index, known_reason = None, None
        if not any(dwell) and len(points) <= 27:
            try:
                return super().measure(points, departure, dwell)
            except google.ValidationUnavailable as exc:
                if str(exc) not in RECOVERABLE:
                    raise
                known_index = exc.details.get('leg_index')
                known_reason = {'code':str(exc), **deepcopy(exc.details)}
        legs = []
        for i in range(len(points)-1):
            self.client.check_canceled()
            clock = departure + timedelta(seconds=sum(dwell[:i+1])+sum(x['duration_s'] for x in legs))
            try:
                leg = self._edge(points[i:i+2], clock, known_reason if i == known_index else None)
                legs.append(leg)
                # If mixed providers disagree at a handoff, remeasure the two
                # incident edges natively. Never invent a connector or its time.
                for j in range(i, 0, -1):
                    if google.meters(legs[j-1]['end'], legs[j]['start']) <= 30:
                        break
                    reason = {'code':'google_cross_request_join_mismatch', 'point_index':j,
                              'snap_distance_m':google.meters(legs[j-1]['end'], legs[j]['start'])}
                    for index in (j-1, j):
                        when = departure + timedelta(seconds=sum(dwell[:index+1])+sum(x['duration_s'] for x in legs[:index]))
                        legs[index] = self.fallback.route(points[index:index+2], when, reason)
                    if google.meters(legs[j-1]['end'], legs[j]['start']) > 30:
                        raise google.ValidationUnavailable('google_local_timing_join_unresolved', details=reason)
            except google.ValidationUnavailable as exc:
                exc.details.setdefault('leg_index', i)
                raise
        for i, leg in enumerate(legs):
            if leg.get('timing_provider') == 'amap':
                leg['requested_forecast_departure'] = (departure + timedelta(
                    seconds=sum(dwell[:i+1])+sum(x['duration_s'] for x in legs[:i]))).isoformat()
        return google.Measurement(departure, legs, sum(dwell))
