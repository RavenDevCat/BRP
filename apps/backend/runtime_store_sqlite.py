from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable
from uuid import uuid4

SCHEMA_VERSION = 4

HISTORY_GROUP_MEMBER_ROLES = {"editor", "viewer"}

SIDE_TOOL_HISTORY_SCOPES = {
    "distance_checker": ("distance_reference", "distance_route_cost"),
    "reference_distance": ("distance_reference",),
    "route_cost": ("distance_route_cost",),
    "fleet_planner": ("fleet_planner",),
    "route_insert_advisor": ("route_insert_advisor",),
}

HISTORY_SCOPE_TOOL_KEYS = {
    scope: tuple(
        tool_key
        for tool_key, scopes in SIDE_TOOL_HISTORY_SCOPES.items()
        if scope in scopes
    )
    for scope in {
        scope
        for scopes in SIDE_TOOL_HISTORY_SCOPES.values()
        for scope in scopes
    }
}


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
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
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
                CREATE TABLE IF NOT EXISTS history_groups (
                    group_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    owner_email TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_history_groups_owner_scope_name
                    ON history_groups(owner_email, scope, name COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS history_group_items (
                    scope TEXT NOT NULL,
                    owner_email TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(scope, owner_email, item_id),
                    FOREIGN KEY(group_id) REFERENCES history_groups(group_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_history_group_items_group
                    ON history_group_items(group_id);
                CREATE TABLE IF NOT EXISTS history_group_members (
                    group_id TEXT NOT NULL,
                    member_email TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'editor', 'viewer')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, member_email),
                    FOREIGN KEY(group_id) REFERENCES history_groups(group_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_history_group_members_email
                    ON history_group_members(member_email);
                CREATE TABLE IF NOT EXISTS history_group_preferences (
                    scope TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    fixed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, user_email),
                    FOREIGN KEY(group_id) REFERENCES history_groups(group_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_history_group_preferences_group
                    ON history_group_preferences(group_id);
                """
                )
                duplicate_item = conn.execute(
                    """
                    SELECT scope, item_id
                    FROM history_group_items
                    GROUP BY scope, item_id
                    HAVING COUNT(*) > 1
                    LIMIT 1
                    """
                ).fetchone()
                if duplicate_item:
                    raise RuntimeError(
                        "Cannot upgrade history workspaces while one history item "
                        "belongs to multiple groups."
                    )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_history_group_items_scope_item
                    ON history_group_items(scope, item_id)
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO history_group_members(
                        group_id, member_email, role, created_at, updated_at
                    )
                    SELECT group_id, owner_email, 'owner', created_at, updated_at
                    FROM history_groups
                    """
                )
                conn.execute(
                    """
                    UPDATE history_group_members
                    SET role = 'owner'
                    WHERE EXISTS(
                        SELECT 1 FROM history_groups
                        WHERE history_groups.group_id = history_group_members.group_id
                            AND history_groups.owner_email = history_group_members.member_email
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO schema_migrations(name, version, updated_at)
                    VALUES('runtime_store', ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    WHERE schema_migrations.version < excluded.version
                    """,
                    (SCHEMA_VERSION, utc_now_iso()),
                )
            self._initialized = True

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
            self._apply_history_group_preference(
                conn, "route_audit", job_id, summary["owner_email"]
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
            sql += """
                WHERE shared_with_all = 1 OR owner_email = ? OR EXISTS(
                    SELECT 1
                    FROM history_group_items
                    JOIN history_groups
                        ON history_groups.group_id = history_group_items.group_id
                    LEFT JOIN history_group_members
                        ON history_group_members.group_id = history_groups.group_id
                        AND history_group_members.member_email = ?
                    WHERE history_group_items.scope = 'route_audit'
                        AND history_group_items.item_id = jobs.job_id
                        AND (
                            history_groups.owner_email = ?
                            OR history_group_members.member_email IS NOT NULL
                        )
                )
            """
            params = (normalized_user, normalized_user, normalized_user)
        sql += " ORDER BY COALESCE(created_at, started_at, finished_at, '') DESC, job_id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._job_summary_from_row(row) for row in rows]

    def delete_job(self, job_id: str) -> bool:
        self.initialize()
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            if cursor.rowcount:
                self._delete_history_group_items(conn, ("route_audit",), job_id)
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
            for scope in SIDE_TOOL_HISTORY_SCOPES.get(normalized_tool_key, ()):
                self._apply_history_group_preference(
                    conn, scope, run_id, summary["owner_email"]
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
            scopes = SIDE_TOOL_HISTORY_SCOPES.get(tool_key, ())
            workspace_clause = ""
            workspace_params: tuple[Any, ...] = ()
            if scopes:
                placeholders = ",".join("?" for _ in scopes)
                workspace_clause = f""" OR EXISTS(
                    SELECT 1
                    FROM history_group_items
                    JOIN history_groups
                        ON history_groups.group_id = history_group_items.group_id
                    LEFT JOIN history_group_members
                        ON history_group_members.group_id = history_groups.group_id
                        AND history_group_members.member_email = ?
                    WHERE history_group_items.scope IN ({placeholders})
                        AND history_group_items.item_id = side_tool_runs.run_id
                        AND (
                            history_groups.owner_email = ?
                            OR history_group_members.member_email IS NOT NULL
                        )
                )"""
                workspace_params = (normalized_user, *scopes, normalized_user)
            sql += f" AND (shared_with_all = 1 OR owner_email = ?{workspace_clause})"
            params = (tool_key, normalized_user, *workspace_params)
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
            if cursor.rowcount:
                self._delete_history_group_items(
                    conn, SIDE_TOOL_HISTORY_SCOPES.get(tool_key, ()), run_id
                )
            return cursor.rowcount > 0

    def list_history_groups(
        self,
        scope: str,
        user_email: str,
        *,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        self.initialize()
        normalized_user = normalize_email(user_email)
        with self.connect() as conn:
            if include_all:
                groups = conn.execute(
                    """
                    SELECT group_id, owner_email, name, created_at, updated_at,
                        NULL AS member_role
                    FROM history_groups
                    WHERE scope = ?
                    ORDER BY updated_at DESC, name COLLATE NOCASE
                    """,
                    (scope,),
                ).fetchall()
            else:
                groups = conn.execute(
                    """
                    SELECT history_groups.group_id, history_groups.owner_email,
                        history_groups.name, history_groups.created_at,
                        history_groups.updated_at, history_group_members.role AS member_role
                    FROM history_groups
                    LEFT JOIN history_group_members
                        ON history_group_members.group_id = history_groups.group_id
                        AND history_group_members.member_email = ?
                    WHERE history_groups.scope = ?
                        AND (
                            history_groups.owner_email = ?
                            OR history_group_members.member_email IS NOT NULL
                        )
                    ORDER BY history_groups.updated_at DESC,
                        history_groups.name COLLATE NOCASE
                    """,
                    (normalized_user, scope, normalized_user),
                ).fetchall()
            group_ids = [str(group["group_id"]) for group in groups]
            if not group_ids:
                return []
            placeholders = ",".join("?" for _ in group_ids)
            items = conn.execute(
                f"""
                SELECT group_id, item_id
                FROM history_group_items
                WHERE group_id IN ({placeholders})
                ORDER BY created_at, item_id
                """,
                group_ids,
            ).fetchall()
            members = conn.execute(
                f"""
                SELECT group_id, member_email, role
                FROM history_group_members
                WHERE group_id IN ({placeholders})
                ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                    member_email
                """,
                group_ids,
            ).fetchall()
            preferences = conn.execute(
                f"""
                SELECT group_id, user_email, fixed
                FROM history_group_preferences
                WHERE group_id IN ({placeholders})
                """,
                group_ids,
            ).fetchall()
        item_ids_by_group: dict[str, list[str]] = {}
        for item in items:
            item_ids_by_group.setdefault(str(item["group_id"]), []).append(
                str(item["item_id"])
            )
        members_by_group: dict[str, list[dict[str, Any]]] = {}
        preferences_by_group: dict[str, dict[str, bool]] = {}
        for preference in preferences:
            preferences_by_group.setdefault(str(preference["group_id"]), {})[
                str(preference["user_email"])
            ] = bool(preference["fixed"])
        for member in members:
            group_preferences = preferences_by_group.get(str(member["group_id"]), {})
            member_email = str(member["member_email"])
            members_by_group.setdefault(str(member["group_id"]), []).append(
                {
                    "member_email": member_email,
                    "role": str(member["role"]),
                    "is_default": member_email in group_preferences,
                    "is_fixed": bool(group_preferences.get(member_email)),
                }
            )
        return [
            {
                "group_id": str(group["group_id"]),
                "owner_email": str(group["owner_email"]),
                "name": str(group["name"]),
                "item_ids": item_ids_by_group.get(str(group["group_id"]), []),
                "role": (
                    "admin"
                    if include_all
                    else "owner"
                    if normalize_email(group["owner_email"]) == normalized_user
                    else str(group["member_role"] or "viewer")
                ),
                "members": members_by_group.get(str(group["group_id"]), []),
                "is_default": normalized_user
                in preferences_by_group.get(str(group["group_id"]), {}),
                "is_fixed": bool(
                    preferences_by_group.get(str(group["group_id"]), {}).get(
                        normalized_user
                    )
                ),
                "created_at": str(group["created_at"]),
                "updated_at": str(group["updated_at"]),
            }
            for group in groups
        ]

    def history_item_role(
        self,
        scope: str,
        item_id: str,
        user_email: str,
        *,
        include_all: bool = False,
    ) -> str | None:
        self.initialize()
        normalized_user = normalize_email(user_email)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT history_groups.owner_email, history_group_members.role
                FROM history_group_items
                JOIN history_groups
                    ON history_groups.group_id = history_group_items.group_id
                LEFT JOIN history_group_members
                    ON history_group_members.group_id = history_groups.group_id
                    AND history_group_members.member_email = ?
                WHERE history_group_items.scope = ?
                    AND history_group_items.item_id = ?
                """,
                (normalized_user, scope, str(item_id or "").strip()),
            ).fetchone()
        if not row:
            return None
        if include_all:
            return "admin"
        if normalize_email(row["owner_email"]) == normalized_user:
            return "owner"
        role = str(row["role"] or "").strip().lower()
        return role if role in HISTORY_GROUP_MEMBER_ROLES else None

    @staticmethod
    def _history_group_role(
        conn: sqlite3.Connection,
        scope: str,
        group_id: str,
        user_email: str,
        *,
        include_all: bool = False,
    ) -> str | None:
        normalized_user = normalize_email(user_email)
        row = conn.execute(
            """
            SELECT history_groups.owner_email, history_group_members.role
            FROM history_groups
            LEFT JOIN history_group_members
                ON history_group_members.group_id = history_groups.group_id
                AND history_group_members.member_email = ?
            WHERE history_groups.scope = ? AND history_groups.group_id = ?
            """,
            (normalized_user, scope, str(group_id or "").strip()),
        ).fetchone()
        if not row:
            return None
        if include_all:
            return "admin"
        if normalize_email(row["owner_email"]) == normalized_user:
            return "owner"
        role = str(row["role"] or "").strip().lower()
        return role if role in HISTORY_GROUP_MEMBER_ROLES else None

    @staticmethod
    def _history_item_owner(
        conn: sqlite3.Connection, scope: str, item_id: str
    ) -> str:
        if scope == "route_audit":
            row = conn.execute(
                "SELECT owner_email FROM jobs WHERE job_id = ?", (item_id,)
            ).fetchone()
        else:
            tool_keys = HISTORY_SCOPE_TOOL_KEYS.get(scope, ())
            if not tool_keys:
                return ""
            placeholders = ",".join("?" for _ in tool_keys)
            row = conn.execute(
                f"""
                SELECT owner_email FROM side_tool_runs
                WHERE run_id = ? AND tool_key IN ({placeholders})
                LIMIT 1
                """,
                (item_id, *tool_keys),
            ).fetchone()
        return normalize_email(row["owner_email"]) if row else ""

    @classmethod
    def _fixed_history_group_for_item(
        cls, conn: sqlite3.Connection, scope: str, item_id: str
    ) -> str | None:
        owner_email = cls._history_item_owner(conn, scope, item_id)
        if not owner_email:
            return None
        row = conn.execute(
            """
            SELECT group_id FROM history_group_preferences
            WHERE scope = ? AND user_email = ? AND fixed = 1
            """,
            (scope, owner_email),
        ).fetchone()
        return str(row["group_id"]) if row else None

    @staticmethod
    def _apply_history_group_preference(
        conn: sqlite3.Connection,
        scope: str,
        item_id: str,
        owner_email: str,
    ) -> None:
        normalized_owner = normalize_email(owner_email)
        if not normalized_owner:
            return
        existing = conn.execute(
            """
            SELECT 1 FROM history_group_items
            WHERE scope = ? AND item_id = ?
            """,
            (scope, item_id),
        ).fetchone()
        if existing:
            return
        preference = conn.execute(
            """
            SELECT history_group_preferences.group_id, history_groups.owner_email
            FROM history_group_preferences
            JOIN history_groups
                ON history_groups.group_id = history_group_preferences.group_id
                AND history_groups.scope = history_group_preferences.scope
            LEFT JOIN history_group_members
                ON history_group_members.group_id = history_groups.group_id
                AND history_group_members.member_email = ?
            WHERE history_group_preferences.scope = ?
                AND history_group_preferences.user_email = ?
                AND (
                    history_groups.owner_email = ?
                    OR history_group_members.role = 'editor'
                )
            """,
            (normalized_owner, scope, normalized_owner, normalized_owner),
        ).fetchone()
        if not preference:
            return
        now = utc_now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO history_group_items(
                scope, owner_email, item_id, group_id, created_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                scope,
                normalize_email(preference["owner_email"]),
                item_id,
                str(preference["group_id"]),
                now,
            ),
        )
        conn.execute(
            "UPDATE history_groups SET updated_at = ? WHERE group_id = ?",
            (now, str(preference["group_id"])),
        )

    def set_history_group_preference(
        self,
        scope: str,
        actor_email: str,
        group_id: str | None,
        *,
        account_email: str | None = None,
        fixed: bool = False,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_actor = normalize_email(actor_email)
        normalized_account = normalize_email(account_email or actor_email)
        target_group_id = str(group_id or "").strip() or None
        if not scope or not normalized_actor or not normalized_account:
            raise ValueError("scope and account_email are required")
        if normalized_account != normalized_actor and not include_all:
            raise PermissionError(
                "Only an admin can set another account's default workspace."
            )
        if fixed and not include_all:
            raise PermissionError("Only an admin can fix an account to a workspace.")
        self.initialize()
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT group_id, fixed FROM history_group_preferences
                WHERE scope = ? AND user_email = ?
                """,
                (scope, normalized_account),
            ).fetchone()
            if existing and bool(existing["fixed"]) and not include_all:
                raise PermissionError(
                    "This account is fixed to a workspace by an admin."
                )
            if not target_group_id:
                conn.execute(
                    """
                    DELETE FROM history_group_preferences
                    WHERE scope = ? AND user_email = ?
                    """,
                    (scope, normalized_account),
                )
                conn.commit()
                return None
            target = conn.execute(
                """
                SELECT group_id FROM history_groups
                WHERE scope = ? AND group_id = ?
                """,
                (scope, target_group_id),
            ).fetchone()
            if not target:
                conn.rollback()
                return None
            account_role = self._history_group_role(
                conn,
                scope,
                target_group_id,
                normalized_account,
                include_all=False,
            )
            if account_role not in {"owner", "editor"}:
                raise PermissionError(
                    "A default workspace requires Owner or Editor access."
                )
            conn.execute(
                """
                INSERT INTO history_group_preferences(
                    scope, user_email, group_id, fixed, updated_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(scope, user_email) DO UPDATE SET
                    group_id = excluded.group_id,
                    fixed = excluded.fixed,
                    updated_at = excluded.updated_at
                """,
                (
                    scope,
                    normalized_account,
                    target_group_id,
                    1 if fixed else 0,
                    now,
                ),
            )
            if fixed:
                if scope == "route_audit":
                    item_rows = conn.execute(
                        "SELECT job_id AS item_id FROM jobs WHERE owner_email = ?",
                        (normalized_account,),
                    ).fetchall()
                else:
                    tool_keys = HISTORY_SCOPE_TOOL_KEYS.get(scope, ())
                    placeholders = ",".join("?" for _ in tool_keys)
                    item_rows = (
                        conn.execute(
                            f"""
                            SELECT DISTINCT run_id AS item_id FROM side_tool_runs
                            WHERE owner_email = ? AND tool_key IN ({placeholders})
                            """,
                            (normalized_account, *tool_keys),
                        ).fetchall()
                        if tool_keys
                        else []
                    )
                item_ids = [str(row["item_id"]) for row in item_rows]
                if item_ids:
                    placeholders = ",".join("?" for _ in item_ids)
                    conn.execute(
                        f"""
                        DELETE FROM history_group_items
                        WHERE scope = ? AND item_id IN ({placeholders})
                        """,
                        (scope, *item_ids),
                    )
                    group_owner = conn.execute(
                        "SELECT owner_email FROM history_groups WHERE group_id = ?",
                        (target_group_id,),
                    ).fetchone()["owner_email"]
                    conn.executemany(
                        """
                        INSERT INTO history_group_items(
                            scope, owner_email, item_id, group_id, created_at
                        ) VALUES(?, ?, ?, ?, ?)
                        """,
                        [
                            (scope, group_owner, item_id, target_group_id, now)
                            for item_id in item_ids
                        ],
                    )
                conn.execute(
                    "UPDATE history_groups SET updated_at = ? WHERE group_id = ?",
                    (now, target_group_id),
                )
            conn.commit()
        return {
            "scope": scope,
            "user_email": normalized_account,
            "group_id": target_group_id,
            "fixed": bool(fixed),
        }

    def transfer_history_group_owner(
        self,
        scope: str,
        actor_email: str,
        group_id: str,
        owner_email: str,
        *,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_owner = normalize_email(owner_email)
        if not normalized_owner:
            raise ValueError("owner_email is required")
        self.initialize()
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            group = conn.execute(
                """
                SELECT owner_email, name FROM history_groups
                WHERE group_id = ? AND scope = ?
                """,
                (group_id, scope),
            ).fetchone()
            if not group:
                conn.rollback()
                return None
            actor_role = self._history_group_role(
                conn,
                scope,
                group_id,
                actor_email,
                include_all=include_all,
            )
            if actor_role not in {"owner", "admin"}:
                raise PermissionError(
                    "Workspace ownership can only be transferred by the owner or an admin."
                )
            previous_owner = normalize_email(group["owner_email"])
            if previous_owner == normalized_owner:
                conn.commit()
            else:
                duplicate = conn.execute(
                    """
                    SELECT 1 FROM history_groups
                    WHERE scope = ? AND owner_email = ? AND name = ? COLLATE NOCASE
                        AND group_id <> ?
                    """,
                    (scope, normalized_owner, str(group["name"]), group_id),
                ).fetchone()
                if duplicate:
                    raise ValueError(
                        "The new owner already has a workspace with this name."
                    )
                conn.execute(
                    """
                    UPDATE history_groups
                    SET owner_email = ?, updated_at = ?
                    WHERE group_id = ? AND scope = ?
                    """,
                    (normalized_owner, now, group_id, scope),
                )
                conn.execute(
                    """
                    UPDATE history_group_items SET owner_email = ?
                    WHERE group_id = ? AND scope = ?
                    """,
                    (normalized_owner, group_id, scope),
                )
                conn.execute(
                    """
                    INSERT INTO history_group_members(
                        group_id, member_email, role, created_at, updated_at
                    ) VALUES(?, ?, 'owner', ?, ?)
                    ON CONFLICT(group_id, member_email) DO UPDATE SET
                        role = 'owner', updated_at = excluded.updated_at
                    """,
                    (group_id, normalized_owner, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO history_group_members(
                        group_id, member_email, role, created_at, updated_at
                    ) VALUES(?, ?, 'editor', ?, ?)
                    ON CONFLICT(group_id, member_email) DO UPDATE SET
                        role = 'editor', updated_at = excluded.updated_at
                    """,
                    (group_id, previous_owner, now, now),
                )
                conn.commit()
        return next(
            group
            for group in self.list_history_groups(
                scope, actor_email, include_all=include_all
            )
            if group["group_id"] == group_id
        )

    def assign_history_group(
        self,
        scope: str,
        owner_email: str,
        name: str,
        item_ids: Iterable[str],
        *,
        include_all: bool = False,
    ) -> dict[str, Any]:
        normalized_owner = normalize_email(owner_email)
        normalized_name = str(name or "").strip()
        normalized_ids = list(
            dict.fromkeys(
                str(item_id or "").strip()
                for item_id in item_ids
                if str(item_id or "").strip()
            )
        )
        if not scope or not normalized_owner or not normalized_name or not normalized_ids:
            raise ValueError("scope, owner_email, name, and item_ids are required")
        self.initialize()
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT group_id FROM history_groups
                WHERE scope = ? AND owner_email = ? AND name = ? COLLATE NOCASE
                """,
                (scope, normalized_owner, normalized_name),
            ).fetchone()
            group_id = str(row["group_id"]) if row else uuid4().hex
            if not row:
                conn.execute(
                    """
                    INSERT INTO history_groups(
                        group_id, scope, owner_email, name, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        scope,
                        normalized_owner,
                        normalized_name,
                        now,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO history_group_members(
                    group_id, member_email, role, created_at, updated_at
                ) VALUES(?, ?, 'owner', ?, ?)
                ON CONFLICT(group_id, member_email) DO UPDATE SET
                    role = 'owner',
                    updated_at = excluded.updated_at
                """,
                (group_id, normalized_owner, now, now),
            )
            if not include_all:
                for item_id in normalized_ids:
                    fixed_group_id = self._fixed_history_group_for_item(
                        conn, scope, item_id
                    )
                    if fixed_group_id and fixed_group_id != group_id:
                        raise PermissionError(
                            "This history item is fixed to another workspace by an admin."
                        )
            placeholders = ",".join("?" for _ in normalized_ids)
            source_groups = conn.execute(
                f"""
                SELECT DISTINCT group_id
                FROM history_group_items
                WHERE scope = ? AND item_id IN ({placeholders})
                """,
                (scope, *normalized_ids),
            ).fetchall()
            for source_group in source_groups:
                source_group_id = str(source_group["group_id"])
                if source_group_id == group_id:
                    continue
                role = self._history_group_role(
                    conn,
                    scope,
                    source_group_id,
                    normalized_owner,
                    include_all=include_all,
                )
                if role not in {"owner", "admin"}:
                    raise PermissionError(
                        "A history item can only be moved by its workspace owner "
                        "or an admin."
                    )
            conn.execute(
                f"""
                DELETE FROM history_group_items
                WHERE scope = ? AND item_id IN ({placeholders})
                """,
                (scope, *normalized_ids),
            )
            conn.executemany(
                """
                INSERT INTO history_group_items(
                    scope, owner_email, item_id, group_id, created_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                [
                    (scope, normalized_owner, item_id, group_id, now)
                    for item_id in normalized_ids
                ],
            )
            conn.execute(
                "UPDATE history_groups SET updated_at = ? WHERE group_id = ?",
                (now, group_id),
            )
            conn.commit()
        return next(
            group
            for group in self.list_history_groups(
                scope, normalized_owner, include_all=include_all
            )
            if group["group_id"] == group_id
        )

    def rename_history_group(
        self,
        scope: str,
        actor_email: str,
        group_id: str,
        name: str,
        *,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("name is required")
        self.initialize()
        with self.connect() as conn:
            group = conn.execute(
                """
                SELECT owner_email FROM history_groups
                WHERE group_id = ? AND scope = ?
                """,
                (group_id, scope),
            ).fetchone()
            if not group:
                return None
            role = self._history_group_role(
                conn,
                scope,
                group_id,
                actor_email,
                include_all=include_all,
            )
            if role not in {"owner", "admin"}:
                raise PermissionError(
                    "A history workspace can only be renamed by its owner or an admin."
                )
            group_owner = normalize_email(group["owner_email"])
            duplicate = conn.execute(
                """
                SELECT 1 FROM history_groups
                WHERE scope = ? AND owner_email = ? AND name = ? COLLATE NOCASE
                    AND group_id <> ?
                """,
                (scope, group_owner, normalized_name, group_id),
            ).fetchone()
            if duplicate:
                raise ValueError("A history group with this name already exists.")
            cursor = conn.execute(
                """
                UPDATE history_groups SET name = ?, updated_at = ?
                WHERE group_id = ? AND scope = ?
                """,
                (
                    normalized_name,
                    utc_now_iso(),
                    group_id,
                    scope,
                ),
            )
        if not cursor.rowcount:
            return None
        return next(
            group
            for group in self.list_history_groups(
                scope, actor_email, include_all=include_all
            )
            if group["group_id"] == group_id
        )

    def set_history_group_member(
        self,
        scope: str,
        actor_email: str,
        group_id: str,
        member_email: str,
        role: str,
        *,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_member = normalize_email(member_email)
        normalized_role = str(role or "").strip().lower()
        if not normalized_member or normalized_role not in HISTORY_GROUP_MEMBER_ROLES:
            raise ValueError("member_email and an editor or viewer role are required")
        self.initialize()
        with self.connect() as conn:
            group = conn.execute(
                """
                SELECT owner_email FROM history_groups
                WHERE group_id = ? AND scope = ?
                """,
                (group_id, scope),
            ).fetchone()
            if not group:
                return None
            actor_role = self._history_group_role(
                conn,
                scope,
                group_id,
                actor_email,
                include_all=include_all,
            )
            if actor_role not in {"owner", "admin"}:
                raise PermissionError(
                    "Workspace members can only be managed by the owner or an admin."
                )
            if normalize_email(group["owner_email"]) == normalized_member:
                raise ValueError("The workspace owner role cannot be changed.")
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO history_group_members(
                    group_id, member_email, role, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(group_id, member_email) DO UPDATE SET
                    role = excluded.role,
                    updated_at = excluded.updated_at
                """,
                (group_id, normalized_member, normalized_role, now, now),
            )
            if normalized_role == "viewer":
                conn.execute(
                    """
                    DELETE FROM history_group_preferences
                    WHERE scope = ? AND user_email = ? AND group_id = ?
                    """,
                    (scope, normalized_member, group_id),
                )
        return next(
            group
            for group in self.list_history_groups(
                scope, actor_email, include_all=include_all
            )
            if group["group_id"] == group_id
        )

    def remove_history_group_member(
        self,
        scope: str,
        actor_email: str,
        group_id: str,
        member_email: str,
        *,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_member = normalize_email(member_email)
        if not normalized_member:
            raise ValueError("member_email is required")
        self.initialize()
        with self.connect() as conn:
            group = conn.execute(
                """
                SELECT owner_email FROM history_groups
                WHERE group_id = ? AND scope = ?
                """,
                (group_id, scope),
            ).fetchone()
            if not group:
                return None
            role = self._history_group_role(
                conn,
                scope,
                group_id,
                actor_email,
                include_all=include_all,
            )
            if role not in {"owner", "admin"}:
                raise PermissionError(
                    "Workspace members can only be managed by the owner or an admin."
                )
            if normalize_email(group["owner_email"]) == normalized_member:
                raise ValueError("The workspace owner cannot be removed.")
            conn.execute(
                """
                DELETE FROM history_group_members
                WHERE group_id = ? AND member_email = ?
                """,
                (group_id, normalized_member),
            )
            conn.execute(
                """
                DELETE FROM history_group_preferences
                WHERE scope = ? AND user_email = ? AND group_id = ?
                """,
                (scope, normalized_member, group_id),
            )
        return next(
            group
            for group in self.list_history_groups(
                scope, actor_email, include_all=include_all
            )
            if group["group_id"] == group_id
        )

    def move_history_group_items(
        self,
        scope: str,
        actor_email: str,
        group_id: str | None,
        item_ids: Iterable[str],
        *,
        include_all: bool = False,
    ) -> dict[str, Any] | None:
        normalized_ids = list(
            dict.fromkeys(
                str(item_id or "").strip()
                for item_id in item_ids
                if str(item_id or "").strip()
            )
        )
        if not normalized_ids:
            raise ValueError("item_ids are required")
        target_group_id = str(group_id or "").strip() or None
        self.initialize()
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target_owner = ""
            if target_group_id:
                target = conn.execute(
                    """
                    SELECT owner_email FROM history_groups
                    WHERE group_id = ? AND scope = ?
                    """,
                    (target_group_id, scope),
                ).fetchone()
                if not target:
                    conn.rollback()
                    return None
                target_role = self._history_group_role(
                    conn,
                    scope,
                    target_group_id,
                    actor_email,
                    include_all=include_all,
                )
                if target_role not in {"owner", "editor", "admin"}:
                    raise PermissionError(
                        "History items can only be added to a workspace by an editor, "
                        "owner, or admin."
                    )
                target_owner = normalize_email(target["owner_email"])
            if not include_all:
                for item_id in normalized_ids:
                    fixed_group_id = self._fixed_history_group_for_item(
                        conn, scope, item_id
                    )
                    if fixed_group_id and fixed_group_id != target_group_id:
                        raise PermissionError(
                            "This history item is fixed to another workspace by an admin."
                        )
            placeholders = ",".join("?" for _ in normalized_ids)
            source_groups = conn.execute(
                f"""
                SELECT DISTINCT group_id
                FROM history_group_items
                WHERE scope = ? AND item_id IN ({placeholders})
                """,
                (scope, *normalized_ids),
            ).fetchall()
            source_group_ids = {str(row["group_id"]) for row in source_groups}
            for source_group_id in source_group_ids:
                if source_group_id == target_group_id:
                    continue
                source_role = self._history_group_role(
                    conn,
                    scope,
                    source_group_id,
                    actor_email,
                    include_all=include_all,
                )
                if source_role not in {"owner", "editor", "admin"}:
                    raise PermissionError(
                        "History items can only be moved by a workspace editor, "
                        "owner, or admin."
                    )
            conn.execute(
                f"""
                DELETE FROM history_group_items
                WHERE scope = ? AND item_id IN ({placeholders})
                """,
                (scope, *normalized_ids),
            )
            if target_group_id:
                conn.executemany(
                    """
                    INSERT INTO history_group_items(
                        scope, owner_email, item_id, group_id, created_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    [
                        (scope, target_owner, item_id, target_group_id, now)
                        for item_id in normalized_ids
                    ],
                )
            touched_group_ids = source_group_ids | ({target_group_id} if target_group_id else set())
            if touched_group_ids:
                touched_placeholders = ",".join("?" for _ in touched_group_ids)
                conn.execute(
                    f"""
                    UPDATE history_groups SET updated_at = ?
                    WHERE group_id IN ({touched_placeholders})
                    """,
                    (now, *sorted(touched_group_ids)),
                )
            conn.commit()
        if not target_group_id:
            return None
        return next(
            group
            for group in self.list_history_groups(
                scope, actor_email, include_all=include_all
            )
            if group["group_id"] == target_group_id
        )

    def delete_history_group(
        self,
        scope: str,
        actor_email: str,
        group_id: str,
        *,
        include_all: bool = False,
    ) -> bool:
        self.initialize()
        with self.connect() as conn:
            role = self._history_group_role(
                conn,
                scope,
                group_id,
                actor_email,
                include_all=include_all,
            )
            if role is None:
                return False
            if role not in {"owner", "admin"}:
                raise PermissionError(
                    "A history workspace can only be deleted by its owner or an admin."
                )
            cursor = conn.execute(
                "DELETE FROM history_groups WHERE group_id = ? AND scope = ?",
                (group_id, scope),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def _delete_history_group_items(
        conn: sqlite3.Connection, scopes: Iterable[str], item_id: str
    ) -> None:
        normalized_scopes = tuple(scope for scope in scopes if scope)
        if not normalized_scopes:
            return
        placeholders = ",".join("?" for _ in normalized_scopes)
        conn.execute(
            f"""
            DELETE FROM history_group_items
            WHERE scope IN ({placeholders}) AND item_id = ?
            """,
            (*normalized_scopes, item_id),
        )

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
        metadata = json_loads(row["metadata_json"], {})
        if isinstance(metadata, dict):
            metadata = {
                key: value
                for key, value in metadata.items()
                if key not in {"client_prep", "planner_config"}
            }
        else:
            metadata = {}
        return {
            "job_id": row["job_id"],
            "owner_email": row["owner_email"],
            "shared_with_all": bool(row["shared_with_all"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "metadata": metadata,
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
