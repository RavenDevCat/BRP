from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
import threading
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RATE_LIMIT_DIR = REPO_ROOT / "state" / "api_rate_limits"


def _rate_limit_dir() -> Path:
    configured = os.environ.get("BRP_API_RATE_LIMIT_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_RATE_LIMIT_DIR


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip().lower()).strip("_") or "default"


@contextmanager
def cross_process_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


class CrossProcessRateLimiter:
    def __init__(self, name: str, max_qps: float) -> None:
        self.name = _safe_name(name)
        self.max_qps = max_qps
        self.min_interval_seconds = 1.0 / max_qps if max_qps > 0 else 0.0
        self.state_path = _rate_limit_dir() / f"{self.name}.json"

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return

        with cross_process_file_lock(self.state_path):
            state = _load_json(self.state_path)
            try:
                next_allowed_at = max(0.0, float(state.get("next_allowed_at", 0.0) or 0.0))
            except Exception:
                next_allowed_at = 0.0

            now = time.time()
            if now < next_allowed_at:
                time.sleep(next_allowed_at - now)
                now = time.time()

            state["name"] = self.name
            state["max_qps"] = self.max_qps
            state["next_allowed_at"] = max(now, next_allowed_at) + self.min_interval_seconds
            _save_json(self.state_path, state)
