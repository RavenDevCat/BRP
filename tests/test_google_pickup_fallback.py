from copy import deepcopy
from datetime import timedelta
import json

import pytest
import client_runtime as runtime
import google_geocoding as geo
from google_final_validation import ValidationUnavailable
from google_pickup_fallback import checked
from test_google_geocoding import NOW, point, payload

ADDRESS = '\u6d4b\u8bd5\u8def238\u53f7'


def candidate(address=ADDRESS):
    return {'provider': 'amap', 'country': 'China', 'city': 'Shanghai',
        'address': address, 'lat': 31.2, 'lng': 121.4,
        'formatted_address': '\u4e0a\u6d77\u5e02' + address,
        'geocode_level': '\u95e8\u724c\u53f7', 'coordinate_system': 'GCJ02'}


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'is_plausible_geocode_result', lambda *args, **kwargs: True)
    def lookup(*args):
        args[-1]()
        return {'status': 'ZERO_RESULTS'}
    value = geo.GoogleGeocodeResolver('unit', path=tmp_path/'google.json', lookup=lookup,
        now=lambda: NOW, allow_verified_fallback=True)
    value.fallback.confirmed_lookup = lambda p: None
    value.fallback.cache_lookup = lambda p: candidate()
    value.fallback.lookup = lambda *a: pytest.fail('Unexpected external AMap lookup')
    return value


def test_fallback_separate_cache_provenance_and_ownership(resolver):
    original = point(ADDRESS)
    saved = deepcopy(original)
    result = resolver.resolve(original)
    assert original == saved
    assert result['provider'] == 'amap'
    assert result['node_id'] == original['node_id'] and result['passenger_count'] == 3
    assert result['location_resolution']['source'] == 'verified_amap_fallback'
    assert result['google_coordinate_profile'] == 'google-shanghai-gcj02-v1'
    assert result['location_resolution']['timing_provider'] == 'google_routes'
    assert 'google_place_id' not in result and 'pickup_override_revision' not in result
    assert result['plot_lat'] != result['lat']
    assert next(iter(json.loads(resolver.path.read_text()).values()))['state'] == 'unresolved'
    stored = next(iter(json.loads(resolver.fallback.path.read_text()).values()))['point']
    assert stored['provider'] == 'amap' and 'passenger_count' not in stored


def test_google_match_precedes_provider_fallback(resolver):
    resolver.lookup = lambda *a: payload(formatted_address='Shanghai ' + ADDRESS)
    resolver.fallback.cache_lookup = lambda p: pytest.fail('Unneeded fallback')
    result = resolver.resolve(point(ADDRESS))
    assert result['provider'] == 'google'
    assert result['location_resolution']['source'] == 'google_geocode'


def test_registry_confirmation_precedes_automatic_geocoding(resolver):
    value = {**candidate(), 'pickup_override_revision': 3, 'pickup_precision_status': 'operator_confirmed'}
    resolver.fallback.confirmed_lookup = lambda p: value
    resolver.lookup = lambda *a: pytest.fail('Operator location must not be overwritten')
    result = resolver.resolve(point(ADDRESS))
    assert result['location_resolution']['source'] == 'operator_confirmed'
    assert result['location_resolution']['revision'] == 3
    assert resolver.api_calls == resolver.fallback_api_calls == 0
    assert not resolver.path.exists() and not resolver.fallback.path.exists()


@pytest.mark.parametrize('code', ['google_budget_cap', 'google_geocode_http_403',
    'google_geocode_transport_failed', 'google_geocode_provider_rejected'])
def test_systemic_failure_does_not_trigger_fallback(resolver, code):
    def fail(*args):
        raise ValidationUnavailable(code)
    resolver.lookup = fail
    resolver.fallback.cache_lookup = lambda p: pytest.fail('System failure cannot become local fallback')
    with pytest.raises(ValidationUnavailable, match=code):
        resolver.resolve(point(ADDRESS))


@pytest.mark.parametrize('change', [
    {'geocode_level': '\u9053\u8def'},
    {'formatted_address': '\u6d4b\u8bd5\u8def999\u53f7'},
    {'lat': float('nan')},
    {'provider': 'google'},
    {'coordinate_system': 'WGS84'},
    {'geocode_level': 'poi', 'amap_poi_id': ''},
    {'formatted_address': ''},
])
def test_invalid_or_unverified_fallback_not_accepted(resolver, change):
    with pytest.raises(ValidationUnavailable, match='google_pickup_identity_unresolved'):
        checked(point(ADDRESS), {**candidate(), **change})


def test_input_or_cached_metadata_cannot_self_certify(resolver):
    fake = {**candidate('\u6d4b\u8bd5\u8def999\u53f7'),
        'pickup_precision_status': 'operator_confirmed', 'pickup_override_revision': 1}
    with pytest.raises(ValidationUnavailable):
        checked(point(ADDRESS), fake)


def test_opposite_roadside_needs_registry_confirmation(resolver):
    with pytest.raises(ValidationUnavailable, match='google_pickup_identity_unresolved'):
        checked(point(ADDRESS+'\u5bf9\u9762'), candidate())


def test_fallback_cache_reused_without_repeating_targeted_call(resolver):
    resolver.fallback.cache_lookup = lambda p: None
    calls = []
    def lookup(p, cancel, count):
        count(); calls.append(1)
        return candidate()
    resolver.fallback.lookup = lookup
    resolver.resolve(point(ADDRESS))
    result = resolver.resolve({**point(ADDRESS), 'passenger_count': 9})
    assert result['passenger_count'] == 9
    assert calls == [1] and resolver.fallback_api_calls == 1
    assert resolver.api_calls != resolver.fallback_api_calls


def test_expired_fallback_not_reused(resolver):
    resolver.resolve(point(ADDRESS))
    resolver.fallback.now = lambda: NOW + timedelta(days=8)
    resolver.fallback.cache_lookup = lambda p: None
    calls = []
    def lookup(p, cancel, count):
        calls.append(1)
        return candidate()
    resolver.fallback.lookup = lookup
    resolver.resolve(point(ADDRESS))
    assert calls == [1]


def test_cancel_before_provider_or_cache(resolver):
    def cancel():
        raise InterruptedError('canceled')
    resolver.check_canceled = cancel
    with pytest.raises(InterruptedError):
        resolver.resolve(point(ADDRESS))
    assert not resolver.path.exists() and not resolver.fallback.path.exists()


def test_shared_timing_session_enables_verified_fallback():
    from types import SimpleNamespace
    from google_final_validation import ValidationSession
    session = ValidationSession(SimpleNamespace(budget_id='unit', check_canceled=lambda: None))
    assert session.pickups.fallback is not None


@pytest.mark.parametrize('code,local', [('30001', True), ('10001', False), ('40000', False)])
def test_amap_data_failure_is_local_but_auth_and_quota_are_global(monkeypatch, code, local):
    from amap_geocode_quality import GeocodePrecisionError
    from google_pickup_points import _lookup
    monkeypatch.setattr(runtime, '_amap_city_param', lambda *a: '310000')
    calls = []
    def fail(endpoint, params, limiter):
        calls.append(endpoint)
        raise runtime.AMapProviderError(endpoint, 'TEST_ERROR', code)
    monkeypatch.setattr(runtime, 'amap_request_json', fail)
    with pytest.raises(GeocodePrecisionError if local else ValidationUnavailable):
        _lookup(point(ADDRESS), lambda: None, lambda: None)
    assert len(calls) <= 2


def test_nearby_hint_is_not_another_pickup_but_gate_and_roadside_stay():
    from google_pickup_points import _match_address
    assert _match_address(point('Station (\u8fd1Road595\u53f7)')) == 'Station'
    assert _match_address(point('Garden (\u4e1c\u95e8)')) == 'Garden (\u4e1c\u95e8)'
    assert _match_address(point('Garden\u5357\u95e8\u5bf9\u9762')) == 'Garden\u5357\u95e8\u5bf9\u9762'


def test_cached_read_does_not_extend_fallback_ttl(resolver):
    resolver.resolve(point(ADDRESS))
    before = resolver.fallback.path.read_text()
    resolver.fallback.now = lambda: NOW + timedelta(days=3)
    resolver.resolve(point(ADDRESS))
    assert resolver.fallback.path.read_text() == before


def test_local_failure_is_not_retried_in_same_solve(resolver):
    from amap_geocode_quality import GeocodePrecisionError
    resolver.fallback.cache_lookup = lambda p: None
    calls = []
    def fail(*args):
        calls.append(1)
        raise GeocodePrecisionError('No match')
    resolver.fallback.lookup = fail
    for _ in range(2):
        with pytest.raises(ValidationUnavailable, match='google_pickup_identity_unresolved'):
            resolver.resolve(point(ADDRESS))
    assert calls == [1]
