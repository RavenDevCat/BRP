"""Compatibility import for standalone client and backend launchers."""
from pathlib import Path
import sys

_apps = str(Path(__file__).resolve().parents[1])
if _apps not in sys.path:
    sys.path.insert(0, _apps)
from brp_storage.json_cache_store import JsonFileCache, clear_json_object, json_safe, load_json_object, save_json_object  # noqa: E402,F401
