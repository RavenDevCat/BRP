from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from runtime_store_sqlite import (  # noqa: E402
    RuntimeJsonPaths,
    SqliteRuntimeStore,
    migrate_json_runtime_to_sqlite,
    verify_json_sqlite_parity,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def job_record(job_id: str, owner: str, *, shared: bool = False) -> dict[str, object]:
    return {
        "job_id": job_id,
        "owner_email": owner,
        "shared_with_all": shared,
        "status": "succeeded",
        "created_at": f"2026-06-21T12:0{job_id[-1]}:00+00:00",
        "started_at": None,
        "finished_at": None,
        "metadata": {"job_name": f"job {job_id}"},
        "prepared_payload_summary": {"stop_count": 2},
        "config": {"service_direction": "To School"},
        "prepared_payload": {"rows": []},
        "result": {"ok": True},
        "error": None,
        "traceback": None,
    }


def side_tool_record(run_id: str, owner: str, *, shared: bool = False) -> dict[str, object]:
    return {
        "run_id": run_id,
        "tool_key": "fleet_planner",
        "owner_email": owner,
        "title": f"run {run_id}",
        "created_at": f"2026-06-21T13:0{run_id[-1]}:00+00:00",
        "shared_with_all": shared,
        "scenario": {"market": "KR"},
        "summary": {"route_count": 1},
        "global_plan_result": {"routes": []},
    }


def test_sqlite_job_store_filters_and_deletes(tmp_path: Path) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(job_record("job1", "alice@example.com"))
    store.upsert_job(job_record("job2", "bob@example.com", shared=True))

    alice_jobs = store.list_jobs(user_email="alice@example.com")
    assert {item["job_id"] for item in alice_jobs} == {"job1", "job2"}
    assert store.get_job("job1")["metadata"] == {"job_name": "job job1"}
    assert store.count_jobs() == 2

    assert store.delete_job("job1") is True
    assert store.delete_job("missing") is False
    assert store.get_job("job1") is None
    assert [item["job_id"] for item in store.list_jobs(include_all=True)] == ["job2"]


def test_sqlite_side_tool_store_filters_and_deletes(tmp_path: Path) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_side_tool_run("fleet_planner", side_tool_record("run1", "alice@example.com"))
    store.upsert_side_tool_run("fleet_planner", side_tool_record("run2", "bob@example.com", shared=True))

    alice_runs = store.list_side_tool_runs("fleet_planner", user_email="alice@example.com")
    assert {item["run_id"] for item in alice_runs} == {"run1", "run2"}
    assert store.get_side_tool_run("fleet_planner", "run2")["summary"] == {"route_count": 1}
    assert store.count_side_tool_runs() == 2

    assert store.delete_side_tool_run("fleet_planner", "run1") is True
    assert store.delete_side_tool_run("fleet_planner", "missing") is False
    assert store.get_side_tool_run("fleet_planner", "run1") is None


def test_json_to_sqlite_migration_and_parity(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    side_tools_dir = tmp_path / "side_tools"
    sqlite_path = tmp_path / "runtime.sqlite"
    write_json(jobs_dir / "job1.json", job_record("job1", "alice@example.com"))
    write_json(jobs_dir / "job2.json", job_record("job2", "bob@example.com", shared=True))
    write_json(jobs_dir / "index.json", [])
    write_json(side_tools_dir / "fleet_planner" / "run1.json", side_tool_record("run1", "alice@example.com"))
    write_json(side_tools_dir / "fleet_planner" / "index.json", [])

    paths = RuntimeJsonPaths(jobs_dir=jobs_dir, side_tools_dir=side_tools_dir)
    summary = migrate_json_runtime_to_sqlite(paths, sqlite_path)

    assert summary == {"jobs": 2, "side_tool_runs": 1}
    parity = verify_json_sqlite_parity(paths, sqlite_path)
    assert parity["passed"] is True
    assert parity["json_job_count"] == 2
    assert parity["sqlite_job_count"] == 2
    assert parity["json_side_tool_run_count"] == 1
    assert parity["sqlite_side_tool_run_count"] == 1


def test_parity_reports_missing_sqlite_records(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    side_tools_dir = tmp_path / "side_tools"
    sqlite_path = tmp_path / "runtime.sqlite"
    write_json(jobs_dir / "job1.json", job_record("job1", "alice@example.com"))

    paths = RuntimeJsonPaths(jobs_dir=jobs_dir, side_tools_dir=side_tools_dir)
    parity = verify_json_sqlite_parity(paths, sqlite_path)

    assert parity["passed"] is False
    assert parity["missing_jobs"] == ["job1"]
