#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]


def parse_env_file(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = path.expanduser()
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def env_value(key: str, env_file_values: dict[str, str]) -> str:
    return env_file_values.get(key) or os.environ.get(key, "")


def resolve_path(value: Path | None, fallback: Path) -> Path:
    return (value or fallback).expanduser()


def count_job_archive(jobs_dir: Path) -> int:
    if not jobs_dir.exists():
        return 0
    return sum(1 for path in jobs_dir.glob("*.json") if path.name != "index.json")


def count_side_tool_archive(side_tools_dir: Path) -> int:
    if not side_tools_dir.exists():
        return 0
    total = 0
    for tool_dir in side_tools_dir.iterdir():
        if tool_dir.is_dir():
            total += sum(1 for path in tool_dir.glob("*.json") if path.name != "index.json")
    return total


def query_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def sqlite_status(sqlite_path: Path, recent_limit: int) -> dict[str, Any]:
    if not sqlite_path.exists():
        return {
            "sqlite_exists": False,
            "schema_version": None,
            "job_count": 0,
            "side_tool_run_count": 0,
            "jobs_by_status": {},
            "side_tool_runs_by_tool": {},
            "latest_job_updated_at": None,
            "latest_side_tool_updated_at": None,
            "recent_jobs": [],
            "recent_side_tool_runs": [],
            "error": "sqlite database does not exist",
        }

    try:
        conn = sqlite3.connect(str(sqlite_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        with conn:
            schema_table_exists = table_exists(conn, "schema_migrations")
            jobs_table_exists = table_exists(conn, "jobs")
            side_tool_table_exists = table_exists(conn, "side_tool_runs")
            if not jobs_table_exists or not side_tool_table_exists:
                return {
                    "sqlite_exists": True,
                    "schema_version": None,
                    "job_count": 0,
                    "side_tool_run_count": 0,
                    "jobs_by_status": {},
                    "side_tool_runs_by_tool": {},
                    "latest_job_updated_at": None,
                    "latest_side_tool_updated_at": None,
                    "recent_jobs": [],
                    "recent_side_tool_runs": [],
                    "error": "required runtime tables are missing",
                }

            schema_version = None
            if schema_table_exists:
                row = conn.execute(
                    "SELECT version FROM schema_migrations WHERE name = 'runtime_store'"
                ).fetchone()
                schema_version = row["version"] if row else None

            jobs_by_status = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status ORDER BY status"
                )
            }
            side_tool_runs_by_tool = {
                row["tool_key"]: row["count"]
                for row in conn.execute(
                    """
                    SELECT tool_key, COUNT(*) AS count
                    FROM side_tool_runs
                    GROUP BY tool_key
                    ORDER BY tool_key
                    """
                )
            }
            job_count = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            side_tool_run_count = int(conn.execute("SELECT COUNT(*) FROM side_tool_runs").fetchone()[0])
            latest_job_updated_at = conn.execute("SELECT MAX(updated_at) FROM jobs").fetchone()[0]
            latest_side_tool_updated_at = conn.execute("SELECT MAX(updated_at) FROM side_tool_runs").fetchone()[0]
            recent_jobs = query_dicts(
                conn,
                """
                SELECT job_id, owner_email, status, created_at, updated_at
                FROM jobs
                ORDER BY COALESCE(updated_at, created_at, '') DESC, job_id DESC
                LIMIT ?
                """,
                (recent_limit,),
            )
            recent_side_tool_runs = query_dicts(
                conn,
                """
                SELECT tool_key, run_id, owner_email, title, created_at, updated_at
                FROM side_tool_runs
                ORDER BY COALESCE(updated_at, created_at, '') DESC, tool_key, run_id
                LIMIT ?
                """,
                (recent_limit,),
            )
    except Exception as exc:
        return {
            "sqlite_exists": sqlite_path.exists(),
            "schema_version": None,
            "job_count": 0,
            "side_tool_run_count": 0,
            "jobs_by_status": {},
            "side_tool_runs_by_tool": {},
            "latest_job_updated_at": None,
            "latest_side_tool_updated_at": None,
            "recent_jobs": [],
            "recent_side_tool_runs": [],
            "error": str(exc),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "sqlite_exists": True,
        "schema_version": schema_version,
        "job_count": job_count,
        "side_tool_run_count": side_tool_run_count,
        "jobs_by_status": jobs_by_status,
        "side_tool_runs_by_tool": side_tool_runs_by_tool,
        "latest_job_updated_at": latest_job_updated_at,
        "latest_side_tool_updated_at": latest_side_tool_updated_at,
        "recent_jobs": recent_jobs,
        "recent_side_tool_runs": recent_side_tool_runs,
        "error": None,
    }


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report BRP SQLite runtime-store status. JSON counts are shown as an archive "
            "reference only; SQLite is the authoritative runtime store after migration."
        )
    )
    parser.add_argument("--env-file", type=Path, help="Optional BRP env file to resolve runtime paths.")
    parser.add_argument("--jobs-dir", type=Path, help="Legacy JSON jobs archive directory.")
    parser.add_argument("--side-tools-dir", type=Path, help="Legacy JSON side-tool archive directory.")
    parser.add_argument("--sqlite-path", type=Path, help="Authoritative SQLite runtime database path.")
    parser.add_argument("--recent-limit", type=int, default=5)
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--fail-empty", action="store_true", help="Return non-zero if SQLite has no runtime records.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file_values = parse_env_file(args.env_file)
    jobs_dir = resolve_path(
        args.jobs_dir,
        Path(env_value("BRP_BACKEND_JOBS_DIR", env_file_values) or ROOT_DIR / "state" / "jobs"),
    )
    side_tools_dir = resolve_path(
        args.side_tools_dir,
        Path(env_value("BRP_SIDE_TOOLS_DIR", env_file_values) or ROOT_DIR / "state" / "side_tools"),
    )
    sqlite_path = resolve_path(
        args.sqlite_path,
        Path(env_value("BRP_RUNTIME_DB_PATH", env_file_values) or ROOT_DIR / "state" / "brp_runtime.sqlite"),
    )

    status = sqlite_status(sqlite_path, max(0, args.recent_limit))
    json_archive_job_count = count_job_archive(jobs_dir)
    json_archive_side_tool_run_count = count_side_tool_archive(side_tools_dir)
    passed = bool(status["sqlite_exists"] and not status["error"])
    if args.fail_empty and status["job_count"] == 0 and status["side_tool_run_count"] == 0:
        passed = False

    summary: dict[str, Any] = {
        "passed": passed,
        "mode": "sqlite_authoritative",
        "sqlite_path": str(sqlite_path),
        "jobs_dir": str(jobs_dir),
        "side_tools_dir": str(side_tools_dir),
        "sqlite_exists": status["sqlite_exists"],
        "schema_version": status["schema_version"],
        "sqlite_job_count": status["job_count"],
        "sqlite_side_tool_run_count": status["side_tool_run_count"],
        "jobs_by_status": status["jobs_by_status"],
        "side_tool_runs_by_tool": status["side_tool_runs_by_tool"],
        "latest_job_updated_at": status["latest_job_updated_at"],
        "latest_side_tool_updated_at": status["latest_side_tool_updated_at"],
        "recent_jobs": status["recent_jobs"],
        "recent_side_tool_runs": status["recent_side_tool_runs"],
        "json_archive_job_count": json_archive_job_count,
        "json_archive_side_tool_run_count": json_archive_side_tool_run_count,
        "json_archive_note": (
            "JSON records are retained only as pre-switch archive/migration evidence; "
            "new runtime records are expected to be SQLite-only."
        ),
        "error": status["error"],
    }

    if args.print_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"passed={summary['passed']}")
        print(f"mode={summary['mode']}")
        print(f"sqlite_path={summary['sqlite_path']}")
        print(f"sqlite_exists={summary['sqlite_exists']} schema_version={summary['schema_version']}")
        print(
            f"sqlite_jobs={summary['sqlite_job_count']} "
            f"statuses={format_counts(summary['jobs_by_status'])}"
        )
        print(
            f"sqlite_side_tool_runs={summary['sqlite_side_tool_run_count']} "
            f"tools={format_counts(summary['side_tool_runs_by_tool'])}"
        )
        print(f"latest_job_updated_at={summary['latest_job_updated_at']}")
        print(f"latest_side_tool_updated_at={summary['latest_side_tool_updated_at']}")
        print(f"json_archive_jobs={summary['json_archive_job_count']}")
        print(f"json_archive_side_tool_runs={summary['json_archive_side_tool_run_count']}")
        print(f"json_archive_note={summary['json_archive_note']}")
        if summary["error"]:
            print(f"error={summary['error']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
