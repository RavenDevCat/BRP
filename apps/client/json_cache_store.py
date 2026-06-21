from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any


_CACHE_FILE_LOCKS: dict[str, threading.Lock] = {}


def _cache_lock(path: Path) -> threading.Lock:
    key = str(path.expanduser().resolve())
    lock = _CACHE_FILE_LOCKS.get(key)
    if lock is None:
        lock = threading.Lock()
        _CACHE_FILE_LOCKS[key] = lock
    return lock


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


def load_json_object(path: Path | str) -> dict[str, Any]:
    cache_path = Path(path).expanduser()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_json_object(
    path: Path | str,
    payload: dict[str, Any],
    *,
    sort_keys: bool = False,
    indent: int | None = 2,
) -> None:
    cache_path = Path(path).expanduser()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        json_safe(payload),
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        allow_nan=False,
    )
    temp_path = cache_path.with_name(
        f"{cache_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    lock = _cache_lock(cache_path)
    with lock:
        try:
            temp_path.write_text(body, encoding="utf-8")
            temp_path.replace(cache_path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def clear_json_object(path: Path | str) -> None:
    save_json_object(path, {})


class JsonFileCache:
    def __init__(self, path: Path | str, *, sort_keys: bool = False) -> None:
        self.path = Path(path).expanduser()
        self.sort_keys = sort_keys

    def load(self) -> dict[str, Any]:
        return load_json_object(self.path)

    def save(self, payload: dict[str, Any]) -> None:
        save_json_object(self.path, payload, sort_keys=self.sort_keys)

    def clear(self) -> None:
        clear_json_object(self.path)
