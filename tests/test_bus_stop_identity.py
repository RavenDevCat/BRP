from copy import deepcopy
import pytest
from test_amap_geocode_quality import quality, runtime, core, REQUEST, poi


@pytest.mark.parametrize('backend', [False, True])
def test_bus_geocode_with_precise_sounding_level_is_not_confirmed_poi(monkeypatch, backend):
    module = core.load_legacy_planner() if backend else runtime
    calls = []
    def fetch(endpoint, params, limiter):
        calls.append(endpoint)
        if endpoint == '/v3/place/text':
            return {'pois': []}
        return {'geocodes': [{'formatted_address': REQUEST, 'level': '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9',
                             'location': '121.43,31.2', 'adcode': '310105'}]}
    monkeypatch.setattr(module, 'amap_request_json', fetch)
    result = module.amap_geocode_query('China', 'Shanghai', REQUEST)
    assert calls == ['/v3/place/text', '/v3/geocode/geo']
    assert result['pickup_precision_status'] == 'needs_review'
    assert result['pickup_resolution_status'] == 'reference_only'
    assert 'bus_stop_identity_unconfirmed' in result['pickup_precision_issues']
    assert result['lat'] == 31.2 and result['lng'] == 121.43
    assert quality.reusable_amap_geocode(result, REQUEST)


def test_bus_poi_requires_identity_and_bus_type():
    assert quality.select_amap_pickup_candidate(REQUEST, [poi(id='')], poi=True) is None
    assert quality.select_amap_pickup_candidate(REQUEST, [poi(type='shop')], poi=True) is None
    assert quality.select_amap_pickup_candidate(REQUEST, [poi()], poi=True)['id'] == poi()['id']


def test_legacy_bus_coordinate_is_retained_without_false_matched_status():
    point = {'provider': 'amap', 'address': REQUEST, 'formatted_address': REQUEST,
             'geocode_level': '\u516c\u4ea4\u5730\u94c1\u7ad9\u70b9', 'lat': 31.2, 'lng': 121.43}
    original = deepcopy(point)
    result = quality.annotate_amap_pickup(point, REQUEST)
    assert point == original and result['lat'] == point['lat'] and result['lng'] == point['lng']
    assert result['pickup_precision_status'] == 'needs_review'
    assert quality.reusable_amap_geocode(result, REQUEST)


def test_confirmed_pickup_stays_confirmed_without_automatic_relocation():
    point = {'provider': 'amap', 'address': REQUEST, 'pickup_precision_status': 'operator_confirmed',
             'pickup_override_revision': 1, 'lat': 31.2, 'lng': 121.43}
    assert quality.annotate_amap_pickup(point, REQUEST) == point
