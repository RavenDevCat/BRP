from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any

try:
    from .planner_core import PlannerConfig, run_backend_planner_with_prepared_data
except ImportError:  # pragma: no cover - supports running as a direct script.
    from planner_core import PlannerConfig, run_backend_planner_with_prepared_data


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        return 0

    job_record["status"] = "running"
    job_record["started_at"] = job_record.get("started_at") or utc_now_iso()
    job_record["worker_pid"] = int(os.getpid())
    _save_json(job_path, job_record)

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
        _save_json(job_path, job_record)
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
        _save_json(job_path, job_record)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
