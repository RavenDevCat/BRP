from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from typing import Any
from uuid import uuid4

try:
    from .deep_verification import (
        next_target_vehicle_count,
        solver_seconds_for_target,
        terminal_status_for_record,
    )
    from .runtime_store_sqlite import SqliteRuntimeStore
except ImportError:  # pragma: no cover - supports direct script execution.
    from deep_verification import (
        next_target_vehicle_count,
        solver_seconds_for_target,
        terminal_status_for_record,
    )
    from runtime_store_sqlite import SqliteRuntimeStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _runtime_db_path() -> Path:
    configured = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "state" / "brp_runtime.sqlite"


def _runtime_store() -> SqliteRuntimeStore:
    store = SqliteRuntimeStore(_runtime_db_path())
    store.initialize()
    return store


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


def _scenario_route_count(scenario: dict[str, Any]) -> int:
    return max(
        0,
        _safe_int(scenario.get("bus_count"), len(list(scenario.get("routes") or []))),
    )


def _provider_api_calls(
    scenario: dict[str, Any],
    full_result: dict[str, Any] | None = None,
) -> int:
    gate = dict(scenario.get("traffic_gate") or {})
    structured = dict(dict(full_result or {}).get("structured_results") or {})
    runtime_profile = dict(structured.get("runtime_profile") or {})
    return max(
        0,
        _safe_int(gate.get("api_calls"), 0),
        _safe_int(runtime_profile.get("traffic_api_calls"), 0),
    )


def _remaining_slice_budgets(
    record: dict[str, Any], target_vehicle_count: int
) -> tuple[int, int]:
    remaining_wall_clock_seconds = max(
        0.0,
        _safe_float(record.get("total_budget_seconds"), 0.0)
        - _safe_float(record.get("elapsed_seconds"), 0.0),
    )
    remaining_provider_calls = max(
        0,
        _safe_int(record.get("provider_call_budget"), 300)
        - _safe_int(record.get("provider_api_calls"), 0),
    )
    if remaining_wall_clock_seconds <= 0.0 or remaining_provider_calls <= 0:
        return 0, remaining_provider_calls
    return (
        min(
            solver_seconds_for_target(record, target_vehicle_count),
            max(1, int(remaining_wall_clock_seconds)),
        ),
        remaining_provider_calls,
    )


def _classify_scenario(scenario: dict[str, Any]) -> str:
    scenario_status = str(scenario.get("scenario_status") or "").strip().lower()
    search_status = str(
        dict(scenario.get("constraint_search_outcome") or {}).get("status") or ""
    ).strip().lower()
    traffic_status = str(
        dict(scenario.get("traffic_gate") or {}).get("status") or ""
    ).strip().lower()
    if scenario_status == "passed":
        return "feasible"
    if traffic_status in {"unavailable", "disabled"}:
        return "provider_pending"
    if scenario_status == "infeasible" and search_status == "infeasible":
        return "certified_infeasible"
    if scenario_status == "unresolved" or search_status == "unresolved":
        return "pending"
    return "candidate_rejected"


def _provider_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "amap",
            "kakao",
            "provider",
            "traffic validation",
            "traffic gate",
            "quota",
            "rate limit",
            "http 429",
        )
    )


def _append_result_job_id(record: dict[str, Any], child_job_id: str) -> None:
    result_job_ids = [
        str(value or "").strip()
        for value in list(record.get("result_job_ids") or [])
        if str(value or "").strip()
    ]
    normalized_id = str(child_job_id or "").strip()
    if normalized_id and normalized_id not in result_job_ids:
        result_job_ids.append(normalized_id)
    record["result_job_ids"] = result_job_ids


def _build_derived_result_job(
    store: SqliteRuntimeStore,
    record: dict[str, Any],
    parent_job: dict[str, Any],
    scenario: dict[str, Any],
    verification_result: dict[str, Any],
) -> str:
    try:
        from .planner_core import (
            attach_output_paths_to_structured_results,
            build_planner_config,
            compare_current_plan_to_baseline,
            rerender_html_from_structured_results,
            summarize_structured_results,
        )
    except ImportError:  # pragma: no cover - direct script execution.
        from planner_core import (
            attach_output_paths_to_structured_results,
            build_planner_config,
            compare_current_plan_to_baseline,
            rerender_html_from_structured_results,
            summarize_structured_results,
        )

    child_job_id = uuid4().hex[:12]
    parent_result = deepcopy(parent_job.get("result") or {})
    structured = deepcopy(parent_result.get("structured_results") or {})
    scenario_key = str(record.get("scenario_key") or "").strip()
    structured[scenario_key] = deepcopy(scenario)
    if scenario_key == "time_constrained":
        structured["time_constrained_optimization"] = deepcopy(scenario)
        structured["current_plan_comparison"] = compare_current_plan_to_baseline(
            dict(structured.get("current_plan_assessment") or {}), scenario
        )
    else:
        structured["exception_preserving_optimization"] = deepcopy(scenario)
    structured["deep_verification"] = {
        "verification_id": record.get("verification_id"),
        "parent_job_id": record.get("parent_job_id"),
        "scenario_key": scenario_key,
        "best_vehicle_count": _scenario_route_count(scenario),
        "status": record.get("status"),
        "updated_at": utc_now_iso(),
    }

    child_config_payload = deepcopy(parent_job.get("config") or {})
    child_config_payload["output_directory_name"] = child_job_id
    child_config = build_planner_config(child_config_payload)
    artifact_generation_error = ""
    try:
        structured = rerender_html_from_structured_results(structured, child_config)
    except Exception as exc:
        structured = attach_output_paths_to_structured_results(structured, child_config)
        artifact_generation_error = str(exc)
    structured["deep_verification"]["artifact_generation_status"] = (
        "failed" if artifact_generation_error else "ready"
    )
    if artifact_generation_error:
        structured["deep_verification"]["artifact_generation_error"] = (
            artifact_generation_error
        )
    input_records = list(dict(parent_job.get("prepared_payload") or {}).get("input_records") or [])
    service_record_count = sum(
        1 for item in input_records if _safe_int(dict(item or {}).get("passenger_count"), 0) > 0
    )
    merged_result = deepcopy(parent_result)
    merged_result["structured_results"] = structured
    merged_result["summary"] = summarize_structured_results(
        structured, service_record_count
    )
    merged_result[scenario_key] = deepcopy(scenario)
    if scenario_key == "time_constrained":
        merged_result["time_constrained_optimization"] = deepcopy(scenario)
    else:
        merged_result["exception_preserving_optimization"] = deepcopy(scenario)
    merged_result["deep_verification"] = deepcopy(structured["deep_verification"])
    merged_result["logs"] = str(verification_result.get("logs") or "")
    merged_result["elapsed_seconds"] = _safe_float(
        verification_result.get("elapsed_seconds"), 0.0
    )

    metadata = deepcopy(parent_job.get("metadata") or {})
    parent_name = str(metadata.get("job_name") or metadata.get("source_label") or "Route Audit")
    verified_vehicle_count = _scenario_route_count(scenario)
    scenario_name = str(scenario.get("display_name") or scenario_key)
    metadata.update(
        {
            "job_name": (
                f"{parent_name} - verified {verified_vehicle_count}-route "
                f"{scenario_name}"
            ),
            "job_custom_name": "",
            "scheduled_job": False,
            "scheduled_start_at": None,
            "scheduled_trigger_label": None,
            "deep_verification_result": True,
            "deep_verification_id": record.get("verification_id"),
            "deep_verification_parent_job_id": record.get("parent_job_id"),
            "deep_verification_scenario_key": scenario_key,
            "deep_verification_best_vehicle_count": _scenario_route_count(scenario),
        }
    )
    created_at = utc_now_iso()
    child_record = {
        "job_id": child_job_id,
        "owner_email": parent_job.get("owner_email"),
        "shared_with_all": bool(parent_job.get("shared_with_all")),
        "status": "succeeded",
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": utc_now_iso(),
        "scheduled_start_at": None,
        "scheduled_trigger_label": None,
        "worker_pid": None,
        "job_slot_path": None,
        "config": child_config_payload,
        "prepared_payload": deepcopy(parent_job.get("prepared_payload") or {}),
        "prepared_payload_summary": deepcopy(
            parent_job.get("prepared_payload_summary") or {}
        ),
        "metadata": metadata,
        "result": merged_result,
        "error": None,
        "traceback": None,
    }
    store.upsert_job(child_record)
    store.copy_route_audit_workspace(str(parent_job.get("job_id") or ""), child_job_id)
    return child_job_id


def _finish_slice(
    store: SqliteRuntimeStore,
    record: dict[str, Any],
    *,
    elapsed_seconds: float,
) -> None:
    record["elapsed_seconds"] = _safe_float(record.get("elapsed_seconds"), 0.0) + max(
        0.0, elapsed_seconds
    )
    record["last_slice_finished_at"] = utc_now_iso()
    record["updated_at"] = utc_now_iso()
    record["worker_pid"] = None
    record["job_slot_path"] = None
    record["pending_target_vehicle_counts"] = sorted(
        (
            _safe_int(key, -1)
            for key, state in dict(record.get("target_states") or {}).items()
            if str(dict(state or {}).get("status") or "")
            in {"pending", "candidate_rejected", "provider_pending"}
        ),
        reverse=True,
    )
    budget_exhausted = _safe_float(record.get("elapsed_seconds"), 0.0) >= _safe_float(
        record.get("total_budget_seconds"), 0.0
    )
    provider_exhausted = _safe_int(record.get("provider_api_calls"), 0) >= _safe_int(
        record.get("provider_call_budget"), 300
    )
    terminal_status = terminal_status_for_record(record)
    if budget_exhausted or provider_exhausted:
        record["status"] = "budget_exhausted"
        record["completion_reason"] = (
            "provider_call_budget_exhausted"
            if provider_exhausted
            else "wall_clock_budget_exhausted"
        )
        record["finished_at"] = utc_now_iso()
        record["target_vehicle_count"] = None
    elif terminal_status:
        record["status"] = terminal_status
        record["completion_reason"] = terminal_status
        record["finished_at"] = utc_now_iso()
        record["target_vehicle_count"] = None
    else:
        record["status"] = "queued"
        record["target_vehicle_count"] = next_target_vehicle_count(record)
    store.upsert_deep_verification(record)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: deep_verification_runner.py <verification_id>")
    verification_id = str(sys.argv[1] or "").strip()
    if not verification_id:
        raise SystemExit("verification_id is required")

    store = _runtime_store()
    record = store.get_deep_verification(verification_id)
    if not record:
        raise SystemExit(f"Deep verification not found: {verification_id}")
    if str(record.get("status") or "").strip().lower() != "running":
        _release_concurrency_slot()
        return 0
    parent_job = store.get_job(str(record.get("parent_job_id") or ""))
    if not parent_job or str(parent_job.get("status") or "").lower() != "succeeded":
        record["status"] = "technical_failure"
        record["finished_at"] = utc_now_iso()
        record["error"] = "The completed parent job is no longer available."
        record["worker_pid"] = None
        record["job_slot_path"] = None
        store.upsert_deep_verification(record)
        _release_concurrency_slot()
        return 1

    target = _safe_int(
        record.get("target_vehicle_count"), next_target_vehicle_count(record) or -1
    )
    if target < 0:
        _finish_slice(store, record, elapsed_seconds=0.0)
        _release_concurrency_slot()
        return 0
    target_states = {
        str(key): dict(value or {})
        for key, value in dict(record.get("target_states") or {}).items()
    }
    target_state = dict(target_states.get(str(target)) or {})
    attempt_number = _safe_int(target_state.get("attempt_count"), 0) + 1
    solver_seconds, remaining_provider_calls = _remaining_slice_budgets(record, target)
    if solver_seconds <= 0 or remaining_provider_calls <= 0:
        _finish_slice(store, record, elapsed_seconds=0.0)
        _release_concurrency_slot()
        return 0
    os.environ["BRP_SOLVER_TIME_LIMIT_SECONDS"] = str(solver_seconds)
    os.environ["BRP_SOLVER_TOTAL_WALL_CLOCK_SECONDS"] = str(solver_seconds)
    os.environ["BRP_SOLVER_TOTAL_WALL_CLOCK_PERSIST_ACROSS_CALLS"] = "1"
    os.environ["BRP_FINAL_ROUTE_TRAFFIC_TOTAL_CALL_BUDGET"] = str(
        remaining_provider_calls
    )
    started = time.perf_counter()

    try:
        try:
            from .planner_core import build_planner_config, run_backend_planner_with_prepared_data
        except ImportError:  # pragma: no cover - direct script execution.
            from planner_core import build_planner_config, run_backend_planner_with_prepared_data

        config = build_planner_config(parent_job.get("config") or {})
        result = run_backend_planner_with_prepared_data(
            dict(parent_job.get("prepared_payload") or {}),
            config=config,
            verification_scenario_key=str(record.get("scenario_key") or ""),
            verification_target_vehicle_count=target,
            verification_reference_result=dict(parent_job.get("result") or {}),
            require_fresh_candidate_traffic=True,
        )
        scenario = dict(result.get("scenario_result") or {})
        outcome = _classify_scenario(scenario)
        actual_vehicle_count = _scenario_route_count(scenario)
        api_calls = _provider_api_calls(scenario, result)
        elapsed_seconds = time.perf_counter() - started
        candidate = {
            "verification_id": verification_id,
            "target_vehicle_count": target,
            "attempt_number": attempt_number,
            "status": outcome,
            "actual_vehicle_count": actual_vehicle_count or None,
            "solver_time_limit_seconds": solver_seconds,
            "elapsed_seconds": elapsed_seconds,
            "provider_api_calls": api_calls,
            "created_at": utc_now_iso(),
            "scenario_result": scenario,
            "full_result": result,
        }
        store.upsert_deep_verification_candidate(candidate)
        target_state.update(
            {
                "target_vehicle_count": target,
                "status": outcome,
                "attempt_count": attempt_number,
                "last_attempt_at": utc_now_iso(),
                "last_elapsed_seconds": elapsed_seconds,
                "last_solver_time_limit_seconds": solver_seconds,
                "last_provider_api_calls": api_calls,
                "last_actual_vehicle_count": actual_vehicle_count or None,
                "source": "deep_verification",
            }
        )
        target_states[str(target)] = target_state
        record["target_states"] = target_states
        record["provider_api_calls"] = _safe_int(record.get("provider_api_calls"), 0) + api_calls
        record["error"] = None

        if outcome == "feasible" and actual_vehicle_count > 0:
            if str(actual_vehicle_count) in target_states:
                actual_state = dict(target_states[str(actual_vehicle_count)])
                actual_state.update(
                    {
                        "status": "feasible",
                        "source": "deep_verification",
                        "feasible_via_target_vehicle_count": target,
                    }
                )
                target_states[str(actual_vehicle_count)] = actual_state
            if actual_vehicle_count < _safe_int(record.get("best_vehicle_count"), 10**9):
                record["best_vehicle_count"] = actual_vehicle_count
                child_job_id = _build_derived_result_job(
                    store, record, parent_job, scenario, result
                )
                _append_result_job_id(record, child_job_id)

        _finish_slice(store, record, elapsed_seconds=elapsed_seconds)
        return 0
    except Exception as exc:
        elapsed_seconds = time.perf_counter() - started
        outcome = "provider_pending" if _provider_exception(exc) else "technical_failure"
        candidate = {
            "verification_id": verification_id,
            "target_vehicle_count": target,
            "attempt_number": attempt_number,
            "status": outcome,
            "solver_time_limit_seconds": solver_seconds,
            "elapsed_seconds": elapsed_seconds,
            "provider_api_calls": 0,
            "created_at": utc_now_iso(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        store.upsert_deep_verification_candidate(candidate)
        target_state.update(
            {
                "target_vehicle_count": target,
                "status": "provider_pending" if outcome == "provider_pending" else "pending",
                "attempt_count": attempt_number,
                "last_attempt_at": utc_now_iso(),
                "last_elapsed_seconds": elapsed_seconds,
                "last_solver_time_limit_seconds": solver_seconds,
                "last_error": str(exc),
                "source": "deep_verification",
            }
        )
        target_states[str(target)] = target_state
        record["target_states"] = target_states
        record["error"] = str(exc)
        record["technical_failure_count"] = _safe_int(
            record.get("technical_failure_count"), 0
        ) + (0 if outcome == "provider_pending" else 1)
        if _safe_int(record.get("technical_failure_count"), 0) >= 3:
            record["status"] = "technical_failure"
            record["finished_at"] = utc_now_iso()
            record["worker_pid"] = None
            record["job_slot_path"] = None
            record["elapsed_seconds"] = _safe_float(
                record.get("elapsed_seconds"), 0.0
            ) + elapsed_seconds
            record["updated_at"] = utc_now_iso()
            store.upsert_deep_verification(record)
        else:
            _finish_slice(store, record, elapsed_seconds=elapsed_seconds)
        return 1
    finally:
        _release_concurrency_slot()


if __name__ == "__main__":
    raise SystemExit(main())
