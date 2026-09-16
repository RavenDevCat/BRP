from copy import deepcopy
from datetime import timedelta
import sys

import pytest
from test_google_final_validation import client, NOW, POINTS, response
from test_google_timing_entrypoints import context, CONFIG
import google_final_validation as g
import google_pickup_points as pickup
import direct_school_analysis as direct
import backend_job_runner as runner
from test_direct_school_analysis import prepared_payload, fake_osrm
from test_measurement_review_api import fixture


def school():
    return {'provider':'amap', 'amap_poi_id':'synthetic-school', 'amap_poi_type':'\u79d1\u6559;\u5b66\u6821',
        'amap_poi_location':'121.4,31.2', 'amap_poi_entr_location':'121.3999,31.2',
        'address':'Test school', 'requested_address':'Test school', 'is_depot':True,
        'lat':31.2,'lng':121.4,'plot_lat':31.2,'plot_lng':121.4,'country':'China'}


def test_school_entrance_is_idempotent_and_does_not_mutate_source():
    original=school(); saved=deepcopy(original)
    normalized=pickup.normalize_point(original)
    assert original==saved
    assert normalized['pickup_entrance_source']=='provider_entr_location'
    assert normalized['lng']==121.3999
    assert normalized==pickup.normalize_point(normalized)


@pytest.mark.parametrize('change', [
    {'pickup_override_revision':3}, {'pickup_precision_status':'operator_confirmed'},
    {'pickup_entrance_source':'named_gate_poi'}, {'requested_address':'School \u4e1c\u95e8'},
    {'amap_poi_type':'\u516c\u4ea4\u7ad9'}, {'amap_poi_entr_location':'nan,31.2'},
    {'amap_poi_entr_location':'121.9,31.2'}, {'amap_poi_entr_location':''}, {'provider':'manual'}])
def test_confirmed_gates_other_stops_and_invalid_entrances_are_preserved(change):
    point={**school(),**change}
    assert pickup.normalize_point(point)==point


def test_snap_error_carries_requested_returned_point_and_leg():
    payload=response(POINTS)
    payload['routes'][0]['legs'][1]['endLocation']['latLng']['latitude']+=0.003
    with pytest.raises(g.ValidationUnavailable) as caught:
        g.parse_response(payload,POINTS)
    details=caught.value.details
    assert str(caught.value)=='google_pickup_snap_mismatch'
    assert details['point_index']==2 and details['leg_index']==1 and details['endpoint']=='end'
    assert details['snap_distance_m']>100 and details['limit_m']==100


def test_rolling_leg_failure_reports_global_point_index(client):
    def transport(body):
        from test_google_final_validation import body_points
        p=body_points(body); payload=response(p)
        if p[0]==POINTS[1]:
            payload['routes'][0]['legs'][0]['endLocation']['latLng']['latitude']+=0.003
        return payload
    client.transport=transport
    with pytest.raises(g.ValidationUnavailable) as caught:
        g.ValidationSession(client).measure(POINTS,NOW+timedelta(days=1),[60,60,0])
    assert caught.value.details['point_index']==2 and caught.value.details['leg_index']==1


def test_google_context_uses_school_entrance_and_keeps_provenance(context):
    origin={**school(),'is_depot':False,'address':'Origin','amap_poi_type':'bus stop'}
    dest=school(); normalized=pickup.normalize_point(dest)
    e=context.route([origin,dest], reference_legs=[{'duration_s':600}])
    assert g.meters(e['legs'][-1]['end'],(normalized['plot_lat'],normalized['plot_lng']))<0.01
    assert e['requested_waypoints'][-1]['pickup_entrance_source']=='provider_entr_location'
    assert dest.get('pickup_entrance_source') is None


def test_failed_worker_retains_checkpoint_and_diagnostics(monkeypatch):
    state={'record':{'job_id':'checkpoint-test','status':'queued','prepared_payload':{},
        'metadata':{'job_kind':'direct_school_analysis','analysis_config':CONFIG}}}
    partial={'status':'running','provider':'google_routes','stops':[{'address':'First','provider_status':'resolved'}],
        'progress':{'completed':1,'provider_api_calls':2}}
    def run(*args,**kwargs):
        kwargs['checkpoint'](deepcopy(partial))
        raise g.ValidationUnavailable('google_pickup_snap_mismatch',details={'address':'Second','snap_distance_m':238})
    monkeypatch.setattr(runner,'_load_job',lambda _:deepcopy(state['record']))
    monkeypatch.setattr(runner,'_save_job',lambda r:state.update(record=deepcopy(r)))
    monkeypatch.setattr(runner,'_release_concurrency_slot',lambda:None)
    monkeypatch.setattr(runner,'run_direct_school_analysis',run)
    monkeypatch.setattr(sys,'argv',['backend_job_runner.py','checkpoint-test'])
    assert runner.main()==1
    saved=state['record']
    assert saved['status']==saved['result']['status']=='failed'
    assert saved['result']['stops']==partial['stops']
    assert saved['metadata']['failure_details']['address']=='Second'
    with pytest.raises(ValueError,match='not available'):
        direct.build_direct_school_workbook(saved)


def test_direct_failure_checkpoint_records_failed_point(context,monkeypatch):
    monkeypatch.setattr(direct,'_osrm_leg',fake_osrm)
    payload=prepared_payload()
    for p in payload['original_points']:
        p.update(plot_lat=p['lat'],plot_lng=p['lng'])
    def fail(*args,**kwargs):
        context.state['api_calls']=1
        raise g.ValidationUnavailable('google_pickup_snap_mismatch',details={'point_index':0,'address':'Failed stop','endpoint':'start','snap_distance_m':238})
    monkeypatch.setattr(context,'route',fail)
    checkpoints=[]
    result=direct.run_direct_school_analysis(payload,CONFIG,timing_context=context,checkpoint=lambda v:checkpoints.append(deepcopy(v)))
    assert result['status']=='partial' and result['measurement_attempts_complete'] is True
    assert result['operational_conclusion']['final']['all_measured_routes_within_window'] is False
    assert checkpoints[-1]['errors'][0]['address']=='Failed stop'
    assert checkpoints[-1]['progress']['provider_api_calls']==1
    assert any(s['provider_status']=='failed' for s in checkpoints[-1]['stops'])


def test_shared_budget_estimate_matches_rolling_dwell():
    plan={'stops':[{'route_id':'R1','country':'China','city':'Shanghai','address':'A','is_depot':False},
                   {'route_id':'R1','country':'China','city':'Shanghai','address':'B','is_depot':False},
                   {'route_id':'R1','country':'China','city':'Shanghai','address':'S','is_depot':True}]}
    assert direct.google_request_estimate(plan,1)['minimum_calls']==4
    assert direct.google_request_estimate(plan,0)['minimum_calls']==3


def test_quota_endpoint_is_authenticated_and_read_only(fixture,monkeypatch):
    api,store,schedule=fixture
    monkeypatch.setattr(g,'availability',lambda:{'available':True,'reason':None})
    monkeypatch.setattr(g,'monthly_budget',lambda:{'month':'2030-01','limit':10000,'used':98,'remaining':9902,'timezone':'Asia/Shanghai'})
    assert api.get('/api/google-validation/quota',headers={'Authorization':'Bearer wrong'}).status_code==401
    reply=api.get('/api/google-validation/quota')
    assert reply.status_code==200 and reply.json()['quota']['remaining']==9902
    schedule.assert_not_called()


def test_disabled_quota_endpoint_does_not_read_store(fixture,monkeypatch):
    api,store,schedule=fixture
    monkeypatch.setattr(g,'availability',lambda:{'available':False,'reason':'google_rollout_disabled'})
    monkeypatch.setattr(g,'monthly_budget',lambda:pytest.fail('Disabled rollout read quota store'))
    assert api.get('/api/google-validation/quota').json()['quota'] is None
    schedule.assert_not_called()


def test_fleet_school_marker_matches_requested_entrance(context):
    import backend_service as backend
    fleet=backend._client_module('demand_routing')
    origin={**school(),'amap_poi_type':'bus stop','is_depot':False,'order':0,'id':'a'}
    dest={**school(),'order':1,'id':'school'}
    route={'cluster_id':'R1','ordered_points':[origin,dest],'duration_s':600,'distance_m':1000,
           'selected_vehicle':{'student_capacity':15},'leg_details':[{'duration_s':600}]}
    plan={'school':{'country':'China'},'summary':{'service_direction':'to_school','max_route_duration_minutes':90},
          'routes':[route],'route_rows':[{'cluster_id':'R1'}]}
    backend._attach_fleet_route_measurements(plan,fleet,measurement_provider=context)
    p=pickup.normalize_point(dest)
    assert route['ordered_points'][-1]['lat']==p['plot_lat']
    assert route['ordered_points'][-1]['lng']==p['plot_lng']
