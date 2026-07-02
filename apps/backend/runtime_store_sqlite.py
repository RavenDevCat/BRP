from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def load_json_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def job_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(record.get("job_id", "") or ""),
        "owner_email": normalize_email(record.get("owner_email")),
        "shared_with_all": bool(record.get("shared_with_all")),
        "status": str(record.get("status", "queued") or "queued"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "scheduled_start_at": record.get("scheduled_start_at"),
        "scheduled_trigger_label": record.get("scheduled_trigger_label"),
        "metadata": dict(record.get("metadata") or {}),
        "prepared_payload_summary": dict(record.get("prepared_payload_summary") or {}),
        "error": record.get("error"),
    }


def side_tool_summary(tool_key: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(record.get("run_id") or ""),
        "tool_key": str(record.get("tool_key") or tool_key),
        "owner_email": normalize_email(record.get("owner_email")),
        "title": str(record.get("title") or ""),
        "created_at": record.get("created_at"),
        "shared_with_all": bool(record.get("shared_with_all")),
        "summary": dict(record.get("summary") or {}),
    }


@dataclass(frozen=True)
class RuntimeJsonPaths:
    jobs_dir: Path
    side_tools_dir: Path


class SqliteRuntimeStore:
    """Dependency-free SQLite sidecar store for the JSON runtime migration."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_email TEXT NOT NULL DEFAULT '',
                    shared_with_all INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    prepared_payload_summary_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_owner_email ON jobs(owner_email);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE TABLE IF NOT EXISTS side_tool_runs (
                    tool_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    owner_email TEXT NOT NULL DEFAULT '',
                    shared_with_all INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tool_key, run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_side_tool_runs_tool_created
                    ON side_tool_runs(tool_key, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_side_tool_runs_owner
                    ON side_tool_runs(owner_email);
                """
            )
            conn.execute(
                """
                INSERT INTO schema_migrations(name, version, updated_at)
                VALUES('runtime_store', ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (SCHEMA_VERSION, utc_now_iso()),
            )

    def upsert_job(self, record: dict[str, Any]) -> None:
        summary = job_summary(record)
        job_id = str(summary.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(
                    job_id, owner_email, shared_with_all, status, created_at,
                    started_at, finished_at, metadata_json,
                    prepared_payload_summary_json, error, record_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    owner_email = excluded.owner_email,
                    shared_with_all = excluded.shared_with_all,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    metadata_json = excluded.metadata_json,
                    prepared_payload_summary_json = excluded.prepared_payload_summary_json,
                    error = excluded.error,
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    summary["owner_email"],
                    1 if summary["shared_with_all"] else 0,
                    summary["status"],
                    summary.get("created_at"),
                    summary.get("started_at"),
                    summary.get("finished_at"),
                    json_dumps(summary.get("metadata") or {}),
                    json_dumps(summary.get("prepared_payload_summary") or {}),
                    summary.get("error"),
                    json_dumps(record),
                    utc_now_iso(),
                ),
            )

    def claim_queued_job(
        self,
        job_id: str,
        *,
        worker_pid: int | None = None,
        job_slot_path: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status, record_json FROM jobs WHERE job_id = ?",
                    (normalized_job_id,),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return None
                stored_status = str(row["status"] or "").strip().lower()
                record = json_loads(row["record_json"], {})
                if not isinstance(record, dict):
                    conn.rollback()
                    return None
                record_status = str(record.get("status", "")).strip().lower()
                if stored_status != "queued" or record_status != "queued":
                    conn.rollback()
                    return None

                started_at = record.get("started_at") or utc_now_iso()
                record["status"] = "running"
                record["started_at"] = started_at
                record["finished_at"] = None
                record["worker_pid"] = worker_pid
                record["job_slot_path"] = job_slot_path
                record["error"] = None
                record["traceback"] = None
                summary = job_summary(record)
                cursor = conn.execute(
                    """
                    UPDATE jobs SET
                        status = ?,
                        started_at = ?,
                        finished_at = ?,
                        error = ?,
                        record_json = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (
                        summary["status"],
                        summary.get("started_at"),
                        summary.get("finished_at"),
                        summary.get("error"),
                        json_dumps(record),
                        utc_now_iso(),
                        normalized_job_id,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()
                return record
            except Exception:
                conn.rollback()
                raise

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute("SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        payload = json_loads(row["record_json"], {})
        return payload if isinstance(payload, dict) else None

    def list_jobs(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        self.initialize()
        normalized_user = normalize_email(user_email)
        sql = (
            "SELECT job_id, owner_email, shared_with_all, status, created_at, "
            "started_at, finished_at, metadata_json, prepared_payload_summary_json, error FROM jobs"
        )
        params: tuple[Any, ...] = ()
        if not include_all:
            sql += " WHERE shared_with_all = 1 OR owner_email = ?"
            params = (normalized_user,)
        sql += " ORDER BY COALESCE(created_at, started_at, finished_at, '') DESC, job_id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._job_summary_from_row(row) for row in rows]

    def delete_job(self, job_id: str) -> bool:
        self.initialize()
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            return cursor.rowcount > 0

    def upsert_side_tool_run(self, tool_key: str, record: dict[str, Any]) -> None:
        summary = side_tool_summary(tool_key, record)
        run_id = str(summary.get("run_id") or "").strip()
        normalized_tool_key = str(summary.get("tool_key") or tool_key).strip()
        if not normalized_tool_key:
            raise ValueError("tool_key is required")
        if not run_id:
            raise ValueError("run_id is required")
        stored_record = dict(record)
        stored_record["tool_key"] = normalized_tool_key
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO side_tool_runs(
                    tool_key, run_id, owner_email, shared_with_all, title,
                    created_at, summary_json, record_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tool_key, run_id) DO UPDATE SET
                    owner_email = excluded.owner_email,
                    shared_with_all = excluded.shared_with_all,
                    title = excluded.title,
                    created_at = excluded.created_at,
                    summary_json = excluded.summary_json,
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_tool_key,
                    run_id,
                    summary["owner_email"],
                    1 if summary["shared_with_all"] else 0,
                    summary["title"],
                    summary.get("created_at"),
                    json_dumps(summary.get("summary") or {}),
                    json_dumps(stored_record),
                    utc_now_iso(),
                ),
            )

    def get_side_tool_run(self, tool_key: str, run_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM side_tool_runs WHERE tool_key = ? AND run_id = ?",
                (tool_key, run_id),
            ).fetchone()
        if not row:
            return None
        payload = json_loads(row["record_json"], {})
        return payload if isinstance(payload, dict) else None

    def list_side_tool_runs(self, tool_key: str, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        self.initialize()
        normalized_user = normalize_email(user_email)
        sql = "SELECT * FROM side_tool_runs WHERE tool_key = ?"
        params: tuple[Any, ...] = (tool_key,)
        if not include_all:
            sql += " AND (shared_with_all = 1 OR owner_email = ?)"
            params = (tool_key, normalized_user)
        sql += " ORDER BY COALESCE(created_at, '') DESC, run_id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._side_tool_summary_from_row(row) for row in rows]

    def delete_side_tool_run(self, tool_key: str, run_id: str) -> bool:
        self.initialize()
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM side_tool_runs WHERE tool_key = ? AND run_id = ?",
                (tool_key, run_id),
            )
            return cursor.rowcount > 0

    def count_jobs(self) -> int:
        self.initialize()
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def count_side_tool_runs(self) -> int:
        self.initialize()
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM side_tool_runs").fetchone()[0])

    @staticmethod
    def _job_summary_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "owner_email": row["owner_email"],
            "shared_with_all": bool(row["shared_with_all"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "metadata": json_loads(row["metadata_json"], {}),
            "prepared_payload_summary": json_loads(row["prepared_payload_summary_json"], {}),
            "error": row["error"],
        }

    @staticmethod
    def _side_tool_summary_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "tool_key": row["tool_key"],
            "owner_email": row["owner_email"],
            "title": row["title"],
            "created_at": row["created_at"],
            "shared_with_all": bool(row["shared_with_all"]),
            "summary": json_loads(row["summary_json"], {}),
        }


def iter_job_records(jobs_dir: Path) -> Iterable[dict[str, Any]]:
    if not jobs_dir.exists():
        return
    for path in sorted(jobs_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        record = load_json_record(path)
        if record and str(record.get("job_id") or "").strip():
            yield record


def iter_side_tool_records(side_tools_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    if not side_tools_dir.exists():
        return
    for tool_dir in sorted(path for path in side_tools_dir.iterdir() if path.is_dir()):
        for path in sorted(tool_dir.glob("*.json")):
            if path.name == "index.json":
                continue
            record = load_json_record(path)
            if record and str(record.get("run_id") or "").strip():
                yield tool_dir.name, record


def migrate_json_runtime_to_sqlite(paths: RuntimeJsonPaths, db_path: Path) -> dict[str, int]:
    store = SqliteRuntimeStore(db_path)
    store.initialize()
    jobs = 0
    side_tool_runs = 0
    for record in iter_job_records(paths.jobs_dir):
        store.upsert_job(record)
        jobs += 1
    for tool_key, record in iter_side_tool_records(paths.side_tools_dir):
        store.upsert_side_tool_run(tool_key, record)
        side_tool_runs += 1
    return {"jobs": jobs, "side_tool_runs": side_tool_runs}


def verify_json_sqlite_parity(paths: RuntimeJsonPaths, db_path: Path) -> dict[str, Any]:
    store = SqliteRuntimeStore(db_path)
    store.initialize()
    json_jobs = {str(record.get("job_id")): record for record in iter_job_records(paths.jobs_dir)}
    sqlite_job_ids = {entry["job_id"] for entry in store.list_jobs(include_all=True)}
    json_side_tools: dict[tuple[str, str], dict[str, Any]] = {}
    for tool_key, record in iter_side_tool_records(paths.side_tools_dir):
        json_side_tools[(tool_key, str(record.get("run_id")))] = record
    sqlite_side_tools: set[tuple[str, str]] = set()
    for tool_key in sorted({key for key, _ in json_side_tools}):
        for entry in store.list_side_tool_runs(tool_key, include_all=True):
            sqlite_side_tools.add((tool_key, str(entry.get("run_id"))))

    job_summary_mismatches = []
    for job_id, record in sorted(json_jobs.items()):
        sqlite_record = store.get_job(job_id)
        if sqlite_record and job_summary(record) != job_summary(sqlite_record):
            job_summary_mismatches.append(job_id)

    side_tool_summary_mismatches = []
    for (tool_key, run_id), record in sorted(json_side_tools.items()):
        sqlite_record = store.get_side_tool_run(tool_key, run_id)
        if sqlite_record and side_tool_summary(tool_key, record) != side_tool_summary(tool_key, sqlite_record):
            side_tool_summary_mismatches.append({"tool_key": tool_key, "run_id": run_id})

    missing_jobs = sorted(set(json_jobs) - sqlite_job_ids)
    extra_jobs = sorted(sqlite_job_ids - set(json_jobs))
    missing_side_tool_runs = [
        {"tool_key": tool_key, "run_id": run_id}
        for tool_key, run_id in sorted(set(json_side_tools) - sqlite_side_tools)
    ]
    extra_side_tool_runs = [
        {"tool_key": tool_key, "run_id": run_id}
        for tool_key, run_id in sorted(sqlite_side_tools - set(json_side_tools))
    ]
    passed = not (
        missing_jobs
        or extra_jobs
        or missing_side_tool_runs
        or extra_side_tool_runs
        or job_summary_mismatches
        or side_tool_summary_mismatches
    )
    return {
        "passed": passed,
        "json_job_count": len(json_jobs),
        "sqlite_job_count": len(sqlite_job_ids),
        "json_side_tool_run_count": len(json_side_tools),
        "sqlite_side_tool_run_count": len(sqlite_side_tools),
        "missing_jobs": missing_jobs,
        "extra_jobs": extra_jobs,
        "missing_side_tool_runs": missing_side_tool_runs,
        "extra_side_tool_runs": extra_side_tool_runs,
        "job_summary_mismatches": job_summary_mismatches,
        "side_tool_summary_mismatches": side_tool_summary_mismatches,
    }
