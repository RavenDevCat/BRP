from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from functools import lru_cache
import sys
import traceback
from typing import Any

try:
    from .direct_school_analysis import run_direct_school_analysis
    from .planner_core import (build_planner_config, run_backend_planner_with_prepared_data,
                               scheduled_traffic_provider, partial_traffic_route_ids, measured_route_gate)
    from .runtime_store_sqlite import SqliteRuntimeStore
except ImportError:  # pragma: no cover - supports running as a direct script.
    from direct_school_analysis import run_direct_school_analysis
    from planner_core import (build_planner_config, run_backend_planner_with_prepared_data,
                              scheduled_traffic_provider, partial_traffic_route_ids, measured_route_gate)
    from runtime_store_sqlite import SqliteRuntimeStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_db_path() -> Path:
    configured = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "state" / "brp_runtime.sqlite"


@lru_cache(maxsize=1)
def _worker_store_at(path: Path) -> SqliteRuntimeStore:
    return SqliteRuntimeStore(path)


def _runtime_store() -> SqliteRuntimeStore:
    return _worker_store_at(_runtime_db_path())


def _load_job(job_id: str) -> dict[str, Any]:
    payload = _runtime_store().get_job(job_id)
    if not payload:
        raise SystemExit(f"Job not found in runtime store: {job_id}")
    return payload


class SupersededAttempt(Exception):
    pass


def _save_job(payload: dict[str, Any]) -> None:
    token = os.environ.get("BRP_JOB_RUN_TOKEN")
    if not token or payload.get("worker_token") != token:
        raise SupersededAttempt()
    meta = payload.get("metadata") or {}
    review = (payload.get("status") == "succeeded" and meta.get("job_kind", "route_audit") == "route_audit"
              and meta.get("measurement_kind") != "pm_return")
    fields = {k:payload[k] for k in ("status", "started_at", "finished_at", "worker_pid", "job_slot_path",
                                    "result", "error", "traceback", "updated_at") if k in payload}
    if meta.get("failure_details") or meta.get("measurement_kind") == "pm_return":
        fields["metadata"] = meta
        if meta.get("measurement_kind") == "pm_return":
            fields["prepared_payload"] = payload["prepared_payload"]
    if _runtime_store().change_existing_job(payload["job_id"], fields, expected_status="running",
                                            token=token, automatic_review=review) is None:
        raise SupersededAttempt()


def _is_scheduled_job(job_record: dict[str, Any]) -> bool:
    metadata = dict(job_record.get("metadata") or {})
    return bool(metadata.get("scheduled_job") or job_record.get("scheduled_start_at"))


def _scheduled_final_traffic_validation_error(result: dict[str, Any] | None, *, expected_provider: str) -> str | None:
    result = result if isinstance(result, dict) else {}
    structured = dict(result.get("structured_results") or {})
    refresh = dict(
        structured.get("scheduled_run_traffic_refresh")
        or result.get("scheduled_run_traffic_refresh")
        or {}
    )
    refresh_provider = str(refresh.get("provider") or "none").strip().lower()
    current_plan_routes = list(dict(structured.get("current_plan") or {}).get("routes") or [])
    if not current_plan_routes:
        return "scheduled result is missing current-plan routes"
    if refresh_provider != expected_provider:
        return f"scheduled result lacks fresh {expected_provider} traffic evidence"
    if expected_provider != "none":
        if refresh.get("status") not in {"ready", "partial"}:
            return "scheduled fresh traffic refresh was unavailable"
        if int(refresh.get("api_calls", 0) or 0) <= 0:
            return "scheduled fresh traffic refresh made no provider API calls"

    measured_count = 0
    incomplete_scenarios = []
    for scenario_key in (
        "current_plan",
        "time_constrained",
        "exception_preserving",
    ):
        scenario = dict(structured.get(scenario_key) or {})
        routes = list(scenario.get("routes") or [])
        if not routes:
            continue
        gate = dict(scenario.get("traffic_gate") or {})
        policy = dict(gate.get("traffic_policy") or {})
        provider = str(gate.get("provider") or policy.get("provider") or "none").strip().lower()
        if provider != expected_provider:
            return f"scheduled final traffic gate unavailable for {scenario_key}"
        if expected_provider == "none":
            continue
        status = str(gate.get("status") or "").strip().lower()
        unavailable = int(gate.get("unavailable_route_count", 0) or 0)
        checked = int(gate.get("checked_route_count", 0) or 0)
        if unavailable > 0:
            missing = partial_traffic_route_ids(scenario, gate)
            if missing is None:
                return f"scheduled final traffic gate unavailable for {scenario_key}"
            incomplete_scenarios.append({"scenario": scenario_key, "checked_route_count": checked,
                "unavailable_route_count": unavailable,
                "route_ids": missing})
        elif status not in {"passed", "failed"}:
            return f"scheduled final traffic gate unavailable for {scenario_key}"
        elif checked != len(routes) or not all(measured_route_gate(r.get("final_route_traffic_gate") or {}) for r in routes):
            return f"scheduled final traffic gate did not check every route for {scenario_key}"
        measured_count += checked

    if refresh.get("status") == "partial":
        original = next((s for s in incomplete_scenarios if s["scenario"] == "current_plan"), {})
        if (not original or original["checked_route_count"] != refresh.get("measured_route_count")
                or original["route_ids"] != refresh.get("unavailable_route_ids")):
            return "scheduled partial refresh does not match current-plan evidence"
    if incomplete_scenarios and not measured_count:
        return "scheduled final traffic gate unavailable for all routes"
    if incomplete_scenarios:
        result["traffic_validation_summary"] = {
            "status": "partial", "checked_route_count": measured_count,
            "unavailable_route_count": sum(item["unavailable_route_count"] for item in incomplete_scenarios),
            "incomplete_scenarios": incomplete_scenarios,
        }
    else:
        result.pop("traffic_validation_summary", None)
    return None


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: backend_job_runner.py <job_id>")
    job_id = str(sys.argv[1] or "").strip()
    if not job_id:
        raise SystemExit("job_id is required")

    job_record = _load_job(job_id)
    if str(job_record.get("status", "")).strip().lower() == "canceled":
        return 0

    job_record["status"] = "running"
    job_record["started_at"] = job_record.get("started_at") or utc_now_iso()
    job_record["worker_pid"] = int(os.getpid())
    job_record["job_slot_path"] = os.environ.get("BRP_JOB_CONCURRENCY_SLOT", "").strip() or job_record.get("job_slot_path")
    result: dict[str, Any] | None = None
    try:
        _save_job(job_record)
        metadata = dict(job_record.get("metadata") or {})
        job_kind = str(metadata.get("job_kind") or "route_audit").strip().lower()
        from audit_return import bind_morning_scenarios, is_audit_return, run_audit_return
        if is_audit_return(job_record):
            if not bind_morning_scenarios(job_record, _runtime_store().get_job):
                job_record.update(status="scheduled", started_at=None, worker_pid=None, job_slot_path=None)
                _save_job(job_record)
                return 0
            _save_job(job_record)
            metadata = dict(job_record["metadata"])
        prepared_payload = dict(job_record.get("prepared_payload") or {})
        from paired_return import ensure_return_due, run_return_measurement
        ensure_return_due(metadata)
        is_return = metadata.get("measurement_kind") == "pm_return"
        if job_kind == "direct_school_analysis" or is_return:
            def save_checkpoint(partial_result: dict[str, Any]) -> None:
                current = _load_job(job_id)
                if str(current.get("status", "")).strip().lower() == "canceled":
                    raise RuntimeError("Direct-to-school analysis was canceled.")
                current["result"] = partial_result
                current["updated_at"] = utc_now_iso()
                _save_job(current)

            if metadata.get("measurement_kind") == "pm_return":
                def check_canceled():
                    if _load_job(job_id).get("status") == "canceled":
                        raise RuntimeError("Return measurement was canceled.")
                if is_audit_return(job_record):
                    result = run_audit_return(job_record, checkpoint=save_checkpoint, check_canceled=check_canceled)
                else:
                    result = run_return_measurement(prepared_payload, dict(metadata.get("analysis_config") or {}),
                        checkpoint=save_checkpoint, check_canceled=check_canceled)
            else:
                delta_options = {}
                if os.environ.get("BRP_DELTA_CHECKPOINTS") == "1":
                    token = os.environ.get("BRP_JOB_RUN_TOKEN")
                    def check_attempt():
                        if not _runtime_store().running_attempt(job_id, token):
                            raise SupersededAttempt()
                    def save_delta(parts, *, reset):
                        if not _runtime_store().checkpoint_parts(job_id, token, parts, reset=reset):
                            raise SupersededAttempt()
                    delta_options = {"checkpoint_delta": save_delta, "check_canceled": check_attempt}
                result = run_direct_school_analysis(
                    prepared_payload,
                    dict(metadata.get("analysis_config") or {}),
                    scheduled_start_at=job_record.get("scheduled_start_at"),
                    run_seed=str(job_record.get("scheduled_start_at") or job_id),
                    checkpoint=save_checkpoint, **delta_options,
                    resume_result=dict(job_record.get("result") or {}),
                )
        else:
            config = build_planner_config(job_record.get("config") or {})
            if _is_scheduled_job(job_record):
                expected = scheduled_traffic_provider(
                    prepared_payload.get("input_records") or [], google=config.final_time_validation_mode == "google"
                )
                result = run_backend_planner_with_prepared_data(
                    prepared_payload,
                    config=config,
                    require_fresh_final_traffic=True,
                )
                validation_error = _scheduled_final_traffic_validation_error(result, expected_provider=expected)
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
    except SupersededAttempt:
        return 0
    except Exception as exc:
        job_record = _load_job(job_id)
        if str(job_record.get("status", "")).strip().lower() == "canceled":
            return 0
        job_record["status"] = "failed"
        job_record["finished_at"] = utc_now_iso()
        job_record["result"] = result if result is not None else job_record.get("result")
        if (job_record.get("metadata", {}).get("analysis_config", {}).get("final_time_validation_mode") == "google"
                and isinstance(job_record.get("result"), dict)):
            job_record["result"]["status"] = "failed"
        if getattr(exc, "details", None):
            job_record.setdefault("metadata", {})["failure_details"] = exc.details
        job_record["error"] = str(exc)
        job_record["traceback"] = traceback.format_exc()
        job_record["worker_pid"] = None
        job_record["job_slot_path"] = None
        try:
            _save_job(job_record)
        except SupersededAttempt:
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
