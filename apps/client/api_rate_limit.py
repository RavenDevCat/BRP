"""Compatibility import for standalone client and backend launchers."""
from pathlib import Path
import sys

_apps = str(Path(__file__).resolve().parents[1])
if _apps not in sys.path:
    sys.path.insert(0, _apps)
from brp_storage.api_rate_limit import CrossProcessRateLimiter  # noqa: E402,F401
