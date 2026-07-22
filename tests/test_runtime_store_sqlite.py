from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from runtime_store_sqlite import (  # noqa: E402
    RuntimeJsonPaths,
    SCHEMA_VERSION,
    SqliteRuntimeStore,
    migrate_json_runtime_to_sqlite,
    verify_json_sqlite_parity,
)
from osrm_manager_store import OsrmManagerStore  # noqa: E402
from quota_store_sqlite import SqliteQuotaStore  # noqa: E402


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
    alice_job = job_record("job1", "alice@example.com")
    alice_job["metadata"].update(
        {
            "client_prep": {"rows": [{"address": "A"}]},
            "planner_config": {"service_direction": "To School"},
        }
    )
    store.upsert_job(alice_job)
    store.upsert_job(job_record("job2", "bob@example.com", shared=True))

    alice_jobs = store.list_jobs(user_email="alice@example.com")
    assert {item["job_id"] for item in alice_jobs} == {"job1", "job2"}
    alice_summary = next(item for item in alice_jobs if item["job_id"] == "job1")
    assert alice_summary["metadata"] == {"job_name": "job job1"}
    assert store.get_job("job1")["metadata"] == alice_job["metadata"]
    assert store.count_jobs() == 2

    assert store.delete_job("job1") is True
    assert store.delete_job("missing") is False
    assert store.get_job("job1") is None
    assert [item["job_id"] for item in store.list_jobs(include_all=True)] == ["job2"]


def test_repeated_store_reads_do_not_write_schema_migrations(tmp_path: Path) -> None:
    cases = [
        (
            SqliteRuntimeStore(tmp_path / "runtime.sqlite"),
            "runtime_store",
            lambda store: store.count_jobs(),
        ),
        (
            SqliteQuotaStore(tmp_path / "quota.sqlite"),
            "quota_store",
            lambda store: store.get_usage("provider", "counter", "month", "2026-07"),
        ),
        (
            OsrmManagerStore(tmp_path / "osrm.sqlite"),
            "osrm_manager_store",
            lambda store: store.is_lock_held("missing"),
        ),
    ]

    for store, migration_name, read in cases:
        read(store)
        with sqlite3.connect(store.db_path) as observer:
            before_timestamp = observer.execute(
                "SELECT updated_at FROM schema_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()[0]
            before_data_version = observer.execute("PRAGMA data_version").fetchone()[0]

            read(store)
            assert observer.execute("PRAGMA data_version").fetchone()[0] == before_data_version
            assert observer.execute(
                "SELECT updated_at FROM schema_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()[0] == before_timestamp

            read(type(store)(store.db_path))
            assert observer.execute("PRAGMA data_version").fetchone()[0] == before_data_version
            assert observer.execute(
                "SELECT updated_at FROM schema_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()[0] == before_timestamp


def test_sqlite_job_claim_transitions_queued_job_once(tmp_path: Path) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    queued = job_record("job1", "alice@example.com")
    queued["status"] = "queued"
    store.upsert_job(queued)

    claimed = store.claim_queued_job(
        "job1", worker_pid=123, job_slot_path=str(tmp_path / "slot-1")
    )

    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["started_at"]
    assert claimed["worker_pid"] == 123
    assert claimed["job_slot_path"] == str(tmp_path / "slot-1")
    assert store.get_job("job1")["status"] == "running"

    assert store.claim_queued_job("job1", worker_pid=456) is None
    stored = store.get_job("job1")
    assert stored["worker_pid"] == 123
    assert stored["job_slot_path"] == str(tmp_path / "slot-1")


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


def test_history_groups_move_rename_and_preserve_empty_workspaces(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(job_record("job1", "alice@example.com"))
    store.upsert_job(job_record("job2", "alice@example.com"))

    first = store.assign_history_group(
        "route_audit", "alice@example.com", "June", ["job1", "job2"]
    )
    second = store.assign_history_group(
        "route_audit", "alice@example.com", "Follow up", ["job2"]
    )
    groups = store.list_history_groups("route_audit", "alice@example.com")
    assert {group["name"]: group["item_ids"] for group in groups} == {
        "June": ["job1"],
        "Follow up": ["job2"],
    }

    renamed = store.rename_history_group(
        "route_audit", "alice@example.com", first["group_id"], "June archive"
    )
    assert renamed is not None
    assert renamed["name"] == "June archive"

    assert store.delete_job("job1") is True
    groups = store.list_history_groups("route_audit", "alice@example.com")
    assert {group["name"]: group["item_ids"] for group in groups} == {
        "June archive": [],
        "Follow up": ["job2"],
    }
    assert store.delete_job("job2") is True
    assert all(
        not group["item_ids"]
        for group in store.list_history_groups("route_audit", "alice@example.com")
    )

    store.upsert_side_tool_run(
        "fleet_planner", side_tool_record("run1", "alice@example.com")
    )
    store.assign_history_group(
        "fleet_planner", "alice@example.com", "Fleet", ["run1"]
    )
    assert store.delete_side_tool_run("fleet_planner", "run1") is True
    fleet_groups = store.list_history_groups("fleet_planner", "alice@example.com")
    assert len(fleet_groups) == 1
    assert fleet_groups[0]["item_ids"] == []


def test_history_group_v2_database_upgrades_in_place(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "runtime.sqlite"
    with sqlite3.connect(sqlite_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                name TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE history_groups (
                group_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE history_group_items (
                scope TEXT NOT NULL,
                owner_email TEXT NOT NULL,
                item_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(scope, owner_email, item_id),
                FOREIGN KEY(group_id) REFERENCES history_groups(group_id) ON DELETE CASCADE
            );
            INSERT INTO schema_migrations VALUES(
                'runtime_store', 2, '2026-07-20T00:00:00+00:00'
            );
            INSERT INTO history_groups VALUES(
                'legacy-group', 'route_audit', 'alice@example.com', 'Legacy',
                '2026-07-20T00:00:00+00:00', '2026-07-20T00:00:00+00:00'
            );
            INSERT INTO history_group_items VALUES(
                'route_audit', 'alice@example.com', 'job1', 'legacy-group',
                '2026-07-20T00:00:00+00:00'
            );
            """
        )

    store = SqliteRuntimeStore(sqlite_path)
    groups = store.list_history_groups("route_audit", "alice@example.com")
    assert groups[0]["group_id"] == "legacy-group"
    assert groups[0]["item_ids"] == ["job1"]
    assert groups[0]["role"] == "owner"

    with store.connect() as conn:
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE name = 'runtime_store'"
        ).fetchone()[0] == SCHEMA_VERSION
        member = conn.execute(
            """
            SELECT member_email, role FROM history_group_members
            WHERE group_id = 'legacy-group'
            """
        ).fetchone()
        assert tuple(member) == ("alice@example.com", "owner")
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(history_group_items)")
        }
        assert "idx_history_group_items_scope_item" in indexes
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'history_group_preferences'"
        ).fetchone()


def test_runtime_store_schema_version_never_downgrades(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "runtime.sqlite"
    future_version = SCHEMA_VERSION + 1
    with sqlite3.connect(sqlite_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE schema_migrations (
                name TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES(
                'runtime_store', {future_version}, '2026-07-22T00:00:00+00:00'
            );
            """
        )

    store = SqliteRuntimeStore(sqlite_path)
    store.initialize()

    with store.connect() as conn:
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE name = 'runtime_store'"
        ).fetchone()[0] == future_version


def test_history_group_roles_and_delete_release_items_not_jobs(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(job_record("job1", "alice@example.com"))
    group = store.assign_history_group(
        "route_audit", "alice@example.com", "Operations", ["job1"]
    )
    group_id = group["group_id"]
    store.set_history_group_member(
        "route_audit",
        "alice@example.com",
        group_id,
        "editor@example.com",
        "editor",
    )
    store.set_history_group_member(
        "route_audit",
        "alice@example.com",
        group_id,
        "viewer@example.com",
        "viewer",
    )

    assert store.list_history_groups(
        "route_audit", "alice@example.com"
    )[0]["role"] == "owner"
    assert store.list_history_groups(
        "route_audit", "editor@example.com"
    )[0]["role"] == "editor"
    assert store.list_history_groups(
        "route_audit", "viewer@example.com"
    )[0]["role"] == "viewer"
    assert store.list_history_groups(
        "route_audit", "admin@example.com", include_all=True
    )[0]["role"] == "admin"
    members = store.list_history_groups(
        "route_audit", "alice@example.com"
    )[0]["members"]
    assert {(member["member_email"], member["role"]) for member in members} == {
        ("alice@example.com", "owner"),
        ("editor@example.com", "editor"),
        ("viewer@example.com", "viewer"),
    }
    assert [
        item["job_id"]
        for item in store.list_jobs(user_email="viewer@example.com")
    ] == ["job1"]
    assert store.history_item_role(
        "route_audit", "job1", "editor@example.com"
    ) == "editor"

    with pytest.raises(PermissionError):
        store.delete_history_group(
            "route_audit", "editor@example.com", group_id
        )
    with pytest.raises(PermissionError):
        store.rename_history_group(
            "route_audit", "viewer@example.com", group_id, "Nope"
        )
    with pytest.raises(PermissionError):
        store.move_history_group_items(
            "route_audit", "viewer@example.com", None, ["job1"]
        )
    with pytest.raises(PermissionError):
        store.assign_history_group(
            "route_audit", "bob@example.com", "Bob", ["job1"]
        )

    store.upsert_job(job_record("job2", "alice@example.com"))
    second = store.assign_history_group(
        "route_audit", "alice@example.com", "Second", ["job2"]
    )
    store.set_history_group_member(
        "route_audit",
        "alice@example.com",
        second["group_id"],
        "editor@example.com",
        "editor",
    )
    moved = store.move_history_group_items(
        "route_audit", "editor@example.com", second["group_id"], ["job1"]
    )
    assert moved is not None
    assert set(moved["item_ids"]) == {"job1", "job2"}
    assert store.move_history_group_items(
        "route_audit", "editor@example.com", None, ["job1"]
    ) is None
    assert store.history_item_role(
        "route_audit", "job1", "editor@example.com"
    ) is None

    updated = store.remove_history_group_member(
        "route_audit",
        "alice@example.com",
        second["group_id"],
        "editor@example.com",
    )
    assert updated is not None
    assert all(
        member["member_email"] != "editor@example.com"
        for member in updated["members"]
    )

    assert store.delete_history_group(
        "route_audit", "admin@example.com", group_id, include_all=True
    ) is True
    assert store.get_job("job1") is not None
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM history_group_items WHERE group_id = ?", (group_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM history_group_members WHERE group_id = ?", (group_id,)
        ).fetchone()[0] == 0


def test_history_group_owner_transfer_keeps_previous_owner_as_editor(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(job_record("job1", "alice@example.com"))
    group = store.assign_history_group(
        "route_audit", "alice@example.com", "Operations", ["job1"]
    )
    store.set_history_group_member(
        "route_audit",
        "alice@example.com",
        group["group_id"],
        "bob@example.com",
        "editor",
    )

    transferred = store.transfer_history_group_owner(
        "route_audit",
        "alice@example.com",
        group["group_id"],
        "bob@example.com",
    )

    assert transferred is not None
    assert transferred["owner_email"] == "bob@example.com"
    assert transferred["role"] == "editor"
    assert {
        (member["member_email"], member["role"])
        for member in transferred["members"]
    } == {
        ("alice@example.com", "editor"),
        ("bob@example.com", "owner"),
    }
    bob_group = store.list_history_groups("route_audit", "bob@example.com")[0]
    assert bob_group["role"] == "owner"
    assert bob_group["item_ids"] == ["job1"]
    with pytest.raises(PermissionError):
        store.transfer_history_group_owner(
            "route_audit",
            "alice@example.com",
            group["group_id"],
            "carol@example.com",
        )


def test_history_group_default_and_admin_fixed_assignment(
    tmp_path: Path,
) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(job_record("job1", "alice@example.com"))
    group = store.assign_history_group(
        "route_audit", "alice@example.com", "Operations", ["job1"]
    )
    group_id = group["group_id"]

    preference = store.set_history_group_preference(
        "route_audit", "alice@example.com", group_id
    )
    assert preference == {
        "scope": "route_audit",
        "user_email": "alice@example.com",
        "group_id": group_id,
        "fixed": False,
    }
    store.upsert_job(job_record("job2", "alice@example.com"))
    alice_group = store.list_history_groups("route_audit", "alice@example.com")[0]
    assert alice_group["is_default"] is True
    assert alice_group["is_fixed"] is False
    assert alice_group["item_ids"] == ["job1", "job2"]

    store.set_history_group_member(
        "route_audit",
        "alice@example.com",
        group_id,
        "bob@example.com",
        "editor",
    )
    store.upsert_job(job_record("job3", "bob@example.com"))
    with pytest.raises(PermissionError):
        store.set_history_group_preference(
            "route_audit",
            "alice@example.com",
            group_id,
            account_email="bob@example.com",
            fixed=True,
        )

    fixed = store.set_history_group_preference(
        "route_audit",
        "admin@example.com",
        group_id,
        account_email="bob@example.com",
        fixed=True,
        include_all=True,
    )
    assert fixed is not None and fixed["fixed"] is True
    store.upsert_job(job_record("job4", "bob@example.com"))
    admin_group = store.list_history_groups(
        "route_audit", "admin@example.com", include_all=True
    )[0]
    bob = next(
        member for member in admin_group["members"]
        if member["member_email"] == "bob@example.com"
    )
    assert bob["is_default"] is True
    assert bob["is_fixed"] is True
    assert set(admin_group["item_ids"]) == {"job1", "job2", "job3", "job4"}
    with pytest.raises(PermissionError):
        store.move_history_group_items(
            "route_audit", "bob@example.com", None, ["job3"]
        )
    assert store.move_history_group_items(
        "route_audit",
        "admin@example.com",
        None,
        ["job3"],
        include_all=True,
    ) is None


def test_side_tool_workspace_member_can_list_private_run(tmp_path: Path) -> None:
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_side_tool_run(
        "fleet_planner", side_tool_record("run1", "alice@example.com")
    )
    group = store.assign_history_group(
        "fleet_planner", "alice@example.com", "Fleet", ["run1"]
    )
    store.set_history_group_member(
        "fleet_planner",
        "alice@example.com",
        group["group_id"],
        "viewer@example.com",
        "viewer",
    )

    assert [
        item["run_id"]
        for item in store.list_side_tool_runs(
            "fleet_planner", user_email="viewer@example.com"
        )
    ] == ["run1"]


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



def test_backend_service_job_store_uses_sqlite_as_source_of_truth(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)

    job_store = backend_service.JobStore(tmp_path / "jobs")
    created = job_store.create_job(
        {"service_direction": "To School"},
        {"rows": []},
        metadata={"job_name": "sqlite only"},
        owner_email="alice@example.com",
    )

    sqlite_store = SqliteRuntimeStore(sqlite_path)
    stored = sqlite_store.get_job(created["job_id"])
    assert stored is not None
    assert stored["owner_email"] == "alice@example.com"
    assert stored["metadata"]["job_name"] == "sqlite only"
    assert stored["metadata"]["job_queue_scope"]
    assert not (tmp_path / "jobs" / f"{created['job_id']}.json").exists()
    assert not (tmp_path / "jobs" / "index.json").exists()

    assert [entry["job_id"] for entry in job_store.list_jobs(include_all=True)] == [
        created["job_id"]
    ]
    assert [entry["job_id"] for entry in job_store.list_queued_jobs()] == [
        created["job_id"]
    ]

    updated = job_store.update_job(created["job_id"], status="failed", error="boom")
    assert updated is not None
    assert updated["status"] == "failed"
    assert sqlite_store.get_job(created["job_id"])["error"] == "boom"

    job_store.delete_job(created["job_id"])
    assert sqlite_store.get_job(created["job_id"]) is None


def test_backend_service_job_store_marks_queue_scope(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)
    monkeypatch.setattr(backend_service, "JOB_QUEUE_SCOPE", "staging")

    job_store = backend_service.JobStore(tmp_path / "jobs")
    created = job_store.create_job(
        {"service_direction": "To School"},
        {"rows": []},
        metadata={"job_name": "scoped"},
        owner_email="alice@example.com",
    )

    stored = SqliteRuntimeStore(sqlite_path).get_job(created["job_id"])
    assert stored["metadata"]["job_queue_scope"] == "staging"


def test_backend_service_job_store_filters_queued_jobs_by_scope(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    sqlite_store = SqliteRuntimeStore(sqlite_path)
    staging_job = job_record("job1", "alice@example.com")
    staging_job["status"] = "queued"
    staging_job["metadata"] = {"job_queue_scope": "staging"}
    prod_job = job_record("job2", "alice@example.com")
    prod_job["status"] = "queued"
    prod_job["metadata"] = {"job_queue_scope": "prod"}
    sqlite_store.upsert_job(staging_job)
    sqlite_store.upsert_job(prod_job)

    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)
    monkeypatch.setattr(backend_service, "JOB_QUEUE_SCOPE", "staging")

    job_store = backend_service.JobStore(tmp_path / "jobs")

    assert [entry["job_id"] for entry in job_store.list_queued_jobs()] == ["job1"]


def test_backend_service_reconcile_keeps_live_running_worker(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    running = job_record("job1", "alice@example.com")
    running["status"] = "running"
    running["worker_pid"] = 12345
    running["metadata"] = {"job_queue_scope": "staging"}
    SqliteRuntimeStore(sqlite_path).upsert_job(running)

    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)
    monkeypatch.setattr(backend_service, "pid_is_alive", lambda pid: pid == 12345)

    backend_service.JobStore(tmp_path / "jobs")

    stored = SqliteRuntimeStore(sqlite_path).get_job("job1")
    assert stored["status"] == "running"
    assert stored["worker_pid"] == 12345
    assert stored["error"] is None


def test_backend_service_reconcile_fails_dead_running_worker(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    running = job_record("job1", "alice@example.com")
    running["status"] = "running"
    running["worker_pid"] = 12345
    running["metadata"] = {"job_queue_scope": "staging"}
    SqliteRuntimeStore(sqlite_path).upsert_job(running)

    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)
    monkeypatch.setattr(backend_service, "pid_is_alive", lambda pid: False)

    backend_service.JobStore(tmp_path / "jobs")

    stored = SqliteRuntimeStore(sqlite_path).get_job("job1")
    assert stored["status"] == "failed"
    assert stored["worker_pid"] is None
    assert stored["error"] == "Job was interrupted because the backend service restarted."


def test_backend_service_side_tool_store_uses_sqlite_as_source_of_truth(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_service  # noqa: WPS433

    sqlite_path = tmp_path / "runtime.sqlite"
    monkeypatch.setattr(backend_service, "RUNTIME_DB_PATH", sqlite_path)
    monkeypatch.setattr(backend_service, "_RUNTIME_SQLITE_STORE", None)

    history_store = backend_service.SideToolHistoryStore(tmp_path / "side_tools", "fleet_planner")
    summary = history_store.create(
        {
            "title": "Fleet test",
            "scenario": {"market": "KR"},
            "summary": {"route_count": 1},
        },
        owner_email="alice@example.com",
    )

    sqlite_store = SqliteRuntimeStore(sqlite_path)
    stored = sqlite_store.get_side_tool_run("fleet_planner", summary["run_id"])
    assert stored is not None
    assert stored["title"] == "Fleet test"
    assert not (
        tmp_path / "side_tools" / "fleet_planner" / f"{summary['run_id']}.json"
    ).exists()
    assert not (tmp_path / "side_tools" / "fleet_planner" / "index.json").exists()
    assert [entry["run_id"] for entry in history_store.list(include_all=True)] == [
        summary["run_id"]
    ]

    history_store.delete(summary["run_id"])
    assert sqlite_store.get_side_tool_run("fleet_planner", summary["run_id"]) is None


def test_backend_job_runner_reads_and_writes_sqlite_job(
    tmp_path: Path, monkeypatch
) -> None:
    import backend_job_runner  # noqa: WPS433
    import sys

    sqlite_path = tmp_path / "runtime.sqlite"
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH", str(sqlite_path))
    monkeypatch.setattr(
        backend_job_runner,
        "run_backend_planner_with_prepared_data",
        lambda prepared_payload, config: {"ok": True, "rows": prepared_payload["rows"]},
    )
    monkeypatch.setattr(sys, "argv", ["backend_job_runner.py", "job1"])

    payload = job_record("job1", "alice@example.com")
    payload["status"] = "queued"
    payload["prepared_payload"] = {"rows": [{"address": "A"}]}
    SqliteRuntimeStore(sqlite_path).upsert_job(payload)

    assert backend_job_runner.main() == 0

    sqlite_store = SqliteRuntimeStore(sqlite_path)
    stored = sqlite_store.get_job("job1")
    assert stored["status"] == "succeeded"
    assert stored["result"] == {"ok": True, "rows": [{"address": "A"}]}
    assert stored["worker_pid"] is None
    assert stored["job_slot_path"] is None
    assert not (tmp_path / "jobs" / "job1.json").exists()
