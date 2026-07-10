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


def _is_scheduled_job(job_record: dict[str, Any]) -> bool:
    metadata = dict(job_record.get("metadata") or {})
    return bool(metadata.get("scheduled_job") or job_record.get("scheduled_start_at"))


def _scheduled_final_traffic_validation_error(result: dict[str, Any] | None) -> str | None:
    result = dict(result or {})
    structured = dict(result.get("structured_results") or {})
    refresh = dict(
        structured.get("scheduled_run_traffic_refresh")
        or result.get("scheduled_run_traffic_refresh")
        or {}
    )
    refresh_provider = str(refresh.get("provider") or "none").strip().lower()
    supported_providers = {"amap", "kakao_navi"}
    current_plan_routes = list(dict(structured.get("current_plan") or {}).get("routes") or [])
    if not current_plan_routes:
        return "scheduled result is missing current-plan routes"
    if refresh_provider in supported_providers:
        if str(refresh.get("status") or "").strip().lower() != "ready":
            return "scheduled fresh traffic refresh was unavailable"
        if int(refresh.get("api_calls", 0) or 0) <= 0:
            return "scheduled fresh traffic refresh made no provider API calls"

    required_gate_count = 0
    for scenario_key in (
        "current_plan",
        "original",
        "subway",
        "nearby",
        "time_constrained",
        "exception_preserving",
        "ep15min",
    ):
        scenario = dict(structured.get(scenario_key) or {})
        routes = list(scenario.get("routes") or [])
        if not routes:
            continue
        gate = dict(scenario.get("traffic_gate") or {})
        policy = dict(gate.get("traffic_policy") or {})
        provider = str(gate.get("provider") or policy.get("provider") or "none").strip().lower()
        if provider not in supported_providers:
            if refresh_provider in supported_providers:
                return f"scheduled final traffic gate unavailable for {scenario_key}"
            continue
        required_gate_count += 1
        status = str(gate.get("status") or "").strip().lower()
        if status not in {"passed", "failed"} or int(gate.get("unavailable_route_count", 0) or 0) > 0:
            return f"scheduled final traffic gate unavailable for {scenario_key}"
        if int(gate.get("checked_route_count", 0) or 0) < len(routes):
            return f"scheduled final traffic gate did not check every route for {scenario_key}"

    if required_gate_count and refresh_provider not in supported_providers:
        return "scheduled result lacks fresh provider traffic evidence"
    return None


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

    result: dict[str, Any] | None = None
    try:
        config = _build_planner_config(job_record.get("config") or {})
        prepared_payload = dict(job_record.get("prepared_payload") or {})
        if _is_scheduled_job(job_record):
            result = run_backend_planner_with_prepared_data(
                prepared_payload,
                config=config,
                require_fresh_final_traffic=True,
            )
            validation_error = _scheduled_final_traffic_validation_error(result)
            if validation_error:
                raise RuntimeError(validation_error)
        else:
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
        job_record["result"] = result
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
