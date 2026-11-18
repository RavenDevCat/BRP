"""Queue transitions must remain true across processes, not just scheduler threads."""
from datetime import datetime, timezone
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/backend"))
from runtime_store_sqlite import SqliteRuntimeStore


def seed(store, job_id="am", status="queued", **extra):
    store.upsert_job(dict(job_id=job_id, status=status, metadata={"job_queue_scope":"qa"}, **extra))


def _dispatch(db, barrier, timer):
    os.environ.update(BRP_RUNTIME_DB_PATH=str(db), BRP_JOB_QUEUE_SCOPE="qa",
                      BRP_QUOTA_DB_PATH=str(db.parent/"quota.sqlite"),
                      BRP_JOB_CONCURRENCY_DIR=str(db.parent/"slots"))
    sys.path.insert(0, str(ROOT / "ops/scripts"))
    import release_scheduled_audit_jobs as cli
    service = cli.backend_service
    service.JOB_QUEUE.runner_path = Path(__file__).resolve()
    barrier.wait(timeout=90)
    if timer:
        cli.main()
    else:
        service.JOB_QUEUE.schedule_queued_jobs()


def test_two_dispatch_processes_spawn_only_once(tmp_path):
    path = tmp_path/"race.sqlite"
    store = SqliteRuntimeStore(path)
    seed(store, status="scheduled", scheduled_start_at="2020-01-01T07:00:00+08:00")
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    ps = [ctx.Process(target=_dispatch, args=(path,barrier,timer)) for timer in (True,False)]
    for p in ps: p.start()
    try:
        for p in ps:
            p.join(120)
            assert p.exitcode == 0
        until = time.monotonic()+25
        while store.get_job("am")["status"] == "running" and time.monotonic()<until:
            time.sleep(.05)
        saved = store.get_job("am")
        assert saved["status"] == "succeeded"
        assert (tmp_path/"worker-starts").read_text().splitlines() == [saved["worker_token"]]
    finally:
        for p in ps:
            if p.is_alive(): p.terminate(); p.join()


def test_stale_worker_cannot_complete_cancel_or_resurrect(tmp_path, monkeypatch):
    import backend_job_runner as worker
    store=SqliteRuntimeStore(tmp_path/"worker.sqlite")
    monkeypatch.setattr(worker,"_runtime_store",lambda:store)
    seed(store)
    assert store.claim_queued_job("am",scope="other") is None
    first=store.claim_queued_job("am",scope="qa")
    monkeypatch.setenv("BRP_JOB_RUN_TOKEN",first["worker_token"])
    store.cancel_active_job("am")
    with pytest.raises(worker.SupersededAttempt): worker._save_job(dict(first,status="succeeded"))
    store.change_existing_job("am", {"status":"queued"}, expected_status="canceled")
    second=store.claim_queued_job("am")
    assert first["worker_token"] != second["worker_token"]
    assert store.change_existing_job("am",{"status":"failed"},expected_status="running",token=first["worker_token"]) is None
    with pytest.raises(worker.SupersededAttempt): worker._save_job(dict(second,status="failed"))
    assert store.get_job("am")["status"]=="running"
    store.delete_jobs(["am"])
    monkeypatch.setenv("BRP_JOB_RUN_TOKEN",second["worker_token"])
    with pytest.raises(worker.SupersededAttempt): worker._save_job(dict(second,status="succeeded"))
    assert store.get_job("am") is None


def _crash_after_claim(path):
    assert SqliteRuntimeStore(path).claim_queued_job("am")
    os._exit(17)


def test_launcher_crash_between_claim_and_pid_attachment(tmp_path,monkeypatch):
    import backend_service as service
    path=tmp_path/"crash.sqlite"
    store=SqliteRuntimeStore(path)
    seed(store)
    proc=mp.get_context("spawn").Process(target=_crash_after_claim,args=(path,))
    proc.start(); proc.join(30)
    assert proc.exitcode==17 and store.get_job("am")["status"]=="running"
    monkeypatch.setattr(service,"_runtime_sqlite_store",lambda:store)
    monkeypatch.setattr(service,"JOB_QUEUE_SCOPE","qa")
    service.JobStore(tmp_path).reconcile_running_jobs()
    assert store.get_job("am")["status"]=="failed"
    assert store.claim_queued_job("am") is None


def test_review_retry_after_partial_write_and_restart(tmp_path,monkeypatch):
    import backend_job_runner as worker
    path=tmp_path/"outbox.sqlite"
    store=SqliteRuntimeStore(path)
    seed(store)
    claim=store.claim_queued_job("am")
    monkeypatch.setenv("BRP_JOB_RUN_TOKEN",claim["worker_token"])
    monkeypatch.setattr(worker,"_runtime_store",lambda:store)
    worker._save_job(dict(claim,status="succeeded",result={"verified":True}))
    def children(_):
        for s in ["a","b"]:
            yield dict(verification_id=s,parent_job_id="am",scenario_key=s,status="queued")
    def partial(parent):
        yield next(children(parent))
        raise RuntimeError("fault after first child")
    store.drain_automatic_reviews("other",children)
    store.drain_automatic_reviews("qa",partial)
    assert store.get_job("am")["result"] == {"verified":True} and store.get_job("am")["status"]=="succeeded"
    assert not store.list_deep_verifications()
    restarted=SqliteRuntimeStore(path)
    with restarted.connect() as conn:
        assert conn.execute("SELECT attempts,retry_at>unixepoch() FROM automatic_review_outbox").fetchone()[:] == (1,1)
        conn.execute("UPDATE automatic_review_outbox SET retry_at=0")
    restarted.drain_automatic_reviews("qa",children)
    restarted.ensure_review_children("am",children)
    restarted.drain_automatic_reviews("qa",children)
    assert sorted(x["scenario_key"] for x in restarted.list_deep_verifications()) == ["a","b"]
    with restarted.connect() as conn:
        assert conn.execute("SELECT count(*) FROM automatic_review_outbox").fetchone()[0] == 0


@pytest.mark.parametrize("parent_status",["failed","canceled"])
def test_failed_parent_cannot_release_return_and_deletion_wins(tmp_path,parent_status):
    from audit_return import bind_morning_scenarios
    store=SqliteRuntimeStore(tmp_path/"pair.sqlite")
    ids={"am":"am","pm":"pm"}
    for period,status in [("am",parent_status),("pm","scheduled")]:
        store.upsert_job({"job_id":period,"status":status,"scheduled_start_at":"2020-01-01T01:00:00Z",
                          "metadata":{"job_queue_scope":"qa","job_kind":"route_audit","pair_id":"p",
                                      "paired_job_ids":ids,"period":period,"measurement_kind":None if period=="am" else "pm_return"}})
    row,won=store.release_scheduled("pm","qa",bind_morning_scenarios, now=datetime.now(timezone.utc))
    assert not won and row["status"]=="failed" and parent_status in row["error"]
    store.prepare_jobs_for_deletion(list(ids))
    store.delete_jobs(list(ids))
    assert store.release_scheduled("pm","qa",bind_morning_scenarios,now=datetime.now(timezone.utc))==(None,False)
    assert store.claim_queued_job("pm") is None and not store.list_jobs(include_all=True)


@pytest.mark.parametrize("history_size", [100,10000])
def test_dispatch_uses_bounded_due_index_with_history_and_waiting_parent(tmp_path,history_size):
    import json
    from runtime_store_sqlite import _QUEUE_SCOPE, _DUE
    store=SqliteRuntimeStore(tmp_path/"index.sqlite")
    store.initialize()
    with store.connect() as c:
        c.executemany("""INSERT INTO jobs(job_id,status,record_json,updated_at)
            VALUES(?,'succeeded','{}','2026-01-01')""",((f"history-{i}",) for i in range(history_size)))
    now="2026-11-18T00:00:00Z"
    def scheduled(id,at,**m):
        store.upsert_job({"job_id":id,"status":"scheduled","scheduled_start_at":at,
                          "metadata":{"job_queue_scope":"qa",**m}})
    seed(store,"parent",status="running")
    scheduled("wait","2020-01-01T00:00:00Z",return_dependency_status="waiting_for_morning",paired_job_ids={"am":"parent"})
    for i in range(67): scheduled(f"ready-{i}","2026-11-18T08:00:00+08:00")
    scheduled("future","2026-11-18T08:00:01+08:00")
    ids=store.dispatch_ids("scheduled","qa",due_at=now)
    assert len(ids)==64 and all(x.startswith("ready-") for x in ids)
    assert store.dispatch_ids("scheduled","other",due_at=now)==[]
    store.cancel_active_job("parent")
    assert store.dispatch_ids("scheduled","qa",due_at=now,limit=1)==["wait"]
    with store.connect() as c:
        plan=c.execute(f"EXPLAIN QUERY PLAN SELECT job_id FROM jobs WHERE status='scheduled' AND {_QUEUE_SCOPE}=? AND {_DUE}<=julianday(?) ORDER BY {_DUE},job_id LIMIT 64",("qa",now))
        assert any("idx_jobs_scheduled_dispatch" in row[3] for row in plan)


if __name__=="__main__":
    path=Path(os.environ["BRP_RUNTIME_DB_PATH"])
    token=os.environ["BRP_JOB_RUN_TOKEN"]
    with (path.parent/"worker-starts").open("a") as out: print(token,file=out)
    assert SqliteRuntimeStore(path).change_existing_job(sys.argv[1],{"status":"succeeded"},expected_status="running",token=token)
