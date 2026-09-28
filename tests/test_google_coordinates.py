from copy import deepcopy
from datetime import timedelta

import pytest
import google_coordinates as coordinates
import google_final_validation as google
from test_google_final_validation import NOW, POINTS, response, body_points, client


@pytest.mark.parametrize('country,city,expected',[
    ('China','Shanghai',coordinates.SHANGHAI),('CN','上海市',coordinates.SHANGHAI),
    ('中国','上海',coordinates.SHANGHAI),('China','Beijing',coordinates.WGS84),
    ('Korea','Seoul',coordinates.WGS84),('China','',coordinates.WGS84),
    ('Thailand','Shanghai',coordinates.WGS84)])
def test_market_scope_is_explicit(country,city,expected):
    assert coordinates.profile_for(country,city)==expected


def test_normalization_is_copy_only_and_preserves_native_measurements():
    original=response(POINTS)
    saved=deepcopy(original)
    normalized=coordinates.normalize_response(original,coordinates.SHANGHAI)
    assert original==saved
    assert normalized['routes'][0]['duration']==original['routes'][0]['duration']
    assert normalized['routes'][0]['distanceMeters']==original['routes'][0]['distanceMeters']
    for before,after in zip(original['routes'][0]['legs'],normalized['routes'][0]['legs']):
        assert before['duration']==after['duration'] and before['distanceMeters']==after['distanceMeters']
        assert google.location(after['startLocation'])==coordinates.to_wgs84(google.location(before['startLocation']),coordinates.SHANGHAI)
        assert after['polyline']['geoJsonLinestring']['coordinates'][0]==list(google.location(after['startLocation'])[::-1])


def test_native_geocode_to_wire_roundtrip_and_canonical_evidence(client):
    canonical=[coordinates.to_wgs84(point,coordinates.SHANGHAI) for point in POINTS]
    client.configure_coordinates([{'google_coordinate_profile':coordinates.SHANGHAI}]*3)
    sent=[]
    def transport(body):
        sent.append(deepcopy(body))
        return response(body_points(body))
    client.transport=transport
    legs=client.route(canonical,NOW+timedelta(days=1))
    for actual,raw in zip(body_points(sent[0]),POINTS):
        assert google.meters(actual,raw)<2
    for leg,expected in zip(legs,canonical):
        assert google.meters(leg['start'],expected)<2
        assert leg['coordinate_system']=='WGS84'
        assert leg['provider_coordinate_system']=='GCJ02'
        assert leg['coordinate_profile']==coordinates.SHANGHAI
    assert client.route(canonical,NOW+timedelta(days=1))==legs
    assert client.calls==1 and client.cache_hits==1


def test_cache_key_separates_coordinate_profiles(client):
    date=NOW+timedelta(days=1)
    client.route(POINTS,date)
    client.configure_coordinates([{'google_coordinate_profile':coordinates.SHANGHAI}]*3)
    client.route(POINTS,date)
    assert client.calls==2 and client.cache_hits==0


@pytest.mark.parametrize('profiles',[[coordinates.WGS84,coordinates.SHANGHAI],['unknown']])
def test_mixed_or_unknown_frames_fail_before_paid_call(client,profiles):
    with pytest.raises(google.ValidationUnavailable,match='coordinate_profile_mismatch'):
        client.configure_coordinates([{'google_coordinate_profile':x} for x in profiles])
    assert client.calls==0


def test_invalid_profile_or_coordinates_rejected():
    with pytest.raises(ValueError): coordinates.to_wire((31,121),'bad')
    with pytest.raises(ValueError): coordinates.to_wgs84((float('nan'),121),coordinates.SHANGHAI)


def test_unverified_markets_keep_existing_coordinate_contract():
    assert coordinates.to_wire((37.5,127),coordinates.WGS84)==(37.5,127)
    assert coordinates.to_wgs84((39.9,116.4),coordinates.WGS84)==(39.9,116.4)
    native=response(POINTS)
    assert coordinates.normalize_response(native,coordinates.WGS84) is native


def test_cross_request_join_checks_use_canonical_coordinates(client):
    client.configure_coordinates([{'google_coordinate_profile':coordinates.SHANGHAI}]*3)
    canonical=[coordinates.to_wgs84(point,coordinates.SHANGHAI) for point in POINTS]
    measured=google.ValidationSession(client).measure(canonical,NOW+timedelta(days=1),[0,60,0])
    assert client.calls==2 and measured.dwell_s==60
    assert google.meters(measured.legs[0]['end'],measured.legs[1]['start'])<0.01
    assert measured.legs[1]['coordinate_system']=='WGS84'
