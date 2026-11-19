from copy import deepcopy
from io import BytesIO
import sqlite3

from openpyxl import load_workbook
import pytest

from test_audit_paired_return import audit_api
import api_app as api
from school_directory import Directory
from audit_return import related_pair_records


@pytest.fixture
def schools(audit_api,tmp_path,monkeypatch):
    client,store,payload,_,_=audit_api
    api.app.dependency_overrides.clear()
    db=Directory(tmp_path/"memberships.db")
    db.initialize()
    a=db.create("North","north@example.test","admin@example.test")["id"]
    b=db.create("South","south@example.test","admin@example.test")["id"]
    db.change(a,"shared@example.test","operator",None,"admin@example.test")
    db.change(b,"shared@example.test","operator",None,"admin@example.test")
    db.change(a,"view@example.test","viewer",None,"admin@example.test")
    monkeypatch.setenv("BRP_SCHOOL_DIRECTORY_DB",str(db.path))
    monkeypatch.setenv("BRP_SCHOOL_SCOPES","1")
    monkeypatch.setattr(api.backend_service,"ADMIN_EMAILS",{"admin@example.test"})
    monkeypatch.setattr(api.backend_service,"AUTH_PROVIDER","cloudflare_header")
    monkeypatch.setattr(api.backend_service,"SERVICE_TOKEN","qa-only")
    monkeypatch.setattr(api.backend_service,"RUNTIME_DB_PATH",store.db_path)
    return client,store,payload,db,a,b


def headers(user,school=None):
    h={"Authorization":"Bearer qa-only","X-BRP-User-Email":user+"@example.test"}
    if school:h["X-BRP-School"]=school
    return h


def test_actual_submission_binds_pair_and_scopes_idempotency(schools):
    c,store,payload,_,a,b=schools
    submit=lambda user,scope=None:c.post("/api/workbooks/submit",headers=headers(user,scope),json=payload)
    assert submit("shared").status_code==422
    assert submit("view").status_code==403
    assert submit("unknown").status_code==403
    assert submit("north",b).status_code==403
    assert not store.list_jobs(include_all=True)
    for user,school,explicit in [("north",a,None),("shared",a,a),("shared",b,b)]:
        resp=submit(user,explicit)
        assert resp.status_code==202,resp.text
        pair=resp.json()["paired_jobs"]
        am,pm=[store.get_job(j["job_id"]) for j in pair]
        assert {am["metadata"]["school_id"],pm["metadata"]["school_id"]}=={school}
        assert {r["job_id"] for r in related_pair_records(pm,store.get_job)}=={am["job_id"],pm["job_id"]}
        assert submit(user,explicit).json()["paired_jobs"][1]["job_id"]==pm["job_id"]
    assert len(store.list_jobs(include_all=True))==6
    altered=deepcopy(am)
    altered["metadata"]["school_id"]=a
    with pytest.raises(ValueError,match="identity mismatch"):
        related_pair_records(pm,lambda jid: altered if jid==am["job_id"] else pm)
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_job(altered)
    with pytest.raises(ValueError,match="same school"):
        store.insert_job_pair([altered,pm])


def test_files_lists_exports_and_revocation(schools):
    c,store,payload,db,a,b=schools
    pair=c.post("/api/workbooks/submit",headers=headers("north"),json=payload).json()["paired_jobs"]
    am,pm=[j["job_id"] for j in pair]
    record=store.get_job(pm)
    record.update(status="succeeded",shared_with_all=True,result={"analysis_kind":"pm_return","routes":[
        {"route_id":"R1","status":"resolved","riders":2,"total_duration_s":3599,"stops":[]}]})
    store.upsert_job(record)
    folders="/api/history-groups/route_audit"
    resp=c.post(folders,headers=headers("north"),json={"name":"Ops","item_ids":[am,pm]})
    assert resp.status_code==200,resp.text
    group=folders+"/"+resp.json()["group"]["group_id"]
    assert c.put(group+"/members/view@example.test",headers=headers("north"),json={"role":"viewer"}).status_code==200
    assert c.put(group+"/members/south@example.test",headers=headers("north"),json={"role":"viewer"}).status_code==403
    export=f"/api/jobs/{pm}/exports/return-routes"
    for allowed in ["north","view"]:
        assert c.get(group,headers=headers(allowed)).status_code==200
        data=c.get(export,headers=headers(allowed))
        assert data.status_code==200 and load_workbook(BytesIO(data.content))["Return routes"]["F6"].value==3599
    for link in [group,export,f"/api/jobs/{am}",f"/api/jobs/{pm}",f"/api/jobs/{am}/artifacts/current",
                 f"/api/jobs/{am}/map-data/current",f"/api/jobs/{pm}/measurement-reviews"]:
        assert c.get(link,headers=headers("south")).status_code==404,link
    for link,key in [(folders,"groups"),("/api/jobs","jobs")]:
        assert c.get(link,headers=headers("south")).json()[key]==[]
    assert c.patch(group,headers=headers("view"),json={"name":"Other"}).status_code==403
    db.change(a,"view@example.test",None,"viewer","admin@example.test")
    assert c.get(export,headers=headers("view")).status_code==404
    assert c.get(group,headers=headers("view")).status_code==404
    assert c.get(export,headers={"Authorization":"Bearer qa-only"}).status_code==401


def test_side_history_and_mixed_folder(schools):
    c,store,payload,_,a,b=schools
    path="/api/fleet-planner/history"
    body={"title":"Synthetic fleet","summary":{"school_id":b},"preview_result":{"summary":{"total_riders":2}},"global_plan_result":{"routes":[]}}
    added=c.post(path,headers=headers("north"),json=body)
    assert added.status_code==201,added.text
    rid=added.json()["job"]["run_id"]
    saved=store.get_side_tool_run("fleet_planner",rid)
    assert saved["summary"]["school_id"]==a
    store.upsert_side_tool_run("fleet_planner",{**saved,"shared_with_all":True})
    assert c.get(path+"/"+rid,headers=headers("north")).status_code==200
    assert c.get(path+"/"+rid,headers=headers("south")).status_code==404
    assert rid not in c.get(path,headers=headers("south")).text
    ids=[]
    for school in (a,b):
        ids.append(c.post("/api/workbooks/submit",headers=headers("shared",school),json=payload).json()["job"]["job_id"])
    folder="/api/history-groups/route_audit"
    assert c.post(folder,headers=headers("shared"),json={"name":"mixed","item_ids":ids}).status_code==409


def test_scoped_local_identity_never_uses_fallback(schools,monkeypatch):
    c,*_=schools
    monkeypatch.setattr(api.backend_service,"AUTH_PROVIDER","local")
    monkeypatch.setattr(api.backend_service,"DEV_USER_EMAIL","admin@example.test")
    assert c.get("/api/jobs",headers={"Authorization":"Bearer qa-only","Cf-Access-Authenticated-User-Email":"north@example.test"}).status_code==401


def test_runtime_guard_rejects_shared_or_preexisting_state(tmp_path,monkeypatch):
    import school_scope
    root=tmp_path/"isolated"
    root.mkdir()
    monkeypatch.setenv("BRP_SCHOOL_SCOPES","1")
    monkeypatch.setenv("BRP_SCHOOL_DATA_ROOT",str(root))
    for key in ("BRP_RUNTIME_DB_PATH","BRP_SCHOOL_DIRECTORY_DB","BRP_BACKEND_JOBS_DIR","BRP_SIDE_TOOLS_DIR"):
        monkeypatch.setenv(key,str(root/key))
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH",str(tmp_path/"outside.db"))
    with pytest.raises(RuntimeError,match="inside"):
        school_scope.check_runtime()
    db=root/"existing.db";db.touch()
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH",str(db))
    with pytest.raises(RuntimeError,match="unmarked"):
        school_scope.check_runtime()
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH",str(root/"new.db"))
    school_scope.check_runtime()
    monkeypatch.delenv("BRP_SCHOOL_SCOPES")
    with pytest.raises(RuntimeError,match="requires"):
        school_scope.check_runtime()
