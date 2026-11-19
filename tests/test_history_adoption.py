from io import BytesIO
import sqlite3
from openpyxl import load_workbook
import pytest

from test_school_bound_resources import schools, audit_api, headers
import api_app as api

BASE="/api/admin/history-adoption"
ADMIN=headers("admin")


def legacy_pack(store,prefix="old"):
    ids={p:prefix+"-"+p for p in ("am","pm")}
    for p,key in ids.items():
        rec={"job_id":key,"owner_email":"north@example.test","status":"succeeded","shared_with_all":p=="pm",
            "metadata":{"pair_id":prefix,"period":p,"paired_job_ids":ids,"job_name":"South history","job_kind":"route_audit"}}
        if p=="pm":
            rec["metadata"]["measurement_kind"]="pm_return"
            rec["result"]={"routes":[{"route_id":"Q","riders":1,"total_duration_s":3500,"status":"resolved","stops":[]}]}
        store.upsert_job(rec)
    g=store.assign_history_group("route_audit","north@example.test","Explicit "+prefix,[ids["am"],ids["pm"]])["group_id"]
    store.set_history_group_member("route_audit","north@example.test",g,"south@example.test","viewer")
    return ids,g


def post(c,op,body,h=ADMIN):
    return c.post(BASE+"/"+op,json=body,headers=h)


def test_preview_confirm_matches_actual_visibility_and_audits_rename(schools):
    c,store,_,_,a,b=schools
    ids,g=legacy_pack(store)
    req={"school":a,"selection":["job:"+ids["am"]]}
    target="/api/jobs/"+ids["pm"]+"/exports/return-routes"
    for who in ("north","south"):
        assert c.get(target,headers=headers(who)).status_code==404
        assert post(c,"preview",req,headers(who)).status_code==403
    p=post(c,"preview",req)
    assert p.status_code==200,p.text
    p=p.json();allrefs={"job:"+i for i in ids.values()}|{"folder:"+g}
    assert {r["ref"] for r in p["objects"]}==allrefs
    users={r["email"]:r for r in p["users"]}
    assert set(users["north@example.test"]["gained"])==allrefs
    assert set(users["south@example.test"]["legacy_lost"])==allrefs
    assert not users["south@example.test"]["lost"] and p["open_legacy"]
    assert not store.get_job(ids["am"])["metadata"].get("school_id")
    confirm={"token":p["token"],"confirmed":True}
    assert post(c,"confirm",{"token":p["token"]}).status_code==422
    assert post(c,"confirm",{**confirm,"school":b}).status_code==422
    assert post(c,"confirm",confirm,headers("north")).status_code==403
    response=post(c,"confirm",confirm)
    assert response.status_code==200,response.text
    assert response.json()["count"]==3
    assert post(c,"confirm",confirm).json()["already_applied"]
    assert load_workbook(BytesIO(c.get(target,headers=headers("north")).content))["Return routes"]["F6"].value==3500
    assert c.get(target,headers=headers("south")).status_code==404
    for who,status in (("north",200),("south",404)):
        assert c.get("/api/history-groups/route_audit/"+g,headers=headers(who)).status_code==status
    assert post(c,"preview",{"selection":["folder:"+g],"school":b}).status_code==409
    ren={"ref":"job:"+ids["pm"],"previous":"South history","name":"Reviewed pair"}
    assert post(c,"rename",ren,headers("north")).status_code==403
    assert post(c,"rename",ren).json()["count"]==2
    assert post(c,"rename",ren).status_code==409
    existing=c.patch("/api/history-items/route_audit/"+ids["am"],headers=ADMIN,json={"name":"Final"})
    assert existing.status_code==200,existing.text
    for i in ids.values():
        rec=store.get_job(i)
        assert (rec["metadata"]["job_name"],rec["metadata"]["school_id"],rec["owner_email"])==("Final",a,"north@example.test")
    logs=c.get(BASE+"/events",headers=ADMIN).json()["items"]
    assert len(logs)==7
    assert {x["actor"] for x in logs}=={"admin@example.test"}
    assert logs[-1]["before_value"] is None and logs[-1]["after_value"]==a
    assert logs[0]["before_value"]=="Reviewed pair" and logs[0]["after_value"]=="Final"
    assert c.get(BASE+"/events",headers=headers("south")).status_code==403


def test_stale_membership_acl_expired_ticket_and_actor(schools,monkeypatch):
    c,store,_,directory,a,_=schools
    ids,g=legacy_pack(store)
    req={"school":a,"selection":["folder:"+g]}
    make=lambda:{"token":post(c,"preview",req).json()["token"],"confirmed":True}
    t=make()
    monkeypatch.setattr(api.backend_service,"ADMIN_EMAILS",{"admin@example.test","second@example.test"})
    assert post(c,"confirm",t,headers("second")).status_code==404
    directory.change(a,"extra@example.test","viewer",None,"admin@example.test")
    assert post(c,"confirm",t).status_code==409
    t=make()
    store.remove_history_group_member("route_audit","north@example.test",g,"south@example.test")
    assert post(c,"confirm",t).status_code==409
    t=make()
    with store.connect() as db:db.execute("UPDATE history_adoption_tickets SET expires=0 WHERE token=?",(t["token"],))
    assert post(c,"confirm",t).status_code==409
    assert "school_id" not in store.get_job(ids["am"])["metadata"]


def test_unrelated_same_name_and_side_history(schools):
    c,store,_,_,a,b=schools
    for key in ("first","second"):
        store.upsert_side_tool_run("fleet_planner",{"run_id":key,"owner_email":"south@example.test","shared_with_all":True,"title":"Identical","summary":{}})
    p=post(c,"preview",{"school":b,"selection":["run:fleet_planner:second"]}).json()
    assert [r["ref"] for r in p["objects"]]==["run:fleet_planner:second"]
    assert post(c,"confirm",{"confirmed":True,"token":p["token"]}).status_code==200
    assert c.get("/api/fleet-planner/history/second",headers=headers("south")).status_code==200
    assert c.get("/api/fleet-planner/history/second",headers=headers("north")).status_code==404
    assert not store.get_side_tool_run("fleet_planner","first")["summary"].get("school_id")
    assert post(c,"rename",{"name":"New title","ref":"run:fleet_planner:second","previous":"Identical"}).json()["count"]==1
    assert store.get_side_tool_run("fleet_planner","second")["title"]=="New title"


def test_batch_rollback_busy_and_immutability(schools):
    c,store,_,_,a,b=schools
    ids,g=legacy_pack(store)
    req={"school":a,"selection":["job:"+ids["pm"]]}
    rec=store.get_job(ids["am"]);rec["status"]="scheduled";store.upsert_job(rec)
    assert post(c,"preview",req).status_code==409
    rec["status"]="succeeded";store.upsert_job(rec)
    t=post(c,"preview",req).json()["token"]
    with store.connect() as db:
        db.execute("""CREATE TRIGGER qa_abort BEFORE UPDATE OF metadata_json ON jobs WHEN OLD.job_id='old-pm'
            BEGIN SELECT RAISE(ABORT,'fixture rollback'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        api._history_admin().confirm("admin@example.test",t)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM history_school_grants").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM history_admin_events").fetchone()[0]==0
        assert db.execute("SELECT 1 FROM school_folders WHERE group_id=?",(g,)).fetchone() is None
        db.execute("DROP TRIGGER qa_abort")
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_job(dict(rec,metadata=dict(rec["metadata"],school_id=b)))
    assert post(c,"confirm",{"confirmed":True,"token":t}).status_code==200
    assert "school_id" in store.get_job(ids["am"])["metadata"]


def test_orphan_return_and_record_mismatch_fail_closed(schools):
    c,store,_,_,a,_=schools
    ids,g=legacy_pack(store)
    rec=store.get_job(ids["pm"])
    original=rec["metadata"].copy()
    rec["metadata"].pop("pair_id")
    store.upsert_job(rec)
    req={"school":a,"selection":["job:"+ids["pm"]]}
    assert post(c,"preview",req).status_code==409
    rec["metadata"]=original
    store.upsert_job(rec)
    token=post(c,"preview",req).json()["token"]
    with store.connect() as db:
        db.execute("UPDATE jobs SET record_json=json_set(record_json,'$.metadata.school_id','unexpected') WHERE job_id=?",(ids["pm"],))
    assert post(c,"confirm",{"token":token,"confirmed":True}).status_code==409
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM history_admin_events").fetchone()[0]==0
        assert db.execute("SELECT 1 FROM school_folders WHERE group_id=?",(g,)).fetchone() is None
