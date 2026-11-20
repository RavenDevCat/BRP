import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone, timedelta

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/backend"))
import job_queue as q


def gate(root):
    return q.JobConcurrencyGate(2, root, slot_attach_stale_seconds=30)


def test_foreign_contents_do_not_strand_capacity(tmp_path, monkeypatch, caplog):
    slots = gate(tmp_path)
    dead = slots.acquire("dead")
    slots.attach_worker(dead, 2147483647)
    monkeypatch.setattr(q.shutil, "rmtree", lambda p: (_ for _ in ()).throw(PermissionError(str(p))))
    first = slots.acquire("first")
    second = slots.acquire("second")
    assert first == dead and second != first and not slots.acquire("third")
    assert len(list(tmp_path.glob(".retired-*"))) == 1
    assert "retained inaccessible contents" in caplog.text
    slots.release(first, job_id="dead")
    assert json.loads((first/"metadata.json").read_text())["job_id"] == "first"


def test_live_inaccessible_worker_is_not_dead(tmp_path, monkeypatch):
    slots = gate(tmp_path)
    first = slots.acquire("active")
    slots.attach_worker(first, 123)
    def denied(*args):
        raise PermissionError()
    monkeypatch.setattr(q.os, "kill", denied)
    slots.cleanup_stale_slots()
    assert first.exists()


def test_unreadable_metadata_does_not_release_an_unknown_worker(tmp_path, caplog):
    slots = gate(tmp_path)
    first = slots.acquire("active")
    (first/"metadata.json").write_text("{")
    slots.cleanup_stale_slots()
    assert first.exists() and "retaining it" in caplog.text


def test_timer_releases_without_spawning(monkeypatch):
    sys.path.insert(0, str(ROOT / "ops/scripts"))
    import release_scheduled_audit_jobs as cli
    released = []
    monkeypatch.setattr(cli.backend_service.JOB_STORE, "release_due_scheduled_jobs",
                        lambda: released.append(True) or [])
    monkeypatch.setattr(cli.backend_service, "_schedule_queued_jobs",
                        lambda: pytest.fail("timer spawned a worker"))
    assert cli.main() == 0 and released == [True]


def test_two_jobs_due_together_get_two_slots_and_release_both(tmp_path, monkeypatch):
    import backend_service as service
    from runtime_store_sqlite import SqliteRuntimeStore
    store = SqliteRuntimeStore(tmp_path / "jobs.sqlite")
    monkeypatch.setattr(service, "_runtime_sqlite_store", lambda: store)
    monkeypatch.setattr(service, "JOB_QUEUE_SCOPE", "concurrency-test")
    due = datetime.now(timezone.utc)
    for ident in ("a", "b"):
        store.upsert_job(dict(job_id=ident, status="scheduled", scheduled_start_at=due.isoformat(),
                              metadata=dict(job_queue_scope="concurrency-test",
                                            schedule_expires_at=(due+timedelta(minutes=15)).isoformat())))
    slots = tmp_path / "slots"
    manager = q.JobQueueManager(job_store=service.JobStore(tmp_path), base_dir=tmp_path,
                                runner_path=Path(__file__).resolve(), concurrency_dir=slots,
                                max_concurrent_jobs=2)
    manager.schedule_queued_jobs()
    try:
        leases = [json.loads(p.read_text()) for p in slots.glob("slot-*/metadata.json")]
        assert {m["job_id"] for m in leases} == {"a", "b"}
        assert len({m["worker_pid"] for m in leases}) == 2
        assert all(store.get_job(i)["status"] == "running" for i in ("a", "b"))
    finally:
        (tmp_path / "finish").touch()
    until = time.monotonic()+30
    while list(slots.glob("slot-*")) and time.monotonic()<until:
        time.sleep(.05)
    assert not list(slots.glob("slot-*"))
    assert all(store.get_job(i)["status"] == "succeeded" for i in ("a", "b"))


if __name__ == "__main__":
    from runtime_store_sqlite import SqliteRuntimeStore
    until = time.monotonic()+30
    while not Path("finish").exists() and time.monotonic()<until:
        time.sleep(.05)
    assert Path("finish").exists()
    assert SqliteRuntimeStore(Path("jobs.sqlite")).change_existing_job(
        sys.argv[1], {"status":"succeeded"}, expected_status="running",
        token=os.environ["BRP_JOB_RUN_TOKEN"])
