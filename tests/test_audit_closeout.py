import json
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("count", [19, 23])
@pytest.mark.parametrize("bad_join", [False, True])
def test_repartition_needs_native_evidence_at_every_join(count, bad_join):
    from test_native_waypoint_evidence import ChunkPlanner, evidence
    points = [(31.2+i*.002, 121.4) for i in range(count)]
    planner = ChunkPlanner()
    def context(p, chunk):
        snap = evidence.fetch_amap_itinerary(p, chunk)
        if len(chunk) == 18:
            snap.pop("waypoint_legs")
        if bad_join and chunk[0] != points[0] and len(chunk) > 3:
            for leg in snap.get("waypoint_legs", [])[:2]:
                leg["geometry"][0][0] += .01
        return snap
    def no_pairs(*args):
        raise ValueError("No stored pair receipt")
    state = {"api_call_limit": 50}
    result = evidence.measure_amap_route(planner, points, {}, state, fetch_leg=no_pairs, fetch_context=context)
    assert state["api_calls"] <= 50
    assert result["continuous_measurement"]["prior_chunk_attempts"][0][0]["reason"] == "native_boundaries_unavailable"
    if bad_join:
        assert result["status"] != "verified"
        assert result["continuous_measurement"]["chunks"][-1]["reason"] == "continuous_chunk_join_unverified"
    else:
        assert result["status"] == "verified" and result["duration_s"] == (count-1)*60
        assert len(result["legs"]) == len(points)-1
        assert all(leg["destination"] == pytest.approx(dst) for leg, dst in zip(result["legs"], points[1:]))
        assert all(c["overlap_resolution"]["status"] == "confirmed" for c in result["continuous_measurement"]["chunks"][1:])


def test_failed_return_saves_only_its_own_receipt(monkeypatch):
    from test_paired_return import source, reverse_payload, run_return_measurement, NOW
    import direct_school_analysis as analysis
    rejected = {"status":"needs_review","complete":False,"issues":[{"code":"stop_turnaround_needs_review","leg_index":1}],
                "legs":[{"leg_index":0}],"continuous_measurement":{"status":"unavailable","chunks":[]}}
    class Provider:
        state = {"api_calls":0, "last_route_evidence":{"wrong_route":True}}
        def route(self,points):
            raise analysis.RouteMeasurementRejected(rejected)
    monkeypatch.setattr(analysis,"_osrm_leg",lambda *a:{})
    result = run_return_measurement(reverse_payload(source()), {"stop_service_minutes":1},
                                    provider=Provider(), now=lambda:NOW)
    r=result["routes"][0]
    assert r["status"]=="failed" and result["status"]=="partial" and not r["under_duration_limit"]
    assert r["rejected_route_evidence"]==rejected and "total_duration_s" not in r and "route_evidence" not in r
    monkeypatch.setattr(analysis,"_osrm_leg",lambda *a:(_ for _ in ()).throw(ValueError("before request")))
    r=run_return_measurement(reverse_payload(source()), {"stop_service_minutes":1},provider=Provider(), now=lambda:NOW)["routes"][0]
    assert "rejected_route_evidence" not in r


def test_distinct_service_days_do_not_hide_repeat_runs():
    from test_operations_review import _job, operations_review
    a = _job("a", None, "2026-11-18T16:05:00+00:00", "Bus 1", 10)
    b = _job("b", None, "2026-11-19T07:05:00+08:00", "Bus 1", 20)
    res=operations_review.build_operations_review([a,b])
    assert res["qualified_day_count"]==1 and res["qualified_sample_count"]==2
    assert res["status"]=="insufficient_evidence"
    assert res["recommendation"]["sample_dates"]==["2026-11-19"]
    b["scheduled_start_at"]="2026-11-18T06:30:00+08:00"
    assert operations_review.build_operations_review([a,b])["qualified_day_count"]==2


def test_walk_zero_is_not_missing():
    import api_app
    assert [api_app._walking_limit(x) for x in (None,"",0,"0",1,500)]==[500,500,0,0,1,500]
    assert not api_app._insert_walkable_point({"lat":None,"lng":121})
    assert api_app._insert_walkable_point({"lat":31,"lng":121})


def test_history_failures_are_not_empty_or_public_tracebacks(tmp_path,monkeypatch):
    import api_app as api
    service=api.backend_service
    def fail(*a,**k):raise sqlite3.OperationalError("private/path")
    dead = SimpleNamespace(list_jobs=fail,get_job=fail,get_side_tool_run=fail,list_side_tool_runs=fail)
    monkeypatch.setattr(service,"_runtime_sqlite_store",lambda:dead)
    side=object.__new__(service.SideToolHistoryStore);side.tool_key="fleet"
    for f,args in ((service.JOB_STORE._list_jobs_unlocked,()),(service.JOB_STORE._load_job_unlocked,("x",)),
                   (side._list_summaries_unlocked,()),(side._load_record_unlocked,("x",))):
        with pytest.raises(service.HistoryReadError) as e:f(*args)
        response=api.history_read_error_handler(None,e.value)
        assert response.status_code==503 and json.loads(response.body)["code"]=="history_unavailable"
    body=json.loads(api.backend_unhandled_error_handler(None,RuntimeError("private/path")).body)
    assert set(body)=={"request_id","code","error"} and "private" not in str(body)


def test_actual_outbound_ai_body_is_a_projection(monkeypatch):
    import ai_audit as ai
    bad="private@example.test"
    sample={"job":{"owner_email":bad},"current_plan":{"route_count":2,"system_findings":[bad]},
            "run_parameters":{"time_window_end":bad,"minimum_vehicle_reduction":bad},
            "scenario_outcomes":[{"key":"time_constrained","status":"passed","route_count":2,"frozen_route_ids":["R1",bad],
            "final_time_impact_gate":{"status":bad,"violations":[{"address":bad}]}}]}
    monkeypatch.setattr(ai,"DEEPSEEK_API_KEY","test")
    monkeypatch.setattr(ai.DEEPSEEK_LIMITER,"wait",lambda:None)
    monkeypatch.setattr(ai,"build_ai_audit_payload",lambda *_:sample)
    out=[]
    monkeypatch.setattr(ai.requests,"post",lambda *a,**k:out.append(k["json"]) or SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"choices":[{"message":{"content":"OK"}}]}))
    ai.generate_ai_audit_report({"result":{"exists":True}},language=bad)
    assert bad not in json.dumps(out)
    fact=json.loads(out[0]["messages"][1]["content"].split("FACTS JSON:\n")[1])
    assert fact["scenario_outcomes"][0]["frozen_route_ids"]==["R1"] and fact["current_plan"]["route_count"]==2


def test_next_geocode_batch_reads_new_revision_and_respects_clear(tmp_path,monkeypatch):
    from brp_storage import keyed_cache as k
    import client_runtime as rt
    monkeypatch.setenv("BRP_KEYED_CACHE","1")
    path=tmp_path/"geocode_cache.json";old=k.load(path)
    monkeypatch.setattr(rt,"GEOCODE_CACHE",old)
    writer=k.load(path);writer["test"]={"revision":2};k.save(path,writer,lambda v:v)
    assert old=={} and rt.geocode_batch_cache()["test"]=={"revision":2}
    k.clear(path)
    assert rt.geocode_batch_cache()=={}
