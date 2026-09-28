from copy import deepcopy

import pytest
import google_final_validation as google
from test_google_final_validation import response


def data(total, delta, count=5):
    points = [(31.2+i*0.001,121.4+i*0.001) for i in range(count+1)]
    body = response(points)
    body['routes'][0]['distanceMeters'] = total
    for leg in body['routes'][0]['legs']:
        leg['distanceMeters'] = (total+delta)/count
    return body, points


def test_small_provider_total_difference_keeps_both_original_values():
    body, points = data(16775,10)
    original = deepcopy(body)
    legs = google.parse_response(body,points)
    assert body == original
    assert sum(leg['distance_m'] for leg in legs) == 16785
    assert legs[0]['distance_reconciliation'] == {
        'route_distance_m':16775,'leg_distance_m':16785,'difference_m':10,'tolerance_m':10}


@pytest.mark.parametrize('total,delta,count', [(16775,11,5),(2000,3,5),(100000,21,26)])
def test_large_or_relatively_large_difference_still_rejected(total,delta,count):
    body, points = data(total,delta,count)
    with pytest.raises(google.ValidationUnavailable,match='google_distance_reconciliation'):
        google.parse_response(body,points)


def test_distance_tolerance_does_not_relax_duration_or_endpoint():
    body, points = data(16775,10)
    body['routes'][0]['duration'] = '10000s'
    with pytest.raises(google.ValidationUnavailable,match='google_duration_reconciliation'):
        google.parse_response(body,points)
    body, points = data(16775,10)
    body['routes'][0]['legs'][0]['startLocation']['latLng']['latitude'] += 0.01
    with pytest.raises(google.ValidationUnavailable,match='google_pickup_snap_mismatch'):
        google.parse_response(body,points)
