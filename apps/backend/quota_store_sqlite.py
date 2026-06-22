from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_") or "default"


def default_quota_db_path() -> Path:
    configured = os.environ.get("BRP_QUOTA_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime_db = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    if runtime_db:
        return Path(runtime_db).expanduser()
    root = Path(__file__).resolve().parents[2]
    return root / "state" / "brp_runtime.sqlite"


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


class SqliteQuotaStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else default_quota_db_path()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
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
                CREATE TABLE IF NOT EXISTS rate_limits (
                    name TEXT PRIMARY KEY,
                    max_qps REAL NOT NULL,
                    next_allowed_at REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_counters (
                    provider TEXT NOT NULL,
                    counter TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    provider_label TEXT NOT NULL DEFAULT '',
                    sku_estimate TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, counter, period_type, period_key)
                );
                CREATE TABLE IF NOT EXISTS usage_counter_migrations (
                    source_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    migrated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO schema_migrations(name, version, updated_at)
                VALUES('quota_store', ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (SCHEMA_VERSION, utc_now_iso()),
            )

    @contextmanager
    def write_tx(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reserve_rate_limit(self, name: str, max_qps: float) -> float:
        if max_qps <= 0:
            return 0.0
        normalized = safe_name(name)
        min_interval = 1.0 / max(0.1, float(max_qps))
        now = time.time()
        with self.write_tx() as conn:
            row = conn.execute(
                "SELECT next_allowed_at FROM rate_limits WHERE name = ?",
                (normalized,),
            ).fetchone()
            next_allowed_at = float(row["next_allowed_at"]) if row else 0.0
            scheduled_at = max(now, next_allowed_at)
            conn.execute(
                """
                INSERT INTO rate_limits(name, max_qps, next_allowed_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    max_qps = excluded.max_qps,
                    next_allowed_at = excluded.next_allowed_at,
                    updated_at = excluded.updated_at
                """,
                (normalized, float(max_qps), scheduled_at + min_interval, utc_now_iso()),
            )
        wait_seconds = max(0.0, scheduled_at - now)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        return wait_seconds

    @staticmethod
    def _row_to_usage(row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {
                "attempted": 0,
                "used": 0,
                "succeeded": 0,
                "failed": 0,
                "updated_at": "",
                "provider": "",
                "sku_estimate": "",
            }
        attempted = int(row["attempted"] or 0)
        succeeded = int(row["succeeded"] or 0)
        failed = int(row["failed"] or 0)
        return {
            "attempted": attempted,
            "used": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "updated_at": row["updated_at"],
            "provider": row["provider_label"] or row["provider"],
            "sku_estimate": row["sku_estimate"],
        }

    def get_usage(self, provider: str, counter: str, period_type: str, period_key: str) -> dict[str, Any]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM usage_counters
                WHERE provider = ? AND counter = ? AND period_type = ? AND period_key = ?
                """,
                (safe_name(provider), safe_name(counter), safe_name(period_type), str(period_key)),
            ).fetchone()
        return self._row_to_usage(row)

    def sum_usage(self, *, period_type: str, period_key: str, contains: str = "") -> int:
        self.initialize()
        normalized_period_type = safe_name(period_type)
        params: list[Any] = [normalized_period_type, str(period_key)]
        where = "period_type = ? AND period_key = ?"
        if contains:
            raw_like = f"%{str(contains).strip().lower()}%"
            safe_like = f"%{safe_name(contains)}%"
            where += (
                " AND (provider LIKE ? OR counter LIKE ? OR lower(provider_label) LIKE ? OR lower(sku_estimate) LIKE ? "
                "OR provider LIKE ? OR counter LIKE ? OR lower(provider_label) LIKE ? OR lower(sku_estimate) LIKE ?)"
            )
            params.extend([safe_like, safe_like, safe_like, safe_like, raw_like, raw_like, raw_like, raw_like])
        with self.connect() as conn:
            row = conn.execute(f"SELECT COALESCE(SUM(attempted), 0) AS total FROM usage_counters WHERE {where}", params).fetchone()
        return int(row["total"] or 0) if row else 0

    def reserve_usage(
        self,
        provider: str,
        counter: str,
        periods: list[tuple[str, str, int]],
        *,
        count: int = 1,
        provider_label: str = "",
        sku_estimate: str = "",
    ) -> dict[str, Any]:
        if count <= 0:
            return {}
        normalized_provider = safe_name(provider)
        normalized_counter = safe_name(counter)
        normalized_periods = [(safe_name(kind), str(key), int(limit or 0)) for kind, key, limit in periods]
        now_iso = utc_now_iso()
        result: dict[str, Any] = {}
        with self.write_tx() as conn:
            current_rows: dict[tuple[str, str], sqlite3.Row | None] = {}
            for period_type, period_key, limit in normalized_periods:
                row = conn.execute(
                    """
                    SELECT * FROM usage_counters
                    WHERE provider = ? AND counter = ? AND period_type = ? AND period_key = ?
                    """,
                    (normalized_provider, normalized_counter, period_type, period_key),
                ).fetchone()
                attempted = int(row["attempted"] or 0) if row else 0
                if limit > 0 and attempted + count > limit:
                    raise RuntimeError(
                        f"{provider_label or provider} {period_type} usage cap would be exceeded: "
                        f"{attempted}+{count}>{limit}"
                    )
                current_rows[(period_type, period_key)] = row
            for period_type, period_key, limit in normalized_periods:
                row = current_rows[(period_type, period_key)]
                attempted = int(row["attempted"] or 0) if row else 0
                succeeded = int(row["succeeded"] or 0) if row else 0
                failed = int(row["failed"] or 0) if row else 0
                new_attempted = attempted + count
                conn.execute(
                    """
                    INSERT INTO usage_counters(
                        provider, counter, period_type, period_key, attempted,
                        succeeded, failed, provider_label, sku_estimate, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, counter, period_type, period_key) DO UPDATE SET
                        attempted = excluded.attempted,
                        succeeded = excluded.succeeded,
                        failed = excluded.failed,
                        provider_label = excluded.provider_label,
                        sku_estimate = excluded.sku_estimate,
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized_provider,
                        normalized_counter,
                        period_type,
                        period_key,
                        new_attempted,
                        succeeded,
                        failed,
                        provider_label or provider,
                        sku_estimate,
                        now_iso,
                    ),
                )
                result[period_type] = {
                    "period_key": period_key,
                    "attempted": new_attempted,
                    "used": new_attempted,
                    "limit": limit,
                }
        return result

    def mark_usage_result(
        self,
        provider: str,
        counter: str,
        periods: list[tuple[str, str]],
        *,
        succeeded: bool,
        provider_label: str = "",
        sku_estimate: str = "",
    ) -> None:
        normalized_provider = safe_name(provider)
        normalized_counter = safe_name(counter)
        result_column = "succeeded" if succeeded else "failed"
        now_iso = utc_now_iso()
        with self.write_tx() as conn:
            for period_type, period_key in [(safe_name(kind), str(key)) for kind, key in periods]:
                row = conn.execute(
                    """
                    SELECT * FROM usage_counters
                    WHERE provider = ? AND counter = ? AND period_type = ? AND period_key = ?
                    """,
                    (normalized_provider, normalized_counter, period_type, period_key),
                ).fetchone()
                attempted = int(row["attempted"] or 0) if row else 0
                succeeded_count = int(row["succeeded"] or 0) if row else 0
                failed_count = int(row["failed"] or 0) if row else 0
                if result_column == "succeeded":
                    succeeded_count += 1
                else:
                    failed_count += 1
                conn.execute(
                    """
                    INSERT INTO usage_counters(
                        provider, counter, period_type, period_key, attempted,
                        succeeded, failed, provider_label, sku_estimate, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, counter, period_type, period_key) DO UPDATE SET
                        attempted = excluded.attempted,
                        succeeded = excluded.succeeded,
                        failed = excluded.failed,
                        provider_label = excluded.provider_label,
                        sku_estimate = excluded.sku_estimate,
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized_provider,
                        normalized_counter,
                        period_type,
                        period_key,
                        attempted,
                        succeeded_count,
                        failed_count,
                        provider_label or provider,
                        sku_estimate,
                        now_iso,
                    ),
                )

    def migrate_flat_month_usage(
        self,
        *,
        source_path: Path,
        provider: str,
        counter: str,
        provider_label: str = "",
        sku_estimate: str = "",
    ) -> None:
        payload = load_json_object(source_path)
        if not payload:
            return
        source_key = f"flat-month:{source_path.expanduser()}:{safe_name(provider)}:{safe_name(counter)}"
        with self.write_tx() as conn:
            if conn.execute(
                "SELECT 1 FROM usage_counter_migrations WHERE source_key = ?",
                (source_key,),
            ).fetchone():
                return
            for month_key, value in payload.items():
                if not re.fullmatch(r"\d{4}-\d{2}", str(month_key)):
                    continue
                try:
                    attempted = max(0, int(value or 0))
                except Exception:
                    continue
                if attempted <= 0:
                    continue
                self._upsert_usage_inside_tx(
                    conn,
                    provider=provider,
                    counter=counter,
                    period_type="month",
                    period_key=str(month_key),
                    attempted=attempted,
                    succeeded=0,
                    failed=0,
                    provider_label=provider_label or provider,
                    sku_estimate=sku_estimate,
                )
            conn.execute(
                """
                INSERT INTO usage_counter_migrations(source_key, source_path, migrated_at)
                VALUES(?, ?, ?)
                """,
                (source_key, str(source_path.expanduser()), utc_now_iso()),
            )

    def migrate_bucket_usage(
        self,
        *,
        source_path: Path,
        provider: str,
        counter: str,
        provider_label: str = "",
        sku_estimate: str = "",
    ) -> None:
        payload = load_json_object(source_path)
        if not payload:
            return
        source_key = f"bucket:{source_path.expanduser()}:{safe_name(provider)}:{safe_name(counter)}"
        with self.write_tx() as conn:
            if conn.execute(
                "SELECT 1 FROM usage_counter_migrations WHERE source_key = ?",
                (source_key,),
            ).fetchone():
                return
            for period_key, value in payload.items():
                if not isinstance(value, dict):
                    continue
                if re.fullmatch(r"\d{4}-\d{2}", str(period_key)):
                    period_type = "month"
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(period_key)):
                    period_type = "day"
                else:
                    continue
                attempted = int(value.get("attempted", value.get("used", 0)) or 0)
                succeeded = int(value.get("succeeded", 0) or 0)
                failed = int(value.get("failed", 0) or 0)
                self._upsert_usage_inside_tx(
                    conn,
                    provider=provider,
                    counter=counter,
                    period_type=period_type,
                    period_key=str(period_key),
                    attempted=max(0, attempted),
                    succeeded=max(0, succeeded),
                    failed=max(0, failed),
                    provider_label=str(value.get("provider") or provider_label or provider),
                    sku_estimate=str(value.get("sku_estimate") or sku_estimate or ""),
                )
            conn.execute(
                """
                INSERT INTO usage_counter_migrations(source_key, source_path, migrated_at)
                VALUES(?, ?, ?)
                """,
                (source_key, str(source_path.expanduser()), utc_now_iso()),
            )

    def migrate_nested_period_usage(
        self,
        *,
        source_path: Path,
        provider: str,
        counter: str,
        day_key: str = "days",
        month_key: str = "months",
        provider_label: str = "",
        sku_estimate: str = "",
    ) -> None:
        payload = load_json_object(source_path)
        if not payload:
            return
        source_key = (
            f"nested-period:{source_path.expanduser()}:{safe_name(provider)}:"
            f"{safe_name(counter)}:{safe_name(day_key)}:{safe_name(month_key)}"
        )
        with self.write_tx() as conn:
            if conn.execute(
                "SELECT 1 FROM usage_counter_migrations WHERE source_key = ?",
                (source_key,),
            ).fetchone():
                return
            for period_type, container_key in (("day", day_key), ("month", month_key)):
                container = payload.get(container_key)
                if not isinstance(container, dict):
                    continue
                for period_key, value in container.items():
                    try:
                        attempted = max(0, int(value or 0))
                    except Exception:
                        continue
                    if attempted <= 0:
                        continue
                    self._upsert_usage_inside_tx(
                        conn,
                        provider=provider,
                        counter=counter,
                        period_type=period_type,
                        period_key=str(period_key),
                        attempted=attempted,
                        succeeded=0,
                        failed=0,
                        provider_label=provider_label or provider,
                        sku_estimate=sku_estimate,
                    )
            conn.execute(
                """
                INSERT INTO usage_counter_migrations(source_key, source_path, migrated_at)
                VALUES(?, ?, ?)
                """,
                (source_key, str(source_path.expanduser()), utc_now_iso()),
            )

    @staticmethod
    def _upsert_usage_inside_tx(
        conn: sqlite3.Connection,
        *,
        provider: str,
        counter: str,
        period_type: str,
        period_key: str,
        attempted: int,
        succeeded: int,
        failed: int,
        provider_label: str,
        sku_estimate: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO usage_counters(
                provider, counter, period_type, period_key, attempted,
                succeeded, failed, provider_label, sku_estimate, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, counter, period_type, period_key) DO UPDATE SET
                attempted = MAX(usage_counters.attempted, excluded.attempted),
                succeeded = MAX(usage_counters.succeeded, excluded.succeeded),
                failed = MAX(usage_counters.failed, excluded.failed),
                provider_label = excluded.provider_label,
                sku_estimate = excluded.sku_estimate,
                updated_at = excluded.updated_at
            """,
            (
                safe_name(provider),
                safe_name(counter),
                safe_name(period_type),
                str(period_key),
                int(attempted),
                int(succeeded),
                int(failed),
                provider_label,
                sku_estimate,
                utc_now_iso(),
            ),
        )
