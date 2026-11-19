from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
for directory in ("apps","apps/backend","apps/client"):
    sys.path.insert(0,str(ROOT/directory))
import api_app as api
from school_directory import Directory, DirectoryError
from runtime_store_sqlite import SqliteRuntimeStore


@pytest.fixture
def db(tmp_path):
    result=Directory(tmp_path/"schools.sqlite")
    result.initialize()
    return result


def test_schools_and_isolated_memberships(db):
    a=db.create("North School","a@example.test","sys@example.test")["id"]
    b=db.create("South School","b@example.test","sys@example.test")["id"]
    db.change(a,"ops@example.test","operator",None,"sys@example.test")
    db.change(b,"OPS@example.test","viewer",None,"sys@example.test")
    assert {s["name"]:s["role"] for s in db.mine("ops@example.test")}=={"North School":"operator","South School":"viewer"}
    assert [s["id"] for s in db.mine("b@example.test")]==[b]
    db.change(a,"ops@example.test",None,"operator","sys@example.test")
    assert [s["id"] for s in db.mine("ops@example.test")]==[b]
    assert not db.mine("new@example.test")
    change=db.snapshot()["changes"][0]
    assert (change["previous"],change["current"],change["actor"])==("operator",None,"sys@example.test")
    with pytest.raises(DirectoryError,match="exists"):db.create("north school","a@example.test","sys@example.test")


def test_conflict_and_last_manager_are_atomic(db):
    a=db.create("School","a@example.test","sys@example.test")["id"]
    with pytest.raises(DirectoryError,match="at least one"):db.change(a,"a@example.test","viewer","school_admin","sys@example.test")
    db.change(a,"b@example.test","school_admin",None,"sys@example.test")
    def remove(who):
        try:db.change(a,who,None,"school_admin","sys@example.test");return 200
        except DirectoryError as exc:return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(remove,["a@example.test","b@example.test"]))==[200,409]
    assert len(db.snapshot()["members"])==1
    count=len(db.snapshot()["changes"])
    with pytest.raises(DirectoryError,match="changed"):db.change(a,"x@example.test","operator","viewer","sys@example.test")
    assert len(db.snapshot()["changes"])==count


@pytest.fixture
def http(db,tmp_path,monkeypatch):
    live=tmp_path/"legacy.sqlite"
    store=SqliteRuntimeStore(live)
    store.upsert_job({"job_id":"x","owner_email":"ops@example.test","status":"scheduled","metadata":{},"shared_with_all":False})
    store.upsert_job({"job_id":"y","owner_email":"b@example.test","status":"succeeded","metadata":{},"shared_with_all":True})
    with store.connect() as con:
        con.execute("insert into history_groups values('folder','route_audit','b@example.test','Folder','now','now')")
        con.execute("insert into history_group_members(group_id,member_email,role,created_at,updated_at) values('folder','ops@example.test','viewer','now','now')")
    monkeypatch.setenv("BRP_SCHOOL_DIRECTORY_DB",str(db.path))
    monkeypatch.setattr(api.backend_service,"RUNTIME_DB_PATH",live)
    monkeypatch.setattr(api.backend_service,"_RUNTIME_SQLITE_STORE",store)
    monkeypatch.setattr(api.backend_service,"ADMIN_EMAILS",{"sys@example.test"})
    monkeypatch.setattr(api.backend_service,"AUTH_PROVIDER","local")
    monkeypatch.setattr(api.backend_service,"SERVICE_TOKEN","test-only")
    # No app lifespan: this test must not start a real scheduler.
    return TestClient(api.app),store


def hdr(email):
    return {"Authorization":"Bearer test-only","X-BRP-User-Email":email}


def test_admin_flow_never_grants_legacy_access(http,db,monkeypatch):
    client,legacy=http
    admin=hdr("sys@example.test");ops=hdr("ops@example.test")
    path="/api/admin/school-directory"
    old=legacy.get_job("x")
    assert client.get(path,headers=ops).status_code==403
    assert client.get(path).status_code==401
    resp=client.post(path+"/schools",headers=admin,json={"name":"North School","manager":"sys@example.test"})
    assert resp.status_code==201
    change=path+"/schools/"+resp.json()["id"]+"/membership"
    body={"email":"ops@example.test","role":"operator","previous":None}
    assert client.put(change,headers=ops,json=body).status_code==403
    assert client.put(change,headers=admin,json=body).status_code==200
    assert client.put(change,headers=admin,json=body).status_code==409
    assert client.put(change,headers=admin,json={"email":"ops@example.test"}).status_code==422
    assert client.get("/api/me/school-workspaces",headers=ops).json()["schools"][0]["role"]=="operator"
    out=client.get(path,headers=admin)
    assert out.headers["cache-control"]=="private, no-store"
    user=next(u for u in out.json()["users"] if u["email"]=="ops@example.test")
    assert user["owned_tasks"]==1 and user["folder_roles"]==[{"name":"Folder","role":"viewer","scope":"route_audit"}]
    assert out.json()["shared_records"]==1 and legacy.get_job("x")==old
    assert legacy.list_history_groups("route_audit","ops@example.test")[0]["role"]=="viewer"
    monkeypatch.setattr(api.backend_service,"AUTH_PROVIDER","microsoft_sso_pending")
    monkeypatch.setattr(api.backend_service,"DEV_USER_EMAIL","sys@example.test")
    assert client.get(path,headers={"Authorization":"Bearer test-only"}).status_code==401
    monkeypatch.delenv("BRP_SCHOOL_DIRECTORY_DB")
    assert client.get(path,headers=admin).status_code==404
    assert not client.get("/api/me",headers=admin).json()["school_directory_enabled"]


def test_admin_inspector_uses_actual_visibility(http):
    client,legacy=http
    for key in ["folder-only","other-private"]:
        legacy.upsert_job({"job_id":key,"owner_email":"other@example.test","status":"succeeded","shared_with_all":False,"metadata":{}})
    with legacy.connect() as con:
        con.execute("insert into history_group_items values('route_audit','b@example.test','folder-only','folder','now')")
    path="/api/admin/school-directory/audit-access?email=ops@example.test"
    assert client.get(path,headers=hdr("ops@example.test")).status_code==403
    reply=client.get(path,headers=hdr("sys@example.test"))
    assert reply.status_code==200
    result={r["id"]:r for r in reply.json()["items"]}
    assert set(result)=={"x","y","folder-only"}
    assert result["x"]["own"] and result["y"]["shared"] and result["folder-only"]["folders"]==["Folder"]
    assert client.get(path+"&page=1",headers=hdr("sys@example.test")).json()=={"total":3,"items":[]}
