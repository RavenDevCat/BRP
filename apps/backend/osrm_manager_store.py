from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
from uuid import uuid4


SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


class OsrmManagerStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).expanduser()

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
                CREATE TABLE IF NOT EXISTS osrm_manager_state (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS osrm_manager_locks (
                    name TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS osrm_manager_use_leases (
                    region TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    path TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(region, owner)
                );
                """
            )
            conn.execute(
                """
                INSERT INTO schema_migrations(name, version, updated_at)
                VALUES('osrm_manager_store', ?, ?)
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

    def load_state(self, legacy_state_path: Path) -> dict[str, Any]:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM osrm_manager_state WHERE key = 'state'"
            ).fetchone()
        if row:
            try:
                payload = json.loads(str(row["payload"]))
                if isinstance(payload, dict) and isinstance(payload.get("regions"), dict):
                    return payload
            except Exception:
                pass
        legacy_payload = _load_json_object(legacy_state_path)
        if isinstance(legacy_payload.get("regions"), dict):
            self.save_state(legacy_payload)
            return legacy_payload
        return {"regions": {}}

    def save_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        with self.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO osrm_manager_state(key, payload, updated_at)
                VALUES('state', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (payload, utc_now_iso()),
            )

    @contextmanager
    def acquire_lock(
        self,
        *,
        name: str,
        path: Path,
        wait_seconds: float,
        ttl_seconds: float,
    ) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        owner = uuid4().hex
        acquired = False
        deadline = None if wait_seconds < 0 else time.monotonic() + wait_seconds
        while True:
            now = time.time()
            with self.write_tx() as conn:
                row = conn.execute(
                    "SELECT owner, expires_at FROM osrm_manager_locks WHERE name = ?",
                    (name,),
                ).fetchone()
                if not row or float(row["expires_at"] or 0) <= now:
                    conn.execute(
                        """
                        INSERT INTO osrm_manager_locks(name, path, owner, acquired_at, expires_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            path = excluded.path,
                            owner = excluded.owner,
                            acquired_at = excluded.acquired_at,
                            expires_at = excluded.expires_at,
                            updated_at = excluded.updated_at
                        """,
                        (name, str(path), owner, now, now + max(1.0, ttl_seconds), utc_now_iso()),
                    )
                    acquired = True
            if acquired:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(name)
            sleep_for = 0.25 if deadline is None else min(0.25, max(0.01, deadline - time.monotonic()))
            time.sleep(sleep_for)
        try:
            yield
        finally:
            self.release_lock(name, owner)

    def release_lock(self, name: str, owner: str) -> None:
        with self.write_tx() as conn:
            conn.execute(
                "DELETE FROM osrm_manager_locks WHERE name = ? AND owner = ?",
                (name, owner),
            )

    def is_lock_held(self, name: str) -> bool:
        self.initialize()
        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM osrm_manager_locks WHERE name = ?",
                (name,),
            ).fetchone()
        return bool(row and float(row["expires_at"] or 0) > now)

    def lock_status(self, lock_dir: Path, stale_ttl_seconds: float) -> list[dict[str, Any]]:
        self.initialize()
        now = time.time()
        rows: list[sqlite3.Row]
        with self.connect() as conn:
            rows = list(conn.execute("SELECT * FROM osrm_manager_locks ORDER BY name"))
        statuses: list[dict[str, Any]] = []
        names = set()
        for row in rows:
            name = str(row["name"])
            names.add(name)
            acquired_at = float(row["acquired_at"] or now)
            expires_at = float(row["expires_at"] or 0)
            locked = expires_at > now
            statuses.append(
                {
                    "path": str(row["path"]),
                    "name": name,
                    "present": True,
                    "locked": locked,
                    "age_s": max(0.0, now - acquired_at),
                    "stale": bool(not locked),
                    "store": "sqlite",
                }
            )
        if lock_dir.exists():
            for path in sorted(lock_dir.glob("*.lock")):
                if not path.is_file() or path.name in names:
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                age_s = max(0.0, now - float(stat.st_mtime))
                statuses.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "present": True,
                        "locked": False,
                        "age_s": age_s,
                        "stale": bool(stale_ttl_seconds > 0 and age_s >= stale_ttl_seconds),
                        "store": "legacy_file",
                    }
                )
        return sorted(statuses, key=lambda item: str(item.get("name") or ""))

    def cleanup_stale_locks(self, lock_dir: Path, stale_ttl_seconds: float) -> list[str]:
        removed: list[str] = []
        now = time.time()
        with self.write_tx() as conn:
            rows = list(
                conn.execute(
                    "SELECT name, path FROM osrm_manager_locks WHERE expires_at <= ? ORDER BY name",
                    (now,),
                )
            )
            for row in rows:
                removed.append(str(row["path"]))
            conn.execute("DELETE FROM osrm_manager_locks WHERE expires_at <= ?", (now,))
        if stale_ttl_seconds > 0 and lock_dir.exists():
            for path in sorted(lock_dir.glob("*.lock")):
                try:
                    age_s = max(0.0, now - float(path.stat().st_mtime))
                except FileNotFoundError:
                    continue
                if age_s < stale_ttl_seconds:
                    continue
                try:
                    path.unlink(missing_ok=True)
                    removed.append(str(path))
                except FileNotFoundError:
                    continue
        return removed

    @contextmanager
    def use_lease(self, *, region: str, path: Path, ttl_seconds: float) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        owner = uuid4().hex
        now = time.time()
        with self.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO osrm_manager_use_leases(region, owner, path, acquired_at, expires_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (region, owner, str(path), now, now + max(1.0, ttl_seconds), utc_now_iso()),
            )
        try:
            yield
        finally:
            with self.write_tx() as conn:
                conn.execute(
                    "DELETE FROM osrm_manager_use_leases WHERE region = ? AND owner = ?",
                    (region, owner),
                )

    def use_is_active(self, region: str) -> bool:
        self.cleanup_expired_use_leases()
        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM osrm_manager_use_leases WHERE region = ? AND expires_at > ? LIMIT 1",
                (region, now),
            ).fetchone()
        return bool(row)

    def active_use_regions(self, regions: list[str]) -> list[str]:
        return sorted(region for region in regions if self.use_is_active(region))

    def cleanup_expired_use_leases(self) -> None:
        now = time.time()
        with self.write_tx() as conn:
            conn.execute("DELETE FROM osrm_manager_use_leases WHERE expires_at <= ?", (now,))
