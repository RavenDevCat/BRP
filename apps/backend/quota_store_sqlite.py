"""Compatibility import for standalone client and backend launchers."""
from pathlib import Path
import sys

_apps = str(Path(__file__).resolve().parents[1])
if _apps not in sys.path:
    sys.path.insert(0, _apps)
from brp_storage.quota_store_sqlite import SCHEMA_VERSION, SqliteQuotaStore, default_quota_db_path, load_json_object, safe_name, utc_now_iso  # noqa: E402,F401
