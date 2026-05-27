from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import traceback
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

try:
    from .planner_core import PlannerConfig, run_backend_planner_with_prepared_data
    from .ai_audit import generate_ai_audit_report
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from planner_core import PlannerConfig, run_backend_planner_with_prepared_data
    from ai_audit import generate_ai_audit_report


BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("BRP_BACKEND_JOBS_DIR", str(BASE_DIR / "jobs"))).expanduser()
JOB_RUNNER_PATH = BASE_DIR / "backend_job_runner.py"
SERVICE_TOKEN = os.environ.get("BRP_BACKEND_SERVICE_TOKEN", "").strip()
DEV_USER_EMAIL = os.environ.get("BRP_DEV_USER_EMAIL", "local@brp.dev").strip().lower()
ADMIN_EMAILS = {
    item.strip().lower()
    for item in os.environ.get("BRP_ADMIN_EMAILS", "").split(",")
    if item.strip()
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_planner_config(config_payload: dict[str, Any]) -> PlannerConfig:
    allowed_field_names = {field.name for field in fields(PlannerConfig)}
    filtered_payload = {
        key: value
        for key, value in dict(config_payload or {}).items()
        if key in allowed_field_names
    }
    return PlannerConfig(**filtered_payload)


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _is_admin_email(email: str) -> bool:
    normalized_email = _normalize_email(email)
    return bool(normalized_email and normalized_email in ADMIN_EMAILS)


def _summarize_prepared_payload(prepared_payload: dict[str, Any]) -> dict[str, Any]:
    input_records = list(prepared_payload.get("input_records") or [])
    current_plan = dict(prepared_payload.get("current_plan") or {})
    current_plan_summary = dict(current_plan.get("summary") or {})
    return {
        "input_record_count": len(input_records),
        "country_samples": sorted(
            {
                str(item.get("country", "")).strip()
                for item in input_records
                if str(item.get("country", "")).strip()
            }
        )[:10],
        "city_samples": sorted(
            {
                str(item.get("city", "")).strip()
                for item in input_records
                if str(item.get("city", "")).strip()
            }
        )[:10],
        "has_current_plan": bool(current_plan),
        "current_plan_route_count": int(current_plan_summary.get("route_count", 0) or 0),
        "current_plan_assignment_count": int(current_plan_summary.get("assignment_count", 0) or 0),
    }


class JobStore:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.index_path = jobs_dir / "index.json"
        self.lock = threading.Lock()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("[]", encoding="utf-8")
        self.reconcile_running_jobs()

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _load_index_unlocked(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
        except Exception:
            pass
        return []

    def _save_index_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_job_unlocked(self, job_id: str) -> dict[str, Any] | None:
        job_path = self._job_path(job_id)
        if not job_path.exists():
            return None
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _save_job_unlocked(self, job_id: str, record: dict[str, Any]) -> None:
        self._job_path(job_id).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def _upsert_index_entry_unlocked(self, record: dict[str, Any]) -> None:
        job_id = str(record.get("job_id", "")).strip()
        index_entries = self._load_index_unlocked()
        summary = {
            "job_id": job_id,
            "owner_email": _normalize_email(record.get("owner_email")),
            "status": str(record.get("status", "queued")),
            "created_at": record.get("created_at"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "metadata": deepcopy(record.get("metadata") or {}),
            "prepared_payload_summary": deepcopy(record.get("prepared_payload_summary") or {}),
            "error": record.get("error"),
        }
        updated = False
        for idx, entry in enumerate(index_entries):
            if str(entry.get("job_id", "")).strip() == job_id:
                index_entries[idx] = summary
                updated = True
                break
        if not updated:
            index_entries.insert(0, summary)
        self._save_index_unlocked(index_entries)

    def create_job(
        self,
        config_payload: dict[str, Any],
        prepared_payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        owner_email: str = "",
    ) -> dict[str, Any]:
        job_id = uuid4().hex[:12]
        created_at = utc_now_iso()
        normalized_owner_email = _normalize_email(owner_email)
        record = {
            "job_id": job_id,
            "owner_email": normalized_owner_email,
            "status": "queued",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "worker_pid": None,
            "config": deepcopy(config_payload or {}),
            "prepared_payload": deepcopy(prepared_payload or {}),
            "prepared_payload_summary": _summarize_prepared_payload(prepared_payload or {}),
            "metadata": deepcopy(metadata or {}),
            "result": None,
            "error": None,
            "traceback": None,
        }
        with self.lock:
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
        return {
            "job_id": job_id,
            "owner_email": normalized_owner_email,
            "status": "queued",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "metadata": deepcopy(metadata or {}),
            "prepared_payload_summary": record["prepared_payload_summary"],
            "error": None,
        }

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            if not record:
                return None
            record.update(changes)
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
            return deepcopy(record)

    def begin_ai_audit(self, job_id: str, *, force: bool = False) -> tuple[str, dict[str, Any] | None]:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            if not record:
                return "missing", None
            existing_report = record.get("ai_audit_report")
            if isinstance(existing_report, dict) and existing_report and not force:
                return "cached", deepcopy(record)
            if str(record.get("ai_audit_status", "")).strip().lower() == "running":
                return "running", deepcopy(record)
            record["ai_audit_status"] = "running"
            record["ai_audit_started_at"] = utc_now_iso()
            record["ai_audit_finished_at"] = None
            record["ai_audit_error"] = None
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
            return "started", deepcopy(record)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            return deepcopy(record) if record else None

    def list_jobs(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            entries = self._load_index_unlocked()
            if include_all:
                return deepcopy(entries)
            normalized_user_email = _normalize_email(user_email)
            return [
                deepcopy(entry)
                for entry in entries
                if _normalize_email(entry.get("owner_email")) == normalized_user_email
            ]

    def delete_job(self, job_id: str) -> bool:
        with self.lock:
            job_path = self._job_path(job_id)
            if not job_path.exists():
                return False
            job_path.unlink(missing_ok=True)
            index_entries = [
                entry
                for entry in self._load_index_unlocked()
                if str(entry.get("job_id", "")).strip() != job_id
            ]
            self._save_index_unlocked(index_entries)
            return True

    def reconcile_running_jobs(self) -> None:
        with self.lock:
            index_entries = self._load_index_unlocked()
            for entry in index_entries:
                job_id = str(entry.get("job_id", "")).strip()
                if not job_id:
                    continue
                record = self._load_job_unlocked(job_id)
                if not record:
                    continue
                if str(record.get("status", "")).strip() in {"queued", "running"}:
                    record["status"] = "failed"
                    record["finished_at"] = utc_now_iso()
                    record["error"] = "Job was interrupted because the backend service restarted."
                    record["traceback"] = None
                    record["worker_pid"] = None
                    self._save_job_unlocked(job_id, record)
            refreshed_entries = []
            for entry in self._load_index_unlocked():
                job_id = str(entry.get("job_id", "")).strip()
                record = self._load_job_unlocked(job_id) if job_id else None
                if record:
                    refreshed_entries.append(
                        {
                            "job_id": job_id,
                            "owner_email": _normalize_email(record.get("owner_email")),
                            "status": str(record.get("status", "queued")),
                            "created_at": record.get("created_at"),
                            "started_at": record.get("started_at"),
                            "finished_at": record.get("finished_at"),
                            "metadata": deepcopy(record.get("metadata") or {}),
                            "prepared_payload_summary": deepcopy(record.get("prepared_payload_summary") or {}),
                            "error": record.get("error"),
                        }
                    )
            self._save_index_unlocked(refreshed_entries)


JOB_STORE = JobStore(JOBS_DIR)


def _process_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn_job_worker(job_id: str) -> dict[str, Any] | None:
    job_record = JOB_STORE.get_job(job_id)
    if not job_record:
        return None
    process = subprocess.Popen(
        [sys.executable, str(JOB_RUNNER_PATH), str(JOBS_DIR / f"{job_id}.json")],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return JOB_STORE.update_job(job_id, worker_pid=int(process.pid))


def _cancel_job(job_id: str) -> dict[str, Any] | None:
    job_record = JOB_STORE.get_job(job_id)
    if not job_record:
        return None
    status = str(job_record.get("status", "")).strip().lower()
    pid = int(job_record.get("worker_pid", 0) or 0)
    if status in {"succeeded", "failed", "canceled"}:
        return job_record
    if _process_is_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return JOB_STORE.update_job(
        job_id,
        status="canceled",
        finished_at=utc_now_iso(),
        error="Job was canceled by the user.",
        traceback=None,
        worker_pid=None,
        result=None,
    )


def _can_access_job(job_record: dict[str, Any], user_email: str, include_all: bool = False) -> bool:
    if include_all:
        return True
    return _normalize_email(job_record.get("owner_email")) == _normalize_email(user_email)


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "BusingRoutingBackend/1.0"

    def _send_json(self, status_code: int, payload: dict[str, Any] | list[Any]) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            print(f"[WARN] Client disconnected before response was fully sent: {self.path}")
            return False

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        return payload if isinstance(payload, dict) else {}

    def _current_user_email(self) -> str:
        return (
            _normalize_email(self.headers.get("X-BRP-User-Email"))
            or _normalize_email(self.headers.get("Cf-Access-Authenticated-User-Email"))
            or DEV_USER_EMAIL
        )

    def _is_authorized_request(self) -> bool:
        if not SERVICE_TOKEN:
            return True
        authorization = str(self.headers.get("Authorization", "") or "").strip()
        expected = f"Bearer {SERVICE_TOKEN}"
        return authorization == expected

    def _require_authorized_request(self) -> bool:
        if self._is_authorized_request():
            return True
        self._send_json(401, {"error": "Unauthorized backend request."})
        return False

    def _handle_sync_compute(self, payload: dict[str, Any]) -> None:
        config_payload = payload.get("config") or {}
        prepared_payload = payload.get("prepared_payload") or {}
        config = _build_planner_config(config_payload)
        result = run_backend_planner_with_prepared_data(prepared_payload, config=config)
        self._send_json(200, result)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if not self._require_authorized_request():
            return
        user_email = self._current_user_email()
        include_all = _is_admin_email(user_email)
        if path == "/jobs":
            self._send_json(200, {"jobs": JOB_STORE.list_jobs(user_email=user_email, include_all=include_all)})
            return
        if path.startswith("/jobs/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) != 2:
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            job_id = parts[1].strip()
            job_record = JOB_STORE.get_job(job_id)
            if not job_record:
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            if not _can_access_job(job_record, user_email, include_all=include_all):
                self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                return
            self._send_json(200, job_record)
            return
        self._send_json(404, {"error": f"Unknown path: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._require_authorized_request():
                return
            user_email = self._current_user_email()
            include_all = _is_admin_email(user_email)
            payload = self._read_json_body()
            if path == "/compute":
                self._handle_sync_compute(payload)
                return
            if path == "/jobs":
                config_payload = payload.get("config") or {}
                prepared_payload = payload.get("prepared_payload") or {}
                metadata = payload.get("metadata") or {}
                summary = JOB_STORE.create_job(
                    config_payload,
                    prepared_payload,
                    metadata=metadata,
                    owner_email=user_email,
                )
                spawned = _spawn_job_worker(str(summary["job_id"]))
                if spawned:
                    summary["worker_pid"] = spawned.get("worker_pid")
                self._send_json(202, summary)
                return
            if path.startswith("/jobs/") and path.endswith("/cancel"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 3:
                    self._send_json(404, {"error": f"Unknown path: {path}"})
                    return
                job_id = parts[1].strip()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                updated = _cancel_job(job_id)
                if not updated:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                self._send_json(200, updated)
                return
            if path.startswith("/jobs/") and path.endswith("/ai-audit"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 3:
                    self._send_json(404, {"error": f"Unknown path: {path}"})
                    return
                job_id = parts[1].strip()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                force_ai_audit = bool(payload.get("force"))
                audit_state, audit_record = JOB_STORE.begin_ai_audit(job_id, force=force_ai_audit)
                if audit_state == "missing" or not audit_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if audit_state == "cached":
                    self._send_json(
                        200,
                        {
                            "job_id": job_id,
                            "ai_audit_status": "succeeded",
                            "ai_audit_report": dict(audit_record.get("ai_audit_report") or {}),
                            "cached": True,
                        },
                    )
                    return
                if audit_state == "running":
                    self._send_json(
                        202,
                        {
                            "job_id": job_id,
                            "ai_audit_status": "running",
                            "ai_audit_report": dict(audit_record.get("ai_audit_report") or {}),
                            "message": "AI audit generation is already running for this job.",
                        },
                    )
                    return
                try:
                    ai_report = generate_ai_audit_report(
                        audit_record,
                        force=force_ai_audit,
                        language=str(payload.get("language") or "").strip() or None,
                    )
                except Exception as exc:
                    JOB_STORE.update_job(
                        job_id,
                        ai_audit_status="failed",
                        ai_audit_finished_at=utc_now_iso(),
                        ai_audit_error=str(exc),
                    )
                    raise
                updated = JOB_STORE.update_job(
                    job_id,
                    ai_audit_report=ai_report,
                    ai_audit_status="succeeded",
                    ai_audit_finished_at=utc_now_iso(),
                    ai_audit_error=None,
                )
                if updated:
                    updated["config"] = None
                    updated["prepared_payload"] = None
                    self._send_json(200, {"job_id": job_id, "ai_audit_status": "succeeded", "ai_audit_report": ai_report})
                    return
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            self._send_json(404, {"error": f"Unknown path: {path}"})
        except Exception as exc:
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                print(f"[WARN] Client disconnected during request handling: {self.path}")
                return
            self._send_json(
                500,
                {
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._require_authorized_request():
                return
            user_email = self._current_user_email()
            include_all = _is_admin_email(user_email)
            if not path.startswith("/jobs/"):
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) != 2:
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            job_id = parts[1].strip()
            job_record = JOB_STORE.get_job(job_id)
            if not job_record:
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            if not _can_access_job(job_record, user_email, include_all=include_all):
                self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                return
            _cancel_job(job_id)
            JOB_STORE.delete_job(job_id)
            self._send_json(200, {"deleted": True, "job_id": job_id})
        except Exception as exc:
            self._send_json(
                500,
                {
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )


def main(host: str = "127.0.0.1", port: int = 8001) -> None:
    server = ThreadingHTTPServer((host, port), BackendHandler)
    print(f"Backend listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    host = os.environ.get("BRP_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.environ.get("BRP_BACKEND_PORT", "8001").strip() or "8001"
    main(host=host, port=int(raw_port))
