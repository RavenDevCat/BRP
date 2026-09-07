"""Low-priority review workers sharing the normal job concurrency gate."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Any, Callable
from uuid import uuid4

try:
    from .job_queue import pid_is_alive, seconds_since_iso, worker_creation_flags
except ImportError:
    from job_queue import pid_is_alive, seconds_since_iso, worker_creation_flags


class MeasurementReviewQueue:
    def __init__(self, *, store: Any, gate: Any, queue_scope: str, runner_path: Path,
                 base_dir: Path, python_executable: str, schedule_normal: Callable[[], None]):
        self.store, self.gate, self.queue_scope = store, gate, queue_scope
        self.runner_path, self.base_dir = runner_path, base_dir
        self.python_executable, self.schedule_normal = python_executable, schedule_normal
        self._lock = threading.RLock()
        self._workers: dict[str, tuple[str, Any, Any, str]] = {}

    def schedule(self) -> None:
        with self._lock:
            if self._workers:
                return
            for row in self.store.queued_route_measurement_reviews(self.queue_scope):
                self._spawn(row["review_id"])
                break

    def _spawn(self, review_id: str) -> None:
        token = uuid4().hex
        owner = f"review:{review_id}:{token}"
        slot = self.gate.acquire(owner)
        if self.gate.enabled and slot is None:
            return
        try:
            claimed = self.store.claim_route_measurement_review(
                review_id, token, queue_scope=self.queue_scope, job_slot_path=str(slot) if slot else None)
        except Exception:
            self.gate.release(slot, job_id=owner)
            raise
        if not claimed:
            self.gate.release(slot, job_id=owner)
            return
        env = os.environ.copy()
        env.update(BRP_RUNTIME_DB_PATH=str(self.store.db_path), BRP_MEASUREMENT_REVIEW_TOKEN=token,
                   BRP_MEASUREMENT_REVIEW_SLOT_OWNER=owner)
        if slot:
            env.update(BRP_JOB_CONCURRENCY_SLOT=str(slot), BRP_JOB_CONCURRENCY_ROOT=str(self.gate.slot_dir))
        else:
            env.pop("BRP_JOB_CONCURRENCY_SLOT", None)
            env.pop("BRP_JOB_CONCURRENCY_ROOT", None)
        try:
            process = subprocess.Popen([self.python_executable, str(self.runner_path), review_id],
                                       cwd=str(self.base_dir), stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=env, creationflags=worker_creation_flags())
        except Exception:
            self.store.finish_route_measurement_worker(review_id, token, "worker_start_failed")
            self.gate.release(slot, job_id=owner)
            return
        self._workers[review_id] = (token, process, slot, owner)
        try:
            self.gate.attach_worker(slot, int(process.pid))
            self.store.attach_route_measurement_worker(review_id, token, int(process.pid))
            threading.Thread(target=self._reap, args=(review_id, token, process, slot, owner),
                             name=f"brp-measurement-review-{process.pid}", daemon=True).start()
        except Exception:
            self._stop_owned(review_id)

    def _finish(self, review_id: str, token: str, slot: Any, owner: str, code: str) -> None:
        self.store.finish_route_measurement_worker(review_id, token, code)
        self.gate.release(slot, job_id=owner)
        if self._workers.get(review_id, (None,))[0] == token:
            self._workers.pop(review_id, None)

    def _reap(self, review_id: str, token: str, process: Any, slot: Any, owner: str) -> None:
        exit_code = process.wait()
        with self._lock:
            self._finish(review_id, token, slot, owner, f"worker_exit_{exit_code}")
        self.schedule_normal()

    def _stop_owned(self, review_id: str) -> bool:
        owned = self._workers.get(review_id)
        if not owned:
            return False
        token, process, slot, owner = owned
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                return False
        except ProcessLookupError:
            if process.poll() is None:
                return False
        self._finish(review_id, token, slot, owner, "worker_stopped")
        return True

    def preempt(self) -> bool:
        with self._lock:
            for review_id in list(self._workers):
                if self.store.pause_route_measurement_review(review_id, yielding=True):
                    return self._stop_owned(review_id)
        return False

    def action(self, review_id: str, action: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.store.get_route_measurement_review(review_id)
            if not row:
                return None
            if row["queue_scope"] != self.queue_scope:
                raise ValueError("Control this review from the environment where it was created.")
            if action == "cancel":
                self.store.cancel_route_measurement_review(review_id)
                self._stop_owned(review_id)
            elif action == "pause":
                self.store.pause_route_measurement_review(review_id)
                self._stop_owned(review_id)
            elif action == "resume":
                self.store.resume_route_measurement_review(review_id)
            else:
                raise ValueError("Unsupported review action.")
            return self.store.get_route_measurement_review(review_id)

    def reconcile(self) -> None:
        with self._lock:
            for review_id, (token, process, slot, owner) in list(self._workers.items()):
                exit_code = process.poll()
                if exit_code is not None:
                    self._finish(review_id, token, slot, owner, f"worker_exit_{exit_code}")
            for row in self.store.active_route_measurement_reviews(self.queue_scope):
                review_id = row["review_id"]
                if review_id in self._workers:
                    continue
                pid = int(row.get("worker_pid") or 0)
                if pid and pid_is_alive(pid):
                    continue
                if not pid:
                    age = seconds_since_iso(row.get("started_at"))
                    if age is None or age < self.gate.slot_attach_stale_seconds:
                        continue
                token = row["worker_token"]
                self._finish(review_id, token, row.get("job_slot_path"),
                             f"review:{review_id}:{token}", "worker_lost")
