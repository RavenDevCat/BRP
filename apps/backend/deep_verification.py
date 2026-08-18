from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from typing import Any, Iterable
from uuid import uuid4


DEEP_VERIFICATION_SCENARIOS = {
    "time_constrained": "Strict Plan",
    "exception_preserving": "Protected Plan",
}
ACTIVE_DEEP_VERIFICATION_STATUSES = {"queued", "running", "paused"}
TERMINAL_DEEP_VERIFICATION_STATUSES = {
    "certified_minimum",
    "best_found_unproven",
    "budget_exhausted",
    "technical_failure",
    "canceled",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _scenario(job_record: dict[str, Any], scenario_key: str) -> dict[str, Any]:
    result = dict(job_record.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    return dict(structured.get(scenario_key) or {})


def _scenario_route_count(scenario: dict[str, Any]) -> int:
    return max(
        0,
        _safe_int(
            scenario.get("bus_count"),
            len(list(scenario.get("routes") or [])),
        ),
    )


def _attempt_target(attempt: dict[str, Any]) -> int | None:
    value = attempt.get("target_vehicle_count")
    if value is None:
        return None
    parsed = _safe_int(value, -1)
    return parsed if parsed >= 0 else None


def _attempt_state(attempt: dict[str, Any]) -> str:
    status = str(attempt.get("status") or "").strip().lower()
    if bool(attempt.get("accepted")) or bool(attempt.get("all_constraints_passed")):
        return "feasible"
    if status == "infeasible":
        return "certified_infeasible"
    if status == "unresolved":
        return "pending"
    if status in {"failed", "rejected"} or attempt.get("actual_vehicle_count") is not None:
        return "candidate_rejected"
    return "pending"


def _scenario_attempts(scenario: dict[str, Any], scenario_key: str) -> list[dict[str, Any]]:
    if scenario_key == "time_constrained":
        return [
            dict(item)
            for item in list(
                dict(scenario.get("vehicle_ladder_search") or {}).get("attempts")
                or []
            )
        ]
    return [
        dict(item)
        for item in list(
            dict(scenario.get("exception_preserving") or {}).get("attempts")
            or []
        )
    ]


def _scenario_lower_bound(scenario: dict[str, Any], scenario_key: str) -> int:
    outcome = dict(scenario.get("constraint_search_outcome") or {})
    if scenario_key == "time_constrained":
        return max(1, _safe_int(outcome.get("theoretical_min_vehicle_count"), 1))
    frozen_count = max(0, _safe_int(outcome.get("frozen_route_count"), 0))
    remainder_minimum = max(
        0,
        _safe_int(outcome.get("theoretical_remainder_min_vehicle_count"), 0),
    )
    return max(1, frozen_count + remainder_minimum)


def _scenario_rank(scenario: dict[str, Any]) -> tuple[int, int, float, float]:
    time_impact = dict(scenario.get("final_time_impact_gate") or {})
    traffic_gate = dict(scenario.get("traffic_gate") or {})
    return (
        _scenario_route_count(scenario),
        _safe_int(time_impact.get("over_limit_rider_count"), 0),
        _safe_float(time_impact.get("max_adverse_minutes"), 0.0),
        _safe_float(
            traffic_gate.get("max_time_window_overrun_minutes")
            or traffic_gate.get("max_estimated_arrival_delay_minutes"),
            0.0,
        ),
    )


def _constraint_snapshot_hash(job_record: dict[str, Any]) -> str:
    snapshot = {
        "config": dict(job_record.get("config") or {}),
        "prepared_payload_summary": dict(
            job_record.get("prepared_payload_summary") or {}
        ),
        "source_job_id": str(job_record.get("job_id") or ""),
    }
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _initial_target_states(
    scenario: dict[str, Any], scenario_key: str, best_count: int, lower_bound: int
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {
        str(target): {
            "target_vehicle_count": target,
            "status": "pending",
            "attempt_count": 0,
            "source": "not_attempted",
        }
        for target in range(best_count - 1, lower_bound - 1, -1)
    }
    for attempt in _scenario_attempts(scenario, scenario_key):
        target = _attempt_target(attempt)
        if target is None or str(target) not in states:
            continue
        state = _attempt_state(attempt)
        states[str(target)].update(
            {
                "status": state,
                "source": "parent_search",
                "parent_attempt": deepcopy(attempt),
            }
        )
    return states


def _pending_targets(target_states: dict[str, dict[str, Any]]) -> list[int]:
    return sorted(
        (
            _safe_int(key, -1)
            for key, state in target_states.items()
            if str(state.get("status") or "")
            in {"pending", "candidate_rejected", "provider_pending"}
        ),
        reverse=True,
    )


def build_automatic_verification_records(
    job_record: dict[str, Any],
    *,
    queue_scope: str | None = None,
) -> list[dict[str, Any]]:
    if str(job_record.get("status") or "").strip().lower() != "succeeded":
        return []
    metadata = dict(job_record.get("metadata") or {})
    if metadata.get("deep_verification_result"):
        return []
    scenarios: list[tuple[str, dict[str, Any]]] = []
    for scenario_key in DEEP_VERIFICATION_SCENARIOS:
        scenario = _scenario(job_record, scenario_key)
        if str(scenario.get("scenario_status") or "").strip().lower() != "passed":
            continue
        best_count = _scenario_route_count(scenario)
        lower_bound = _scenario_lower_bound(scenario, scenario_key)
        if best_count <= lower_bound:
            continue
        target_states = _initial_target_states(
            scenario, scenario_key, best_count, lower_bound
        )
        if _pending_targets(target_states):
            scenarios.append((scenario_key, scenario))
    scenarios.sort(key=lambda item: _scenario_rank(item[1]))

    total_budget_seconds = max(
        60,
        _safe_int(os.environ.get("BRP_DEEP_VERIFY_TOTAL_BUDGET_SECONDS"), 5400),
    )
    initial_slice_seconds = max(
        10,
        _safe_int(os.environ.get("BRP_DEEP_VERIFY_INITIAL_SOLVER_SECONDS"), 60),
    )
    maximum_slice_seconds = max(
        initial_slice_seconds,
        _safe_int(os.environ.get("BRP_DEEP_VERIFY_MAX_SOLVER_SECONDS"), 600),
    )
    maximum_attempts = max(
        1,
        _safe_int(os.environ.get("BRP_DEEP_VERIFY_MAX_ATTEMPTS_PER_TARGET"), 3),
    )
    provider_call_budget = max(
        1,
        _safe_int(os.environ.get("BRP_DEEP_VERIFY_PROVIDER_CALL_BUDGET"), 300),
    )
    records: list[dict[str, Any]] = []
    for priority_index, (scenario_key, scenario) in enumerate(scenarios):
        best_count = _scenario_route_count(scenario)
        lower_bound = _scenario_lower_bound(scenario, scenario_key)
        target_states = _initial_target_states(
            scenario, scenario_key, best_count, lower_bound
        )
        pending_targets = _pending_targets(target_states)
        records.append(
            {
                "verification_id": uuid4().hex[:12],
                "parent_job_id": str(job_record.get("job_id") or ""),
                "owner_email": str(job_record.get("owner_email") or "")
                .strip()
                .lower(),
                "scenario_key": scenario_key,
                "scenario_label": DEEP_VERIFICATION_SCENARIOS[scenario_key],
                "status": "queued",
                "priority": 100 + priority_index,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "worker_pid": None,
                "job_slot_path": None,
                "automatic": True,
                "job_queue_scope": str(
                    queue_scope
                    or metadata.get("job_queue_scope")
                    or job_record.get("job_queue_scope")
                    or "default"
                ).strip()
                or "default",
                "constraint_snapshot_hash": _constraint_snapshot_hash(job_record),
                "initial_best_vehicle_count": best_count,
                "best_vehicle_count": best_count,
                "lower_bound_vehicle_count": lower_bound,
                "target_vehicle_count": pending_targets[0],
                "pending_target_vehicle_counts": pending_targets,
                "target_states": target_states,
                "elapsed_seconds": 0.0,
                "total_budget_seconds": total_budget_seconds,
                "initial_slice_solver_seconds": initial_slice_seconds,
                "max_slice_solver_seconds": maximum_slice_seconds,
                "max_attempts_per_target": maximum_attempts,
                "provider_api_calls": 0,
                "provider_call_budget": provider_call_budget,
                "result_job_ids": [],
                "error": None,
            }
        )
    return records


def next_target_vehicle_count(record: dict[str, Any]) -> int | None:
    target_states = {
        str(key): dict(value or {})
        for key, value in dict(record.get("target_states") or {}).items()
    }
    pending = _pending_targets(target_states)
    if not pending:
        return None
    max_attempts = max(1, _safe_int(record.get("max_attempts_per_target"), 3))
    eligible = [
        target
        for target in pending
        if _safe_int(target_states[str(target)].get("attempt_count"), 0) < max_attempts
    ]
    if not eligible:
        return None
    minimum_attempt_count = min(
        _safe_int(target_states[str(target)].get("attempt_count"), 0)
        for target in eligible
    )
    # First cover every lower count once, then return to unresolved counts.
    return max(
        target
        for target in eligible
        if _safe_int(target_states[str(target)].get("attempt_count"), 0)
        == minimum_attempt_count
    )


def solver_seconds_for_target(record: dict[str, Any], target: int) -> int:
    state = dict(dict(record.get("target_states") or {}).get(str(target)) or {})
    attempt_count = max(0, _safe_int(state.get("attempt_count"), 0))
    initial_seconds = max(
        10, _safe_int(record.get("initial_slice_solver_seconds"), 60)
    )
    maximum_seconds = max(
        initial_seconds, _safe_int(record.get("max_slice_solver_seconds"), 600)
    )
    return min(maximum_seconds, initial_seconds * (2**attempt_count))


def terminal_status_for_record(record: dict[str, Any]) -> str | None:
    target_states = [
        dict(value or {}) for value in dict(record.get("target_states") or {}).values()
    ]
    if not target_states:
        return "certified_minimum"
    unresolved = [
        state
        for state in target_states
        if str(state.get("status") or "")
        in {"pending", "candidate_rejected", "provider_pending"}
    ]
    if unresolved:
        max_attempts = max(1, _safe_int(record.get("max_attempts_per_target"), 3))
        if any(
            _safe_int(state.get("attempt_count"), 0) < max_attempts
            for state in unresolved
        ):
            return None
        return "best_found_unproven"
    best_count = max(0, _safe_int(record.get("best_vehicle_count"), 0))
    lower_states = [
        state
        for state in target_states
        if _safe_int(state.get("target_vehicle_count"), best_count) < best_count
    ]
    if lower_states and all(
        str(state.get("status") or "") == "certified_infeasible"
        for state in lower_states
    ):
        return "certified_minimum"
    return "best_found_unproven"


def public_verification_record(
    record: dict[str, Any], candidates: Iterable[dict[str, Any]] = ()
) -> dict[str, Any]:
    hidden_keys = {"job_slot_path", "worker_pid"}
    payload = {
        key: deepcopy(value)
        for key, value in record.items()
        if key not in hidden_keys
    }
    payload["target_states"] = {
        str(key): {
            state_key: deepcopy(state_value)
            for state_key, state_value in dict(value or {}).items()
            if state_key not in {"parent_attempt"}
        }
        for key, value in dict(record.get("target_states") or {}).items()
    }
    payload["candidates"] = [
        {
            key: deepcopy(value)
            for key, value in dict(candidate or {}).items()
            if key not in {"scenario_result", "full_result"}
        }
        for candidate in candidates
    ]
    return payload
