from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from json_cache_store import clear_json_object, load_json_object, save_json_object  # noqa: E402


def test_json_cache_store_loads_only_json_objects(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_json_object(cache_path) == {}

    cache_path.write_text("{broken", encoding="utf-8")
    assert load_json_object(cache_path) == {}


def test_json_cache_store_writes_atomically_and_cleans_temp_files(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"

    save_json_object(cache_path, {"b": 1, "a": math.nan}, sort_keys=True)

    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"a": None, "b": 1}
    assert load_json_object(cache_path) == {"a": None, "b": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_json_cache_store_clear_writes_empty_object(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"

    save_json_object(cache_path, {"cached": True})
    clear_json_object(cache_path)

    assert load_json_object(cache_path) == {}
