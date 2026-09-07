from __future__ import annotations

import os
import sys

try:
    from .measurement_reviews import execute_saved_review
    from .runtime_store_sqlite import SqliteRuntimeStore
except ImportError:
    from measurement_reviews import execute_saved_review
    from runtime_store_sqlite import SqliteRuntimeStore


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: measurement_review_runner.py <review_id>")
    token = os.environ.get("BRP_MEASUREMENT_REVIEW_TOKEN", "")
    db_path = os.environ.get("BRP_RUNTIME_DB_PATH", "")
    if not token or not db_path:
        raise SystemExit("A claimed review and explicit runtime store are required.")
    row = execute_saved_review(SqliteRuntimeStore(db_path), sys.argv[1], token, preclaimed=True)
    # The parent reaper or dead-worker reconciliation releases capacity after process exit.
    return 0 if row and row["status"] in {"succeeded", "needs_review", "canceled", "pausing", "yielding", "paused"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
