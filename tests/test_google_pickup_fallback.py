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


@pytest.mark.parametrize('level', ['\u5174\u8da3\u70b9', '\u95e8\u5740', '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9'])
def test_precise_named_gate_geocode_is_not_forced_to_be_a_poi(resolver, level):
    address = '\u6587\u5b9a\u8def\u5c1a\u6c47\u8c6a\u5ead\u897f\u5317\u95e8'
    value = {**candidate(address), 'geocode_level': level,
             'formatted_address': '\u4e0a\u6d77\u5e02\u5f90\u6c47\u533a\u6587\u5b9a\u8def\u5c1a\u6c47\u8c6a\u5ead(\u897f\u5317\u95e8)',
             'pickup_resolution_status': 'reference_only'}
    result = checked(point(address), value)
    assert result['pickup_resolution_status'] == 'matched'
    assert result['pickup_entrance_source'] == 'provider_named_gate_geocode'
    assert 'amap_poi_id' not in result


@pytest.mark.parametrize('change', [
    {'geocode_level': '\u4f4f\u5b85\u533a'},
    {'formatted_address': '\u6d4b\u8bd5\u82b1\u56ed(\u5317\u95e8)'},
    {'formatted_address': '\u6d4b\u8bd5\u82b1\u56ed'},
    {'formatted_address': '\u53e6\u4e00\u82b1\u56ed(\u5357\u95e8)'},
    {'pickup_precision_issues': ['multiple_provider_candidates']},
])
def test_gate_identity_conflicts_not_erased(resolver, change):
    address = '\u6d4b\u8bd5\u82b1\u56ed\u5357\u95e8'
    value = {**candidate(address), 'geocode_level': '\u5174\u8da3\u70b9', **change}
    with pytest.raises(ValidationUnavailable):
        checked(point(address), value)


def test_unrelated_bus_stop_cannot_match_landmark_only_query(resolver):
    address = '\u83b2\u82b1\u56fd\u9645\u5e7f\u573a\u95e8\u53e3\u516c\u4ea4\u7ad9'
    value = {**candidate('\u6d4b\u8bd5\u8def\u516c\u4ea4\u7ad9'), 'geocode_level': '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9'}
    with pytest.raises(ValidationUnavailable):
        checked(point(address), value)


def test_bus_road_pair_with_parenthetical_landmark_keeps_station_identity(resolver):
    base = '\u660e\u5174\u8def\u65b0\u5357\u8def\u516c\u4ea4\u7ad9'
    value = {**candidate(base), 'geocode_level': '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9'}
    result = checked(point(base+' (\u65b0\u5357\u8def\u58f9\u53f7)'), value)
    assert result['lat'] == value['lat']


def test_exact_site_precedes_a_tenant_or_another_phase():
    from google_pickup_points import _prefer_exact_site
    rows = [{'id':'site','name':'\u53e4\u5317\u58f9\u53f7','address':'1099\u5f04'},
            {'id':'spa','name':'\u53e4\u5317\u58f9\u53f7SPA','address':'1099\u5f04'},
            {'id':'phase','name':'\u53e4\u5317\u58f9\u53f72\u671f','address':'1099\u5f04'}]
    assert _prefer_exact_site('\u53e4\u5317\u58f9\u53f7', rows) == rows[:1]
    # A specified phase or gate must not be reduced to the parent compound.
    assert _prefer_exact_site('\u53e4\u5317\u58f9\u53f7\u5357\u95e8', rows) == rows


def test_phase_notation_does_not_remove_phase_gate_or_zone():
    from google_pickup_points import _phase_notation
    assert _phase_notation('Garden\u4e09\u671fA\u533a\u5357\u95e8') == 'Garden3\u671fA\u533a\u5357\u95e8'
    assert _phase_notation('Garden3\u53f7\u56ed') == 'Garden3\u671f'
    assert _phase_notation('Garden\u4e8c\u671f') != _phase_notation('Garden3\u53f7\u56ed')
    assert _phase_notation('Garden\u5341\u4e09\u671f') != _phase_notation('Garden3\u53f7\u56ed')


def test_provider_query_preserves_gate_and_uses_known_district():
    from google_pickup_points import _provider_query_address
    original = point('\u6d4b\u8bd5\u82b1\u56ed(\u5357\u95e8)')
    original['formatted_address'] = '\u4e0a\u6d77\u5e02\u95f5\u884c\u533a\u6d4b\u8bd5\u82b1\u56ed'
    assert _provider_query_address(original) == '\u4e0a\u6d77\u5e02\u95f5\u884c\u533a\u6d4b\u8bd5\u82b1\u56ed\u5357\u95e8'
    assert original['address'].endswith('(\u5357\u95e8)')


def test_provider_query_does_not_duplicate_city_or_discard_roadside():
    from google_pickup_points import _provider_query_address
    address = '\u4e0a\u6d77\u5e02\u6d4b\u8bd5\u82b1\u56ed\u5357\u95e8\u5bf9\u9762'
    assert _provider_query_address(point(address)) == address


def test_provider_alias_can_establish_compound_address_with_real_entrance(resolver):
    address = '\u6d4b\u8bd5\u8def555\u5f04'
    value = {**candidate(address), 'geocode_level':'poi', 'amap_poi_id':'compound',
        'amap_poi_name':'\u6d4b\u8bd5\u82b1\u56ed', 'amap_poi_alias':address,
        'amap_poi_address':'\u53e6\u4e00\u8def', 'formatted_address':'\u6d4b\u8bd5\u82b1\u56ed',
        'amap_poi_type':'\u4f4f\u5b85\u533a', 'pickup_entrance_source':'provider_entr_location'}
    result = checked(point(address), value)
    assert result['amap_poi_alias'] == address
    with pytest.raises(ValidationUnavailable):
        checked(point(address), {**value, 'pickup_entrance_source':''})
    with pytest.raises(ValidationUnavailable):
        checked(point(address+'\u5357\u95e8'), value)


def test_alias_cannot_turn_parent_centroid_into_named_gate(resolver):
    address = '\u6d4b\u8bd5\u82b1\u56ed\u5357\u95e8'
    value = {**candidate(address), 'geocode_level':'poi', 'amap_poi_id':'compound',
        'amap_poi_name':'\u6d4b\u8bd5\u82b1\u56ed', 'amap_poi_alias':address,
        'formatted_address':'\u6d4b\u8bd5\u82b1\u56ed', 'pickup_entrance_source':''}
    with pytest.raises(ValidationUnavailable):
        checked(point(address), value)


def test_compound_prefix_does_not_become_part_of_road_name(resolver):
    name, road = '\u6d4b\u8bd5\u82b1\u56ed', '\u6d4b\u8bd5\u8def555\u5f04'
    value = {**candidate(road), 'geocode_level':'poi', 'amap_poi_id':'compound',
        'amap_poi_name':name, 'amap_poi_alias':'\u6d4b\u8bd5\u82b1\u56ed\u516c\u5bd3',
        'amap_poi_address':road, 'formatted_address':road+name,
        'amap_poi_type':'\u4f4f\u5b85\u533a', 'pickup_entrance_source':'provider_entr_location'}
    assert checked(point(name+road), value)['amap_poi_id'] == 'compound'
    with pytest.raises(ValidationUnavailable):
        checked(point(name+'\u53e6\u4e00\u8def555\u5f04'), value)
