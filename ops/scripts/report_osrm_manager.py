from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "apps" / "backend"
DEFAULT_ENV_FILE = ROOT_DIR / "ops" / "env" / "local.env"


def _preparse_env_file(argv: list[str]) -> Path | None:
    for index, item in enumerate(argv):
        if item == "--env-file" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser()
        if item.startswith("--env-file="):
            return Path(item.split("=", 1)[1]).expanduser()
    return DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.exists() else None


def _load_env_file(path: Path | None) -> None:
    if not path or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_preparse_env_file(sys.argv[1:]))
sys.path.insert(0, str(BACKEND_DIR))

import osrm_manager  # noqa: E402


ACTIVE_WORKER_PATTERNS = (
    "backend_job_runner.py",
    "live_traffic_sampler.py",
)
ACTIVE_WRAPPER_PREFIX = "run_live_traffic_"
SHELL_NAMES = {"bash", "dash", "sh", "zsh"}


def _basename(token: str) -> str:
    return Path(token).name


def _is_active_worker_process(comm: str, args: str) -> bool:
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if not tokens:
        return False

    command_name = _basename(comm or tokens[0])
    basenames = [_basename(token) for token in tokens]

    if command_name.startswith("python"):
        return any(name in ACTIVE_WORKER_PATTERNS for name in basenames)

    if command_name in SHELL_NAMES:
        # Real wrapper processes are invoked as `bash /path/run_live_traffic_*.sh ...`.
        # Ignore `bash -c "rg ... run_live_traffic_..."` diagnostics that merely
        # mention wrapper names in a command string.
        for token in tokens[1:]:
            if token.startswith("-"):
                if token == "-c":
                    return False
                continue
            return _basename(token).startswith(ACTIVE_WRAPPER_PREFIX)
        return False

    return command_name.startswith(ACTIVE_WRAPPER_PREFIX)


def _active_worker_processes() -> list[str]:
    if os.name == "nt":
        return []
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    rows = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or "report_osrm_manager.py" in stripped:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        _pid, comm, args = parts
        if _is_active_worker_process(comm, args):
            rows.append(stripped)
    return rows


def _print_status(report: dict[str, object]) -> None:
    print(f"state_path={report.get('state_path')}")
    print(f"on_demand_enabled={report.get('on_demand_enabled')}")
    print(f"idle_ttl_seconds={report.get('idle_ttl_seconds')}")
    available = report.get("available_memory_mb")
    if isinstance(available, (int, float)):
        print(f"available_memory_mb={available:.0f}")
    else:
        print("available_memory_mb=unknown")
    print("region          port dataset managed state          container      idle_expired last_seen_age_s")
    for row in report.get("regions", []):
        if not isinstance(row, dict):
            continue
        container_status = row.get("container_status")
        if isinstance(container_status, dict):
            container = container_status.get("status") or ("missing" if not container_status.get("present") else "unknown")
        else:
            container = "unknown"
        age = row.get("last_seen_age_s")
        age_label = f"{age:.0f}" if isinstance(age, (int, float)) else "-"
        print(
            f"{str(row.get('region', '')):<15} "
            f"{str(row.get('port', '')):>4} "
            f"{'yes' if row.get('dataset_exists') else 'no ':>7} "
            f"{'yes' if row.get('managed') else 'no ':>7} "
            f"{str(row.get('state_status') or '-'):<14} "
            f"{str(container):<14} "
            f"{'yes' if row.get('idle_expired') else 'no ':>12} "
            f"{age_label:>15}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report or clean BRP OSRM manager state.")
    parser.add_argument("--env-file", type=Path, default=_preparse_env_file(sys.argv[1:]) or DEFAULT_ENV_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="Print OSRM manager state without starting OSRM.")
    status.add_argument("--json", action="store_true")
    cleanup = subparsers.add_parser("cleanup-idle", help="Stop manager-owned idle OSRM containers past TTL.")
    cleanup.add_argument("--json", action="store_true")
    cleanup.add_argument("--force", action="store_true", help="Allow cleanup even if BRP worker processes are active.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "status":
        report = osrm_manager.manager_status()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_status(report)
        return

    if args.command == "cleanup-idle":
        active = _active_worker_processes()
        if active and not args.force:
            result = {
                "status": "refused_active_workers",
                "active_workers": active,
                "stopped_regions": [],
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("Refusing cleanup because BRP worker processes are active:")
                for line in active:
                    print(f"  {line}")
                print("Use --force only during a verified maintenance window.")
            raise SystemExit(2)
        stopped = osrm_manager.cleanup_idle_regions()
        result = {
            "status": "ok",
            "active_workers": active,
            "stopped_regions": stopped,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"stopped_regions={','.join(stopped) if stopped else '-'}")
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
