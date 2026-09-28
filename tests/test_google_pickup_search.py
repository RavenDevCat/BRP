from copy import deepcopy
import pytest
from google_pickup_points import _lookup, _parent_poi_id, _select_captured_pickup
from amap_geocode_quality import GeocodePrecisionError
import client_runtime as runtime

ROAD='\u6d4b\u8bd5\u8def555\u5f04'
POINT={'address':ROAD,'city':'Shanghai','country':'China'}

def test_unique_parent_requires_exact_road_number_and_no_gate():
    rows=[('/v3/place/text',{'pois':[{'id':'child','parent':'B0012345','address':ROAD+'45\u53f7'}]})]
    assert _parent_poi_id(POINT,rows)=='B0012345'
    assert _parent_poi_id({**POINT,'address':ROAD+'\u5357\u95e8'},rows) is None
    assert _parent_poi_id({**POINT,'address':ROAD+'\u5bf9\u9762'},rows) is None
    assert _parent_poi_id({**POINT,'address':'\u6d4b\u8bd5\u8def55\u5f04'},rows) is None
    rows[0][1]['pois'].append({'id':'other','parent':'B0023456','address':ROAD+'46\u53f7'})
    assert _parent_poi_id(POINT,rows) is None

@pytest.mark.parametrize('nested',[False,True])
def test_parent_resolution_is_bounded_exact_lookup(monkeypatch,nested):
    monkeypatch.setattr(runtime,'is_plausible_geocode_result',lambda *a,**kw:True)
    child={'id':'child','parent':'B0012345','name':'Tenant','address':ROAD+'45\u53f7',
           'location':'121.4,31.2','type':'\u8d2d\u7269\u670d\u52a1'}
    parent={'id':'B0012345','name':'Garden','alias':ROAD,'address':'\u53e6\u4e00\u8def',
            'type':'\u4f4f\u5b85\u533a','location':'121.4,31.2','entr_location':'121.4001,31.2001'}
    calls=[]
    def request(endpoint,params,limiter):
        calls.append((endpoint,params))
        if endpoint=='/v3/place/detail':
            assert params['id'] in {'B0012345','B0023456'} and params['extensions']=='all'
            if nested and params['id']=='B0012345':
                return {'pois':[{'id':'B0012345','name':'Subarea','parent':'B0023456',
                                 'location':'121.4,31.2','address':'\u53e6\u4e00\u8def'}]}
            return {'pois':[{**deepcopy(parent),'id':params['id']}]}
        return {'pois':[deepcopy(child)]} if endpoint=='/v3/place/text' else {'geocodes':[]}
    monkeypatch.setattr(runtime,'amap_request_json',request)
    counts=[]
    result=_lookup(POINT,lambda:None,lambda:counts.append(1))
    assert result['amap_poi_id']==('B0023456' if nested else 'B0012345')
    assert result['pickup_entrance_source']=='provider_entr_location'
    assert len(calls)==len(counts)==(4 if nested else 3)

def test_unique_poi_is_preferred_but_multiple_pois_are_not_hidden(monkeypatch):
    monkeypatch.setattr(runtime,'is_plausible_geocode_result',lambda *a,**kw:True)
    address='\u6d4b\u8bd5\u8def\u53e6\u4e00\u8def\u53e3'
    point={**POINT,'address':address}
    poi={'id':'junction','name':'\u6d4b\u8bd5\u8def\u4e0e\u53e6\u4e00\u8def\u4ea4\u53c9\u53e3','location':'121.4,31.2'}
    geo={'formatted_address':address,'level':'\u9053\u8def\u4ea4\u53c9\u8def\u53e3','location':'121.4001,31.2001'}
    rows=[('/v3/place/text',{'pois':[poi]}),('/v3/geocode/geo',{'geocodes':[geo]})]
    assert _select_captured_pickup(point,rows)['amap_poi_id']=='junction'
    rows[0][1]['pois'].append({**poi,'id':'other','location':'121.401,31.201'})
    with pytest.raises(GeocodePrecisionError):
        _select_captured_pickup(point,rows)


def test_intersection_query_excludes_nearby_tenants_and_preserves_primary_road():
    from google_pickup_points import _prefer_intersection
    address='\u6d4b\u8bd5\u8def\u53e6\u4e00\u8def\u53e3'
    forward={'id':'forward','name':'\u6d4b\u8bd5\u8def\u4e0e\u53e6\u4e00\u8def\u4ea4\u53c9\u53e3'}
    reverse={'id':'reverse','name':'\u53e6\u4e00\u8def\u4e0e\u6d4b\u8bd5\u8def\u4ea4\u53c9\u53e3'}
    tenant={'id':'tenant','name':'Bank','address':address}
    assert _prefer_intersection(address,[tenant,reverse,forward])==[forward]
    assert _prefer_intersection(address,[tenant])==[]


def test_explicit_child_address_resolves_parent_entrance_without_inventing_alias(monkeypatch):
    monkeypatch.setattr(runtime,'is_plausible_geocode_result',lambda *a,**kw:True)
    parent={'id':'B0012345','name':'Garden','address':'\u6d4b\u8bd5\u8def555\u53f7',
            'type':'\u5546\u52a1\u4f4f\u5b85;\u4f4f\u5b85\u533a','location':'121.4,31.2','entr_location':'121.4001,31.2001'}
    child={'id':'child','parent':parent['id'],'name':'Residents office','address':ROAD+'16\u53f7',
           'location':'121.4002,31.2002','type':'\u751f\u6d3b\u670d\u52a1'}
    receipts=[('/v3/place/text',{'pois':[child]}),('/v3/place/detail',{'pois':[parent]})]
    result=_select_captured_pickup(POINT,receipts)
    assert result['amap_poi_id']==parent['id']
    assert result['amap_poi_address']==parent['address']
    assert not result['amap_poi_alias']
    assert result['amap_parent_address_evidence'][0]['parent']==parent['id']
    assert result['pickup_entrance_source']=='provider_entr_location'
    assert result['lat']==31.2001


@pytest.mark.parametrize('change',[
    {'address':ROAD+'\u5357\u95e8'}, {'address':ROAD+'\u5bf9\u9762'},
    {'amap_poi_type':'\u8d2d\u7269\u670d\u52a1'}, {'amap_poi_entr_location':''},
    {'amap_poi_id':'other'}, {'amap_poi_location':'121.5,31.3'},
    {'address':'\u6d4b\u8bd5\u8def55\u5f04'},
])
def test_parent_relationship_cannot_override_gate_number_or_distant_identity(change):
    from google_pickup_points import _verified_parent_address
    value={**POINT,'amap_poi_id':'parent','amap_poi_type':'\u4f4f\u5b85\u533a',
           'amap_poi_location':'121.4,31.2','amap_poi_entr_location':'121.4001,31.2001',
           'amap_parent_address_evidence':[{'id':'child','parent':'parent','address':ROAD+'16\u53f7',
                                            'location':'121.4002,31.2002'}]}
    assert not _verified_parent_address({**value,**change})
