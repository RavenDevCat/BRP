"""Offline integration: exercise the real storage, query and release boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import multiprocessing
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
for sub in ("apps","apps/client","apps/backend","ops/scripts"):
    sys.path.insert(0,str(ROOT/sub))
from brp_storage import keyed_cache as cache, json_cache_store as store
from runtime_store_sqlite import SqliteRuntimeStore
import verified_web_release as release


def _writer(path,key,barrier):
    snap=store.load_json_object(path)
    barrier.wait(timeout=10)
    snap[key]=key
    store.save_json_object(path,snap)


def test_process_merge_conflict_clear_rollback(tmp_path,monkeypatch):
    monkeypatch.setenv("BRP_KEYED_CACHE","1")
    path=tmp_path/"geocode_cache.json"
    path.write_text('{"old":{"x":1}}')
    original=path.read_bytes()
    ctx=multiprocessing.get_context("spawn")
    barrier=ctx.Barrier(2)
    procs=[ctx.Process(target=_writer,args=(path,k,barrier)) for k in ("A","B")]
    for proc in procs: proc.start()
    for proc in procs:
        proc.join(20)
        assert proc.exitcode==0
    a,b=store.load_json_object(path),store.load_json_object(path)
    assert a=={"A":"A","B":"B","old":{"x":1}}
    a["old"]["x"]=2;store.save_json_object(path,a)
    b["old"]["x"]=3;store.save_json_object(path,b)
    assert b["old"]["x"]==store.load_json_object(path)["old"]["x"]==2
    b.pop("old");store.save_json_object(path,b)
    a["old"]={"x":4};store.save_json_object(path,a)
    assert "old" not in store.load_json_object(path)
    rollback=tmp_path/"rollback.json"
    cache.export(path,rollback)
    assert json.loads(rollback.read_text())=={"A":"A","B":"B"}
    assert path.read_bytes()==original
    store.clear_json_object(path)
    a["A"]="stale"
    store.save_json_object(path,a)
    assert a==store.load_json_object(path)=={}
    monkeypatch.delenv("BRP_KEYED_CACHE")
    assert store.load_json_object(path)=={"old":{"x":1}}


@pytest.mark.parametrize("folder",["client","backend"])
def test_both_launchers_share_quota(folder,tmp_path):
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,sys.argv[1]);from quota_store_sqlite import SqliteQuotaStore as A;from brp_storage.quota_store_sqlite import SqliteQuotaStore as B;assert A is B and callable(A.sum_usage)",str(ROOT/"apps"/folder)],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_worker_checkpoint_equivalence(tmp_path,monkeypatch):
    from test_direct_school_analysis import prepared_payload,FakeProvider,fake_osrm
    import direct_school_analysis as analysis
    monkeypatch.setattr(analysis,"_osrm_leg",fake_osrm)
    monkeypatch.setattr(analysis,"utc_now_iso",lambda:"2026-11-18T07:00:00+08:00")
    db=SqliteRuntimeStore(tmp_path/"db")
    db.upsert_job({"job_id":"direct","worker_token":"token","status":"running","prepared_payload":prepared_payload(),"metadata":{"job_kind":"direct_school_analysis"}})
    before=db.get_job("direct")
    expected=[];actual=[]
    args=(prepared_payload(),{"far_duration_minutes":45})
    kw=dict(run_seed="test",provider_factory=FakeProvider)
    analysis.run_direct_school_analysis(*args,checkpoint=lambda x:expected.append(deepcopy(x)),**kw)
    def delta(parts,reset):
        assert db.checkpoint_parts("direct","token",parts,reset=reset)
        actual.append(db.get_job("direct")["result"])
        assert db.get_job_result_summaries(["direct"])["direct"]["summary"]==actual[-1]["summary"]
    analysis.run_direct_school_analysis(*args,checkpoint_delta=delta,**kw)
    assert len(actual)>1 and actual==expected
    with db.connect() as conn:
        assert json.loads(conn.execute("SELECT record_json FROM jobs").fetchone()[0])==before
    assert db.running_attempt("direct","token") and not db.running_attempt("direct","old")
    assert not db.checkpoint_parts("direct","old",[],reset=True)
    _, canceled=db.cancel_active_job("direct")
    assert canceled["result"]==actual[-1]
    assert not db.checkpoint_parts("direct","token",[],reset=True)
    assert db.delete_job("direct")
    with db.connect() as conn:
        assert not conn.execute("SELECT 1 FROM job_checkpoint_parts").fetchone()


def test_readiness_is_readonly(tmp_path,monkeypatch):
    import api_app as api
    p=tmp_path/"missing.sqlite"
    monkeypatch.setattr(api.backend_service,"RUNTIME_DB_PATH",p)
    assert api.ready().status_code==503 and not p.exists()
    db=SqliteRuntimeStore(p);db.initialize()
    assert api.ready().status_code==200
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0]==0
        conn.execute("DROP INDEX idx_jobs_scheduled_dispatch")
    assert api.ready().status_code==503


def bundle(p,v):
    (p/"assets").mkdir(parents=True)
    (p/"index.html").write_text('<script type="module" src="/assets/index-selected.js"></script>')
    (p/"assets/index-selected.js").write_text('let v="'+v+'"')
    return p


def test_release_rejects_missing_or_owner_only_icon(tmp_path):
    stage=bundle(tmp_path/"stage","2222222")
    html=stage/"index.html"
    html.write_text('<link rel="icon" href="/bus-front.svg">'+html.read_text())
    with pytest.raises(ValueError,match="icon"):
        release.verified_entry(stage,"2222222")
    icon=stage/"bus-front.svg"
    icon.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    if sys.platform!="win32":
        icon.chmod(0o600)
        with pytest.raises(ValueError,match="publicly readable"):
            release.verified_entry(stage,"2222222")
    icon.chmod(0o644)
    assert release.verified_entry(stage,"2222222")


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX traversal permissions")
def test_private_build_and_reader_gate(tmp_path, monkeypatch):
    web=tmp_path/"web"
    old=bundle(web/"dist","1111111")
    source=bundle(tmp_path/"source","2222222")
    source.chmod(0o700)
    def denied(cmd, **kwargs):
        assert cmd[:5]==["sudo","-n","-u","www-data","--"]
        assert any(str(path).endswith("/index.html") for path in cmd)
        raise subprocess.CalledProcessError(1,cmd)
    with monkeypatch.context() as patch:
        patch.setattr(release.subprocess,"run",denied)
        with pytest.raises(subprocess.CalledProcessError):
            release.activate(web,source,"2222222","www-data")
    assert not old.is_symlink() and release.verified_entry(old,"1111111")
    previous=release.activate(web,source,"2222222")
    assert source.stat().st_mode & 0o777 == 0o700
    assert old.resolve().stat().st_mode & 0o777 == 0o711
    target=old.resolve()
    release.switch(web,previous)
    target.chmod(0o700)
    with pytest.raises(ValueError,match="traversed"):
        release.activate(web,source,"2222222")
    assert old.resolve()==previous


def test_wrong_entry_restore_and_retention(tmp_path):
    web=tmp_path/"web";web.mkdir()
    dist=bundle(web/"dist","1111111")
    stage=bundle(tmp_path/"stage","2222222")
    (stage/"assets/index-stale.js").write_text("3333333")
    with pytest.raises(ValueError):
        release.activate(web,stage,"3333333")
    assert release.verified_entry(dist,"1111111")
    original_html=(stage/"index.html").read_text()
    (stage/"index.html").write_text(original_html+'<link rel="stylesheet" href="/assets/missing.css">')
    with pytest.raises(ValueError,match="dependency"):
        release.activate(web,stage,"2222222")
    (stage/"index.html").write_text(original_html)
    previous=release.activate(web,stage,"2222222")
    second=dist.resolve()
    assert release.fingerprint(dist,"2222222")==release.fingerprint(stage,"2222222")
    release.switch(web,previous)
    assert release.verified_entry(dist,"1111111")
    for i in range(3,8):
        v=str(i)*7
        release.activate(web,bundle(tmp_path/v,v),v)
        (dist/".accepted").touch()
    release.prune(web,2,True)
    assert previous.exists() and second.exists() and dist.resolve().exists()
    assert len(list((web/".brp-releases").glob("*/.accepted")))==2
