from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3
import subprocess
import threading

import pytest

from test_measurement_reviews import source, request, Provider, review, SqliteRuntimeStore
import measurement_review_queue as queue_module
from job_queue import JobConcurrencyGate, JobQueueManager


@pytest.fixture
def setup(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    row = store.create_route_measurement_review(request(), queue_scope="test")
    gate = JobConcurrencyGate(1, tmp_path / "slots", slot_attach_stale_seconds=30)
    queue = queue_module.MeasurementReviewQueue(
        store=store, gate=gate, queue_scope="test", runner_path=Path("unused.py"),
        base_dir=tmp_path, python_executable="unused", schedule_normal=Mock())
    return store, row["review_id"], gate, queue


def test_existing_v6_schema_is_upgraded_without_losing_review(tmp_path):
    path = tmp_path / "old.sqlite"
    store = SqliteRuntimeStore(path)
    store.upsert_job(source())
    row = store.create_route_measurement_review(request())
    with sqlite3.connect(path) as conn:
        for name in ("queue_scope", "worker_pid", "job_slot_path", "api_calls", "error_code"):
            conn.execute(f"ALTER TABLE route_measurement_reviews DROP COLUMN {name}")
        conn.execute("UPDATE schema_migrations SET version = 6")
    upgraded = SqliteRuntimeStore(path)
    restored = upgraded.get_route_measurement_review(row["review_id"])
    assert restored["request"] == row["request"] and restored["api_calls"] == 0
    assert restored["queue_scope"] == ""
    assert SqliteRuntimeStore(path).get_route_measurement_review(row["review_id"]) == restored


def test_budget_is_atomic_and_cancel_does_not_release_live_claim(setup):
    store, rid, _, _ = setup
    store.claim_route_measurement_review(rid, "a")
    with ThreadPoolExecutor(max_workers=6) as pool:
        accepted = list(pool.map(lambda _: store.reserve_route_measurement_calls(rid, "a", 1), range(120)))
    assert sum(accepted) == 100 and store.get_route_measurement_review(rid)["api_calls"] == 100
    store.cancel_route_measurement_review(rid)
    second_request = {**request(), "request_key": "second"}
    second = store.create_route_measurement_review(second_request, queue_scope="other")
    assert not store.claim_route_measurement_review(second["review_id"], "b")
    assert not store.reserve_route_measurement_calls(rid, "a", 1)
    assert store.finish_route_measurement_worker(rid, "a")
    assert store.claim_route_measurement_review(second["review_id"], "b", queue_scope="other")


def test_pause_resume_retains_budget_and_stale_worker_cannot_write(setup):
    store, rid, _, queue = setup
    assert queue.action(rid, "pause")["status"] == "paused"
    assert queue.action(rid, "resume")["status"] == "queued"
    assert not store.claim_route_measurement_review(rid, "wrong", queue_scope="other")
    store.claim_route_measurement_review(rid, "first", queue_scope="test")
    store.reserve_route_measurement_calls(rid, "first", 3)
    store.pause_route_measurement_review(rid, yielding=True)
    assert not store.claim_route_measurement_review(rid, "second")
    store.finish_route_measurement_worker(rid, "first")
    store.claim_route_measurement_review(rid, "second")
    assert not store.finish_route_measurement_worker(rid, "first")
    assert not store.reserve_route_measurement_calls(rid, "first", 1)
    result = review.execute_saved_review(store, rid, "second", provider_factory=Provider, preclaimed=True)
    assert result["status"] == "succeeded" and result["api_calls"] == 5
    assert result["result"]["provider_api_calls"] == 5


def test_resume_skips_checkpointed_routes_but_counts_interrupted_request(tmp_path):
    original = source()
    routes = original["result"]["structured_results"]["current_plan"]["routes"]
    routes.append({**deepcopy(routes[0]), "route_id": "R2"})
    payload = review.build_review_request(original, ["current_plan:R1", "current_plan:R2"],
                                          requested_by="admin@example.test", request_key="resume")
    checkpoints = []
    class PausingProvider(Provider):
        def route(self, points, **kwargs):
            if self.calls:
                self.state["api_calls"] += 1
                raise review.ReviewClaimLost("interrupted")
            return super().route(points, **kwargs)
    reservations = []
    with pytest.raises(review.ReviewClaimLost):
        review.run_measurement_review(payload, provider_factory=PausingProvider,
            checkpoint=checkpoints.append, reserve_calls=lambda n: reservations.append(n) or True)
    assert len(checkpoints) == 1 and sum(reservations) == 3
    resumed = review.run_measurement_review(payload, provider_factory=Provider,
                                            resume_result=checkpoints[0], api_calls_used=3)
    assert len(resumed["routes"]) == 2 and resumed["provider_api_calls"] == 5
    assert resumed["routes"][0] == checkpoints[0]["routes"][0]


def test_reservation_denial_prevents_provider_io_and_checkpoint(setup):
    store, rid, _, _ = setup
    store.claim_route_measurement_review(rid, "worker")
    class StopBeforeIo(Provider):
        def route(self, *_args, **_kwargs):
            store.pause_route_measurement_review(rid)
            self.state["api_calls"] += 1
            pytest.fail("No outbound call may follow a denied reservation")
    row = review.execute_saved_review(store, rid, "worker", preclaimed=True, provider_factory=StopBeforeIo)
    assert row["status"] == "pausing" and row["api_calls"] == 0 and row["result"] is None


def test_worker_launch_failure_keeps_failed_record_and_releases_slot(setup, monkeypatch):
    store, rid, gate, queue = setup
    monkeypatch.setattr(queue_module.subprocess, "Popen", Mock(side_effect=OSError("not public")))
    queue.schedule()
    row = store.get_route_measurement_review(rid)
    assert row["status"] == "failed" and row["error_code"] == "worker_start_failed"
    assert not store.active_route_measurement_reviews("test")
    assert gate.acquire("normal")
    queue.schedule()
    assert queue_module.subprocess.Popen.call_count == 1


def test_claim_error_releases_unowned_capacity(setup, monkeypatch):
    store, rid, gate, queue = setup
    monkeypatch.setattr(store, "claim_route_measurement_review", Mock(side_effect=sqlite3.OperationalError("locked")))
    with pytest.raises(sqlite3.OperationalError):
        queue.schedule()
    assert gate.acquire("normal")
    assert store.get_route_measurement_review(rid)["status"] == "queued"


def test_attachment_failure_stops_owned_process_before_releasing(setup, monkeypatch):
    store, rid, gate, queue = setup
    process = Mock(pid=98765)
    process.poll.return_value = None
    monkeypatch.setattr(queue_module.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(gate, "attach_worker", Mock(side_effect=OSError("disk error")))
    queue.schedule()
    process.terminate.assert_called_once()
    assert store.get_route_measurement_review(rid)["status"] == "failed"
    assert gate.acquire("normal")


def test_poll_recovery_after_termination_timeout(setup):
    store, rid, gate, queue = setup
    owner = f"review:{rid}:token"
    slot = gate.acquire(owner)
    store.claim_route_measurement_review(rid, "token", job_slot_path=str(slot))
    process = Mock(poll=Mock(return_value=None), wait=Mock(side_effect=subprocess.TimeoutExpired("worker", 3)))
    queue._workers[rid] = ("token", process, slot, owner)
    assert not queue.preempt()
    process.poll.return_value = -9
    queue.reconcile()
    assert store.get_route_measurement_review(rid)["status"] == "queued"
    assert not queue._workers and gate.acquire("normal")


@pytest.mark.parametrize("times_out", [False, True])
def test_preempt_releases_capacity_only_after_confirmed_death(setup, times_out):
    store, rid, gate, queue = setup
    owner = f"review:{rid}:token"
    slot = gate.acquire(owner)
    store.claim_route_measurement_review(rid, "token", job_slot_path=str(slot))
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("worker", 3) if times_out else None
    queue._workers[rid] = ("token", process, slot, owner)
    assert queue.preempt() is (not times_out)
    assert store.get_route_measurement_review(rid)["status"] == ("yielding" if times_out else "queued")
    assert (gate.acquire("normal") is None) is times_out


def test_reconcile_waits_for_attachment_and_never_kills_unowned_pid(setup, monkeypatch):
    store, rid, gate, queue = setup
    store.claim_route_measurement_review(rid, "token")
    queue.reconcile()
    assert store.get_route_measurement_review(rid)["status"] == "running"
    store.attach_route_measurement_worker(rid, "token", 98765)
    monkeypatch.setattr(queue_module, "pid_is_alive", lambda pid: True)
    queue.reconcile()
    assert store.get_route_measurement_review(rid)["status"] == "running"
    monkeypatch.setattr(queue_module, "pid_is_alive", lambda pid: False)
    queue.reconcile()
    row = store.get_route_measurement_review(rid)
    assert row["status"] == "failed" and row["error_code"] == "worker_lost"


def test_wrong_environment_cannot_control_review(setup):
    store, rid, _, queue = setup
    queue.queue_scope = "other"
    with pytest.raises(ValueError, match="environment"):
        queue.action(rid, "cancel")
    assert store.get_route_measurement_review(rid)["status"] == "queued"


def test_spawn_uses_shared_gate_and_distinct_lease_owner(setup, monkeypatch):
    store, rid, gate, queue = setup
    process = Mock(pid=98765)
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(queue_module.subprocess, "Popen", popen)
    thread = Mock()
    monkeypatch.setattr(queue_module.threading, "Thread", thread)
    queue.schedule()
    queue.schedule()
    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args[0] == ["unused", "unused.py", rid]
    assert kwargs["env"]["BRP_RUNTIME_DB_PATH"] == str(store.db_path)
    token, _, slot, owner = queue._workers[rid]
    assert kwargs["env"]["BRP_MEASUREMENT_REVIEW_TOKEN"] == token
    assert owner == f"review:{rid}:{token}"
    assert store.get_route_measurement_review(rid)["worker_pid"] == 98765
    assert Path(slot).exists()
    assert queue.preempt()
    assert store.get_route_measurement_review(rid)["status"] == "queued"
    assert not Path(slot).exists()


def test_runner_requires_preclaim_and_reaper_finishes_process_lease(setup, monkeypatch):
    import measurement_review_runner as runner
    store, rid, gate, queue = setup
    monkeypatch.setattr(runner.sys, "argv", ["runner", rid])
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH", str(store.db_path))
    monkeypatch.setenv("BRP_MEASUREMENT_REVIEW_TOKEN", "token")
    monkeypatch.setattr(runner, "execute_saved_review", lambda s, r, t, **kwargs:
                        review.execute_saved_review(s, r, t, provider_factory=Provider, **kwargs))
    # An unclaimed request cannot start provider work.
    assert runner.main() == 1
    assert store.get_route_measurement_review(rid)["status"] == "queued"
    owner = f"review:{rid}:token"
    slot = gate.acquire(owner)
    store.claim_route_measurement_review(rid, "token", job_slot_path=str(slot))
    assert runner.main() == 0
    row = store.get_route_measurement_review(rid)
    assert row["status"] == "succeeded" and row["api_calls"] == 2
    assert Path(slot).exists()
    assert store.active_route_measurement_reviews("test")
    queue._reap(rid, "token", Mock(wait=Mock(return_value=0)), slot, owner)
    assert not store.active_route_measurement_reviews("test")
    assert not Path(slot).exists()
    queue.schedule_normal.assert_called_once()


@pytest.mark.parametrize("preempt_success", [True, False])
def test_normal_queue_gets_priority_and_background_waits(preempt_success):
    events = []
    scheduler = SimpleNamespace(
        _scheduler_lock=threading.Lock(), gate=SimpleNamespace(enabled=True, cleanup_stale_slots=lambda: None),
        job_store=SimpleNamespace(release_due_scheduled_jobs=lambda: None,
            list_queued_jobs=lambda: [{"job_id": "normal"}], list_queued_deep_verifications=lambda: []),
        measurement_reviews=SimpleNamespace(reconcile=lambda: None,
            preempt=lambda: events.append("preempt") or preempt_success,
            schedule=lambda: events.append("review")),
        spawn_job_worker=Mock(side_effect=lambda _: events.append("normal") or (123 if events.count("normal") > 1 else None)),
        _preempt_running_deep_verification=lambda: False)
    JobQueueManager.schedule_queued_jobs(scheduler)
    assert events == (["normal", "preempt", "normal", "review"] if preempt_success else ["normal", "preempt"])
