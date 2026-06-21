from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

try:
    from .planner_core import PlannerConfig, run_backend_planner_with_prepared_data
    from .runtime_store_sqlite import SqliteRuntimeStore
except ImportError:  # pragma: no cover - supports running as a direct script.
    from planner_core import PlannerConfig, run_backend_planner_with_prepared_data
    from runtime_store_sqlite import SqliteRuntimeStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_db_path() -> Path:
    configured = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "state" / "brp_runtime.sqlite"


def _runtime_store() -> SqliteRuntimeStore:
    store = SqliteRuntimeStore(_runtime_db_path())
    store.initialize()
    return store


def _load_job(job_id: str) -> dict[str, Any]:
    payload = _runtime_store().get_job(job_id)
    if not payload:
        raise SystemExit(f"Job not found in runtime store: {job_id}")
    return payload


def _save_job(payload: dict[str, Any]) -> None:
    _runtime_store().upsert_job(payload)


def _release_concurrency_slot() -> None:
    slot_path = os.environ.get("BRP_JOB_CONCURRENCY_SLOT", "").strip()
    if not slot_path:
        return
    try:
        resolved_slot = Path(slot_path).resolve()
        root_path = os.environ.get("BRP_JOB_CONCURRENCY_ROOT", "").strip()
        if root_path:
            resolved_slot.relative_to(Path(root_path).resolve())
        if not resolved_slot.name.startswith("slot-"):
            return
        if not (resolved_slot / "metadata.json").exists():
            return
        shutil.rmtree(resolved_slot, ignore_errors=True)
    except Exception:
        return


def _build_planner_config(config_payload: dict[str, Any]) -> PlannerConfig:
    allowed_field_names = {field.name for field in fields(PlannerConfig)}
    filtered_payload = {
        key: value
        for key, value in dict(config_payload or {}).items()
        if key in allowed_field_names
    }
    return PlannerConfig(**filtered_payload)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: backend_job_runner.py <job_id>")
    job_id = str(sys.argv[1] or "").strip()
    if not job_id:
        raise SystemExit("job_id is required")

    job_record = _load_job(job_id)
    if str(job_record.get("status", "")).strip().lower() == "canceled":
        _release_concurrency_slot()
        return 0

    job_record["status"] = "running"
    job_record["started_at"] = job_record.get("started_at") or utc_now_iso()
    job_record["worker_pid"] = int(os.getpid())
    job_record["job_slot_path"] = os.environ.get("BRP_JOB_CONCURRENCY_SLOT", "").strip() or job_record.get("job_slot_path")
    _save_job(job_record)

    try:
        config = _build_planner_config(job_record.get("config") or {})
        prepared_payload = dict(job_record.get("prepared_payload") or {})
        result = run_backend_planner_with_prepared_data(prepared_payload, config=config)
        job_record = _load_job(job_id)
        if str(job_record.get("status", "")).strip().lower() == "canceled":
            return 0
        job_record["status"] = "succeeded"
        job_record["finished_at"] = utc_now_iso()
        job_record["result"] = result
        job_record["error"] = None
        job_record["traceback"] = None
        job_record["worker_pid"] = None
        job_record["job_slot_path"] = None
        _save_job(job_record)
        return 0
    except Exception as exc:
        job_record = _load_job(job_id)
        if str(job_record.get("status", "")).strip().lower() == "canceled":
            return 0
        job_record["status"] = "failed"
        job_record["finished_at"] = utc_now_iso()
        job_record["result"] = None
        job_record["error"] = str(exc)
        job_record["traceback"] = traceback.format_exc()
        job_record["worker_pid"] = None
        job_record["job_slot_path"] = None
        _save_job(job_record)
        return 1
    finally:
        _release_concurrency_slot()


if __name__ == "__main__":
    raise SystemExit(main())
