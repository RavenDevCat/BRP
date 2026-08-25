from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Protocol


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def seconds_since_iso(value: object) -> float | None:
    try:
        timestamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
    ).total_seconds()


def worker_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0) | int(
        getattr(subprocess, "DETACHED_PROCESS", 0) or 0
    )


def terminate_worker_process(pid: int) -> None:
    if pid <= 0 or not pid_is_alive(pid):
        return
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            return
        except OSError:
            return
        except Exception:
            pass
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except Exception:
            pass
    kill_signal = getattr(signal, "SIGKILL", None) or getattr(signal, "SIGTERM", None)
    if kill_signal is None:
        return
    try:
        os.kill(pid, kill_signal)
    except OSError:
        pass


class JobStoreProtocol(Protocol):
    def claim_queued_job(
        self,
        job_id: str,
        *,
        worker_pid: int | None = None,
        job_slot_path: str | None = None,
    ) -> dict[str, Any] | None: ...

    def list_queued_jobs(self) -> list[dict[str, Any]]: ...

    def release_due_scheduled_jobs(self) -> list[dict[str, Any]]: ...

    def get_job(self, job_id: str) -> dict[str, Any] | None: ...

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def list_queued_deep_verifications(self) -> list[dict[str, Any]]: ...

    def list_running_deep_verifications(self) -> list[dict[str, Any]]: ...

    def claim_queued_deep_verification(
        self,
        verification_id: str,
        *,
        worker_pid: int | None = None,
        job_slot_path: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_deep_verification(
        self, verification_id: str
    ) -> dict[str, Any] | None: ...

    def update_deep_verification(
        self, verification_id: str, **changes: Any
    ) -> dict[str, Any] | None: ...


class JobConcurrencyGate:
    def __init__(
        self, limit: int, slot_dir: Path, *, slot_attach_stale_seconds: float
    ) -> None:
        self.limit = max(0, int(limit or 0))
        self.slot_dir = slot_dir
        self.slot_attach_stale_seconds = max(30.0, float(slot_attach_stale_seconds))

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def acquire(self, job_id: str) -> Path | None:
        if not self.enabled:
            return None
        self.slot_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_slots()
        for slot_number in range(1, self.limit + 1):
            slot_path = self.slot_dir / f"slot-{slot_number}"
            try:
                slot_path.mkdir()
            except FileExistsError:
                continue
            metadata = {
                "job_id": job_id,
                "acquired_at": utc_now_iso(),
                "launcher_pid": os.getpid(),
                "worker_pid": None,
            }
            self._write_metadata(slot_path, metadata)
            return slot_path
        return None

    def attach_worker(self, slot_path: Path | None, worker_pid: int) -> None:
        if not slot_path:
            return
        if not slot_path.exists():
            return
        metadata = self._read_metadata(slot_path)
        metadata["worker_pid"] = int(worker_pid)
        metadata["attached_at"] = utc_now_iso()
        try:
            self._write_metadata(slot_path, metadata)
        except FileNotFoundError:
            return

    def release(
        self, slot_path: str | Path | None, *, job_id: str | None = None
    ) -> None:
        if not slot_path:
            return
        try:
            resolved_slot = Path(slot_path).resolve()
            resolved_root = self.slot_dir.resolve()
            resolved_slot.relative_to(resolved_root)
            if resolved_slot.name.startswith("slot-"):
                if job_id is not None:
                    slot_job_id = str(
                        self._read_metadata(resolved_slot).get("job_id", "")
                    ).strip()
                    if slot_job_id != str(job_id).strip():
                        return
                shutil.rmtree(resolved_slot, ignore_errors=True)
        except Exception:
            return

    def cleanup_stale_slots(self) -> None:
        if not self.slot_dir.exists():
            return
        for slot_path in self.slot_dir.glob("slot-*"):
            if not slot_path.is_dir():
                continue
            metadata = self._read_metadata(slot_path)
            worker_pid = safe_int(metadata.get("worker_pid"))
            if worker_pid and not pid_is_alive(worker_pid):
                self.release(slot_path)
                continue
            slot_age = seconds_since_iso(metadata.get("acquired_at"))
            if slot_age is None:
                try:
                    slot_age = time.time() - slot_path.stat().st_mtime
                except OSError:
                    slot_age = None
            if (
                not worker_pid
                and slot_age is not None
                and slot_age > self.slot_attach_stale_seconds
            ):
                self.release(slot_path)

    def _metadata_path(self, slot_path: Path) -> Path:
        return slot_path / "metadata.json"

    def _read_metadata(self, slot_path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(
                self._metadata_path(slot_path).read_text(encoding="utf-8")
            )
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _write_metadata(self, slot_path: Path, metadata: dict[str, Any]) -> None:
        self._metadata_path(slot_path).write_text(
            json.dumps(
                json_safe(metadata), ensure_ascii=False, indent=2, allow_nan=False
            ),
            encoding="utf-8",
        )


class JobQueueManager:
    def __init__(
        self,
        *,
        job_store: JobStoreProtocol,
        runner_path: Path,
        verification_runner_path: Path | None = None,
        base_dir: Path,
        python_executable: str | None = None,
        max_concurrent_jobs: int = 0,
        concurrency_dir: Path,
        poll_seconds: float = 5.0,
        slot_attach_stale_seconds: float = 300.0,
    ) -> None:
        self.job_store = job_store
        self.runner_path = runner_path
        self.verification_runner_path = verification_runner_path
        self.base_dir = base_dir
        self.python_executable = python_executable or sys.executable
        self.poll_seconds = max(1.0, float(poll_seconds or 5.0))
        self.gate = JobConcurrencyGate(
            max_concurrent_jobs,
            concurrency_dir,
            slot_attach_stale_seconds=slot_attach_stale_seconds,
        )
        self._scheduler_lock = threading.Lock()
        self._worker_state_lock = threading.Lock()
        self._scheduler_started = False

    def process_is_alive(self, pid: int | None) -> bool:
        return pid_is_alive(pid)

    def spawn_job_worker(self, job_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        slot_path = self.gate.acquire(normalized_job_id)
        if self.gate.enabled and slot_path is None:
            return None
        with self._worker_state_lock:
            claimed = self.job_store.claim_queued_job(
                normalized_job_id,
                job_slot_path=str(slot_path) if slot_path else None,
            )
            if not claimed:
                self.gate.release(slot_path)
                return None

            env = os.environ.copy()
            if slot_path:
                env["BRP_JOB_CONCURRENCY_SLOT"] = str(slot_path)
                env["BRP_JOB_CONCURRENCY_ROOT"] = str(self.gate.slot_dir)
            try:
                process = subprocess.Popen(
                    [
                        self.python_executable,
                        str(self.runner_path),
                        str(normalized_job_id),
                    ],
                    cwd=str(self.base_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    creationflags=worker_creation_flags(),
                )
            except Exception as exc:
                self.gate.release(slot_path)
                self.job_store.update_job(
                    normalized_job_id,
                    status="failed",
                    finished_at=utc_now_iso(),
                    error=f"Failed to start job worker: {exc}",
                    traceback=traceback.format_exc(),
                    worker_pid=None,
                    job_slot_path=None,
                    result=None,
                )
                raise

            self.gate.attach_worker(slot_path, int(process.pid))
            updated = self.job_store.update_job(
                normalized_job_id,
                worker_pid=int(process.pid),
                job_slot_path=str(slot_path) if slot_path else None,
            )
            threading.Thread(
                target=self._reap_job_worker,
                args=(normalized_job_id, process, slot_path),
                name=f"brp-job-worker-reaper-{process.pid}",
                daemon=True,
            ).start()
        return updated or self.job_store.get_job(normalized_job_id) or claimed

    def _reap_job_worker(
        self, job_id: str, process: subprocess.Popen[Any], slot_path: Path | None
    ) -> None:
        exit_code = int(process.wait())
        with self._worker_state_lock:
            job_record = self.job_store.get_job(job_id)
            if str((job_record or {}).get("status", "")).strip().lower() == "running":
                self.job_store.update_job(
                    job_id,
                    status="failed",
                    finished_at=utc_now_iso(),
                    error=(
                        f"Job worker exited with code {exit_code} before recording a terminal status."
                    ),
                    worker_exit_code=exit_code,
                    worker_pid=None,
                    job_slot_path=None,
                )
            self.gate.release(
                (job_record or {}).get("job_slot_path") or slot_path,
                job_id=job_id,
            )
            self.gate.cleanup_stale_slots()
        self.schedule_queued_jobs()

    def spawn_deep_verification_worker(
        self, verification_id: str
    ) -> dict[str, Any] | None:
        normalized_id = str(verification_id or "").strip()
        if not normalized_id or self.verification_runner_path is None:
            return None
        slot_owner_id = f"deep:{normalized_id}"
        slot_path = self.gate.acquire(slot_owner_id)
        if self.gate.enabled and slot_path is None:
            return None
        with self._worker_state_lock:
            claimed = self.job_store.claim_queued_deep_verification(
                normalized_id,
                job_slot_path=str(slot_path) if slot_path else None,
            )
            if not claimed:
                self.gate.release(slot_path)
                return None
            env = os.environ.copy()
            if slot_path:
                env["BRP_JOB_CONCURRENCY_SLOT"] = str(slot_path)
                env["BRP_JOB_CONCURRENCY_ROOT"] = str(self.gate.slot_dir)
            try:
                process = subprocess.Popen(
                    [
                        self.python_executable,
                        str(self.verification_runner_path),
                        normalized_id,
                    ],
                    cwd=str(self.base_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    creationflags=worker_creation_flags(),
                )
            except Exception as exc:
                self.gate.release(slot_path)
                self.job_store.update_deep_verification(
                    normalized_id,
                    status="queued",
                    error=f"Failed to start deep-verification worker: {exc}",
                    worker_pid=None,
                    job_slot_path=None,
                )
                raise
            self.gate.attach_worker(slot_path, int(process.pid))
            updated = self.job_store.update_deep_verification(
                normalized_id,
                worker_pid=int(process.pid),
                job_slot_path=str(slot_path) if slot_path else None,
            )
            threading.Thread(
                target=self._reap_deep_verification_worker,
                args=(normalized_id, process, slot_path),
                name=f"brp-deep-verification-reaper-{process.pid}",
                daemon=True,
            ).start()
        return updated or self.job_store.get_deep_verification(normalized_id) or claimed

    def _reap_deep_verification_worker(
        self,
        verification_id: str,
        process: subprocess.Popen[Any],
        slot_path: Path | None,
    ) -> None:
        exit_code = int(process.wait())
        exited_worker_pid = safe_int(getattr(process, "pid", 0), 0)
        slot_owner_id = f"deep:{verification_id}"
        with self._worker_state_lock:
            record = self.job_store.get_deep_verification(verification_id)
            status = str((record or {}).get("status") or "").strip().lower()
            record_worker_pid = safe_int((record or {}).get("worker_pid"), 0)
            newer_worker_running = (
                status == "running"
                and exited_worker_pid > 0
                and record_worker_pid > 0
                and record_worker_pid != exited_worker_pid
            )
            if not newer_worker_running:
                if status == "running":
                    crash_count = safe_int((record or {}).get("worker_crash_count"), 0) + 1
                    terminal = crash_count >= 3
                    self.job_store.update_deep_verification(
                        verification_id,
                        status="technical_failure" if terminal else "queued",
                        finished_at=utc_now_iso() if terminal else None,
                        error=(
                            f"Deep-verification worker exited with code {exit_code} "
                            "before saving its checkpoint."
                        ),
                        worker_exit_code=exit_code,
                        worker_crash_count=crash_count,
                        worker_pid=None,
                        job_slot_path=None,
                    )
                self.gate.release(slot_path, job_id=slot_owner_id)
                self.gate.cleanup_stale_slots()
        self.schedule_queued_jobs()

    def _preempt_running_deep_verification(self) -> bool:
        list_running = getattr(
            self.job_store, "list_running_deep_verifications", lambda: []
        )
        running = list_running()
        if not running:
            return False
        record = running[0]
        verification_id = str(record.get("verification_id") or "").strip()
        if not verification_id:
            return False
        slot_owner_id = f"deep:{verification_id}"
        with self._worker_state_lock:
            terminate_worker_process(safe_int(record.get("worker_pid"), 0))
            self.gate.release(record.get("job_slot_path"), job_id=slot_owner_id)
            self.gate.cleanup_stale_slots()
            self.job_store.update_deep_verification(
                verification_id,
                status="queued",
                worker_pid=None,
                job_slot_path=None,
                last_preempted_at=utc_now_iso(),
                preemption_count=safe_int(record.get("preemption_count"), 0) + 1,
            )
        return True

    def schedule_queued_jobs(self) -> None:
        if not self._scheduler_lock.acquire(blocking=False):
            return
        try:
            self.gate.cleanup_stale_slots()
            self.job_store.release_due_scheduled_jobs()
            normal_queue_blocked = False
            for job_record in self.job_store.list_queued_jobs():
                spawned = self.spawn_job_worker(str(job_record.get("job_id", "")))
                if spawned is None and self.gate.enabled:
                    if self._preempt_running_deep_verification():
                        spawned = self.spawn_job_worker(
                            str(job_record.get("job_id", ""))
                        )
                if spawned is None and self.gate.enabled:
                    normal_queue_blocked = True
                    break
            if normal_queue_blocked:
                return
            list_queued_verifications = getattr(
                self.job_store, "list_queued_deep_verifications", lambda: []
            )
            for record in list_queued_verifications():
                self.spawn_deep_verification_worker(
                    str(record.get("verification_id") or "")
                )
                break
        finally:
            self._scheduler_lock.release()

    def scheduler_loop(self) -> None:
        while True:
            try:
                self.schedule_queued_jobs()
            except Exception:
                traceback.print_exc()
            time.sleep(self.poll_seconds)

    def start(self) -> None:
        if self._scheduler_started:
            return
        self._scheduler_started = True
        threading.Thread(
            target=self.scheduler_loop, name="brp-job-scheduler", daemon=True
        ).start()

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self._worker_state_lock:
            job_record = self.job_store.get_job(job_id)
            if not job_record:
                return None
            status = str(job_record.get("status", "")).strip().lower()
            pid = safe_int(job_record.get("worker_pid"))
            if status in {"succeeded", "failed", "canceled"}:
                return job_record
            terminate_worker_process(pid)
            self.gate.release(job_record.get("job_slot_path"), job_id=job_id)
            self.gate.cleanup_stale_slots()
            preserve_result = (
                str(dict(job_record.get("metadata") or {}).get("job_kind") or "").strip().lower()
                == "direct_school_analysis"
            )
            updated = self.job_store.update_job(
                job_id,
                status="canceled",
                finished_at=utc_now_iso(),
                error="Job was canceled by the user.",
                traceback=None,
                worker_pid=None,
                job_slot_path=None,
                result=job_record.get("result") if preserve_result else None,
            )
        self.schedule_queued_jobs()
        return updated

    def set_deep_verification_status(
        self, verification_id: str, action: str
    ) -> dict[str, Any] | None:
        normalized_id = str(verification_id or "").strip()
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"pause", "resume", "cancel"}:
            raise ValueError(f"Unsupported deep-verification action: {action}")
        slot_owner_id = f"deep:{normalized_id}"
        with self._worker_state_lock:
            record = self.job_store.get_deep_verification(normalized_id)
            if not record:
                return None
            status = str(record.get("status") or "").strip().lower()
            if normalized_action == "resume":
                if status != "paused":
                    return record
                updated = self.job_store.update_deep_verification(
                    normalized_id,
                    status="queued",
                    finished_at=None,
                    error=None,
                    worker_pid=None,
                    job_slot_path=None,
                )
            else:
                terminate_worker_process(safe_int(record.get("worker_pid"), 0))
                self.gate.release(
                    record.get("job_slot_path"), job_id=slot_owner_id
                )
                self.gate.cleanup_stale_slots()
                updated = self.job_store.update_deep_verification(
                    normalized_id,
                    status="paused" if normalized_action == "pause" else "canceled",
                    finished_at=(
                        None if normalized_action == "pause" else utc_now_iso()
                    ),
                    completion_reason=(
                        "paused_by_admin"
                        if normalized_action == "pause"
                        else "canceled_by_admin"
                    ),
                    worker_pid=None,
                    job_slot_path=None,
                )
        self.schedule_queued_jobs()
        return updated
