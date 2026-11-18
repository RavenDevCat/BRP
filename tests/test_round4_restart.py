from pathlib import Path
import os
import subprocess
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"apps/backend"))
from runtime_store_sqlite import SqliteRuntimeStore


def test_committed_parts_survive_worker_exit_and_no_claim_can_resurrect_deletion(tmp_path):
    path=tmp_path/"job.sqlite"
    db=SqliteRuntimeStore(path)
    db.upsert_job({"job_id":"j","worker_token":"a","status":"running",
                   "metadata":{"job_kind":"direct_school_analysis"},"prepared_payload":{"probe":"immutable"}})
    child="""from runtime_store_sqlite import SqliteRuntimeStore
import os,sys
db=SqliteRuntimeStore(sys.argv[1])
assert db.checkpoint_parts("j","a",[("head",0,{"status":"running","progress":{"completed":1}}),("stops",0,{"stop_key":"x","duration_s":51})],reset=True)
os._exit(17)
"""
    p=subprocess.run([sys.executable,"-c",child,str(path)],cwd=ROOT/"apps/backend",
                     env={**os.environ,"PYTHONPATH":str(ROOT/"apps")})
    assert p.returncode==17
    restarted=SqliteRuntimeStore(path)
    j=restarted.get_job("j")
    assert j["result"]["stops"]==[{"stop_key":"x","duration_s":51}]
    assert restarted.change_existing_job("j",{"status":"queued"},expected_status="running",token="a")["result"]==j["result"]
    assert not restarted.checkpoint_parts("j","a",[],reset=True)
    assert restarted.get_job("j")["result"]==j["result"]
    assert restarted.delete_job("j")
    assert not restarted.checkpoint_parts("j","a",[],reset=True)
