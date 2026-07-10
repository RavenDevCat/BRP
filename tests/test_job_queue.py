from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import job_queue  # noqa: E402


class FakeJobStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {
            "job1": {"job_id": "job1", "status": "queued", "worker_pid": None}
        }
        self.claims = 0
        self.release_due_calls = 0

    def claim_queued_job(
        self,
        job_id: str,
        *,
        worker_pid: int | None = None,
        job_slot_path: str | None = None,
    ) -> dict[str, Any] | None:
        self.claims += 1
        record = self.records.get(job_id)
        if not record or record.get("status") != "queued":
            return None
        record["status"] = "running"
        record["worker_pid"] = worker_pid
        record["job_slot_path"] = job_slot_path
        return dict(record)

    def list_queued_jobs(self) -> list[dict[str, Any]]:
        return [
            dict(record)
            for record in self.records.values()
            if record.get("status") == "queued"
        ]

    def release_due_scheduled_jobs(self) -> list[dict[str, Any]]:
        self.release_due_calls += 1
        released = []
        for record in self.records.values():
            if record.get("status") == "scheduled" and record.get("due"):
                record["status"] = "queued"
                released.append(dict(record))
        return released

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        record = self.records.get(job_id)
        return dict(record) if record else None

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        record = self.records.get(job_id)
        if not record:
            return None
        record.update(changes)
        return dict(record)


def test_job_queue_claims_before_starting_worker(
    tmp_path: Path, monkeypatch
) -> None:
    store = FakeJobStore()
    popen_calls: list[list[str]] = []
    finish_worker = threading.Event()

    class FakeProcess:
        pid = 4321

        def wait(self) -> int:
            finish_worker.wait()
            return 0

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        assert store.records["job1"]["status"] == "running"
        popen_calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(job_queue.subprocess, "Popen", fake_popen)
    manager = job_queue.JobQueueManager(
        job_store=store,
        runner_path=tmp_path / "backend_job_runner.py",
        base_dir=tmp_path,
        python_executable=sys.executable,
        max_concurrent_jobs=0,
        concurrency_dir=tmp_path / "slots",
    )

    spawned = manager.spawn_job_worker("job1")

    assert spawned is not None
    assert spawned["status"] == "running"
    assert spawned["worker_pid"] == 4321
    assert store.claims == 1
    assert len(popen_calls) == 1

    assert manager.spawn_job_worker("job1") is None
    assert store.claims == 2
    assert len(popen_calls) == 1
    store.update_job("job1", status="succeeded")
    finish_worker.set()


def test_job_queue_releases_due_scheduled_jobs_before_scheduling(
    tmp_path: Path, monkeypatch
) -> None:
    store = FakeJobStore()
    store.records = {"job1": {"job_id": "job1", "status": "scheduled", "due": True}}
    popen_calls: list[list[str]] = []
    finish_worker = threading.Event()

    class FakeProcess:
        pid = 9876

        def wait(self) -> int:
            finish_worker.wait()
            return 0

    def fake_popen(args: list[str], **kwargs: Any) -> FakeProcess:
        popen_calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(job_queue.subprocess, "Popen", fake_popen)
    manager = job_queue.JobQueueManager(
        job_store=store,
        runner_path=tmp_path / "backend_job_runner.py",
        base_dir=tmp_path,
        python_executable=sys.executable,
        max_concurrent_jobs=0,
        concurrency_dir=tmp_path / "slots",
    )

    manager.schedule_queued_jobs()

    assert store.release_due_calls == 1
    assert store.claims == 1
    assert store.records["job1"]["status"] == "running"
    assert store.records["job1"]["worker_pid"] == 9876
    assert len(popen_calls) == 1
    store.update_job("job1", status="succeeded")
    finish_worker.set()


def test_worker_reaper_marks_orphaned_running_job_failed_and_reschedules(
    tmp_path: Path, monkeypatch
) -> None:
    store = FakeJobStore()
    manager = job_queue.JobQueueManager(
        job_store=store,
        runner_path=tmp_path / "backend_job_runner.py",
        base_dir=tmp_path,
        python_executable=sys.executable,
        max_concurrent_jobs=1,
        concurrency_dir=tmp_path / "slots",
    )
    slot_path = manager.gate.acquire("job1")
    assert slot_path is not None
    store.records["job1"].update(
        status="running", worker_pid=2468, job_slot_path=str(slot_path)
    )
    scheduled: list[bool] = []
    monkeypatch.setattr(manager, "schedule_queued_jobs", lambda: scheduled.append(True))

    class CrashedProcess:
        def wait(self) -> int:
            return 7

    manager._reap_job_worker("job1", CrashedProcess(), slot_path)

    record = store.records["job1"]
    assert record["status"] == "failed"
    assert record["worker_exit_code"] == 7
    assert "code 7" in record["error"]
    assert record["worker_pid"] is None
    assert record["job_slot_path"] is None
    assert not slot_path.exists()
    assert scheduled == [True]


def test_worker_reaper_does_not_overwrite_runner_terminal_status(
    tmp_path: Path, monkeypatch
) -> None:
    store = FakeJobStore()
    store.records["job1"].update(
        status="succeeded",
        result={"route_count": 22},
        error=None,
        worker_pid=None,
        job_slot_path=None,
    )
    manager = job_queue.JobQueueManager(
        job_store=store,
        runner_path=tmp_path / "backend_job_runner.py",
        base_dir=tmp_path,
        python_executable=sys.executable,
        max_concurrent_jobs=1,
        concurrency_dir=tmp_path / "slots",
    )
    slot_path = manager.gate.acquire("job1")
    assert slot_path is not None
    scheduled: list[bool] = []
    monkeypatch.setattr(manager, "schedule_queued_jobs", lambda: scheduled.append(True))

    class FinishedProcess:
        def wait(self) -> int:
            return 0

    before = dict(store.records["job1"])
    manager._reap_job_worker("job1", FinishedProcess(), slot_path)

    assert store.records["job1"] == before
    assert not slot_path.exists()
    assert scheduled == [True]
