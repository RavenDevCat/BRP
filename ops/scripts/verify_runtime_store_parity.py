#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "apps" / "backend"))

from runtime_store_sqlite import RuntimeJsonPaths, verify_json_sqlite_parity  # noqa: E402


def default_jobs_dir() -> Path:
    configured = os.environ.get("BRP_BACKEND_JOBS_DIR", "").strip()
    return Path(configured).expanduser() if configured else ROOT_DIR / "state" / "jobs"


def default_side_tools_dir() -> Path:
    configured = os.environ.get("BRP_SIDE_TOOLS_DIR", "").strip()
    return Path(configured).expanduser() if configured else ROOT_DIR / "state" / "side_tools"


def default_sqlite_path() -> Path:
    configured = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    return Path(configured).expanduser() if configured else ROOT_DIR / "state" / "brp_runtime.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify JSON runtime records and SQLite sidecar parity.")
    parser.add_argument("--jobs-dir", type=Path, default=default_jobs_dir())
    parser.add_argument("--side-tools-dir", type=Path, default=default_side_tools_dir())
    parser.add_argument("--sqlite-path", type=Path, default=default_sqlite_path())
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = RuntimeJsonPaths(args.jobs_dir.expanduser(), args.side_tools_dir.expanduser())
    summary = verify_json_sqlite_parity(paths, args.sqlite_path.expanduser())
    summary.update({
        "sqlite_path": str(args.sqlite_path.expanduser()),
        "jobs_dir": str(paths.jobs_dir),
        "side_tools_dir": str(paths.side_tools_dir),
    })
    if args.print_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"passed={summary['passed']}")
        print(f"json_jobs={summary['json_job_count']} sqlite_jobs={summary['sqlite_job_count']}")
        print(
            "json_side_tool_runs="
            f"{summary['json_side_tool_run_count']} sqlite_side_tool_runs={summary['sqlite_side_tool_run_count']}"
        )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
