#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil


def parse_assignment(raw_value: str) -> tuple[str, str]:
    if "=" not in raw_value:
        raise argparse.ArgumentTypeError(f"Expected KEY=VALUE, got {raw_value!r}")
    key, value = raw_value.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("Environment key cannot be empty.")
    return key, value


def update_env_file(path: Path, assignments: list[tuple[str, str]], backup_suffix: str) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    backup_path = path.with_name(f"{path.name}.{backup_suffix}")
    shutil.copy2(path, backup_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    replacements = dict(assignments)
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in replacements:
            updated_lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            updated_lines.append(line)

    missing = [(key, value) for key, value in assignments if key not in seen]
    if missing and updated_lines and updated_lines[-1].strip():
        updated_lines.append("")
    for key, value in missing:
        updated_lines.append(f"{key}={value}")

    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    print(f"updated {path} backup={backup_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set key=value pairs in one or more BRP local.env files.")
    parser.add_argument("--file", action="append", required=True, help="Env file to update. Can be provided multiple times.")
    parser.add_argument("--set", dest="assignments", action="append", type=parse_assignment, required=True)
    parser.add_argument("--backup-suffix", default=f"bak-env-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    args = parser.parse_args()

    for raw_path in args.file:
        update_env_file(Path(raw_path).expanduser(), args.assignments, args.backup_suffix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
