#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "apps" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import backend_service  # noqa: E402


def main() -> int:
    released = backend_service.JOB_STORE.release_due_scheduled_jobs()
    backend_service._schedule_queued_jobs()
    if released:
        print("released scheduled audit jobs:", ", ".join(str(job.get("job_id")) for job in released))
    else:
        print("released scheduled audit jobs: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
