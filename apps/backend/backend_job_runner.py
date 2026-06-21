from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import json
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


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _runtime_store_mode() -> str:
    mode = (os.environ.get("BRP_RUNTIME_STORE", "json").strip().lower() or "json")
    return mode if mode in {"json", "dual", "sqlite"} else "json"


def _runtime_db_path(job_path: Path) -> Path:
    configured = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return job_path.parent.parent / "brp_runtime.sqlite"


def _mirror_runtime_job(job_path: Path, payload: dict[str, Any]) -> None:
    if _runtime_store_mode() not in {"dual", "sqlite"}:
        return
    try:
        store = SqliteRuntimeStore(_runtime_db_path(job_path))
        store.upsert_job(payload)
    except Exception:
        traceback.print_exc()


def _index_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(record.get("job_id", "") or ""),
        "owner_email": str(record.get("owner_email", "") or "").strip().lower(),
        "status": str(record.get("status", "queued") or "queued"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "metadata": dict(record.get("metadata") or {}),
        "prepared_payload_summary": dict(record.get("prepared_payload_summary") or {}),
        "error": record.get("error"),
    }


def _upsert_index(job_path: Path, record: dict[str, Any]) -> None:
    job_id = str(record.get("job_id", "") or "").strip()
    if not job_id:
        return
    index_path = job_path.parent / "index.json"
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else []
    except Exception:
        entries = []
    summary = _index_summary(record)
    updated = False
    for idx, entry in enumerate(entries):
        if str(dict(entry).get("job_id", "") or "").strip() == job_id:
            entries[idx] = summary
            updated = True
            break
    if not updated:
        entries.insert(0, summary)
    index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_job(job_path: Path, payload: dict[str, Any]) -> None:
    _save_json(job_path, payload)
    _upsert_index(job_path, payload)
    _mirror_runtime_job(job_path, payload)


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
        raise SystemExit("Usage: backend_job_runner.py <job_json_path>")
    job_path = Path(sys.argv[1]).resolve()
    if not job_path.exists():
        raise SystemExit(f"Job file does not exist: {job_path}")

    job_record = _load_json(job_path)
    if str(job_record.get("status", "")).strip().lower() == "canceled":
        _release_concurrency_slot()
        return 0

    job_record["status"] = "running"
    job_record["started_at"] = job_record.get("started_at") or utc_now_iso()
    job_record["worker_pid"] = int(os.getpid())
    job_record["job_slot_path"] = os.environ.get("BRP_JOB_CONCURRENCY_SLOT", "").strip() or job_record.get("job_slot_path")
    _save_job(job_path, job_record)

    try:
        config = _build_planner_config(job_record.get("config") or {})
        prepared_payload = dict(job_record.get("prepared_payload") or {})
        result = run_backend_planner_with_prepared_data(prepared_payload, config=config)
        job_record = _load_json(job_path)
        if str(job_record.get("status", "")).strip().lower() == "canceled":
            return 0
        job_record["status"] = "succeeded"
        job_record["finished_at"] = utc_now_iso()
        job_record["result"] = result
        job_record["error"] = None
        job_record["traceback"] = None
        job_record["worker_pid"] = None
        job_record["job_slot_path"] = None
        _save_job(job_path, job_record)
        return 0
    except Exception as exc:
        job_record = _load_json(job_path)
        if str(job_record.get("status", "")).strip().lower() == "canceled":
            return 0
        job_record["status"] = "failed"
        job_record["finished_at"] = utc_now_iso()
        job_record["result"] = None
        job_record["error"] = str(exc)
        job_record["traceback"] = traceback.format_exc()
        job_record["worker_pid"] = None
        job_record["job_slot_path"] = None
        _save_job(job_path, job_record)
        return 1
    finally:
        _release_concurrency_slot()


if __name__ == "__main__":
    raise SystemExit(main())
