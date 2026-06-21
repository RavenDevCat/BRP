from __future__ import annotations

from pathlib import Path
import sys
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

    class FakeProcess:
        pid = 4321

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
