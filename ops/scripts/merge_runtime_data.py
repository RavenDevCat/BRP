#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def copy_if_missing(source: Path, target: Path) -> None:
    if target.exists() or not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def merge_json_payload(target_path: Path, source_path: Path) -> None:
    source_payload = load_json(source_path)
    if source_payload is None:
        copy_if_missing(source_path, target_path)
        return

    target_payload = load_json(target_path) if target_path.exists() else None
    if target_payload is None:
        write_json(target_path, source_payload)
        return

    if isinstance(target_payload, dict) and isinstance(source_payload, dict):
        merged = deepcopy(target_payload)
        if target_path.name == "google_geocode_usage.json":
            for key, value in source_payload.items():
                try:
                    merged[key] = max(int(merged.get(key, 0) or 0), int(value or 0))
                except Exception:
                    merged[key] = value
        else:
            merged.update(source_payload)
        write_json(target_path, merged)
        return

    if isinstance(target_payload, list) and isinstance(source_payload, list):
        seen: set[str] = set()
        merged_list: list[Any] = []
        for item in target_payload + source_payload:
            if isinstance(item, dict):
                key = str(item.get("job_id") or item.get("run_id") or item.get("id") or json.dumps(item, sort_keys=True))
            else:
                key = json.dumps(item, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged_list.append(item)
        merged_list.sort(
            key=lambda item: str(item.get("created_at") if isinstance(item, dict) else ""),
            reverse=True,
        )
        write_json(target_path, merged_list[:200])
        return

    copy_if_missing(source_path, target_path)


def merge_cache_dir(target_dir: Path, source_dir: Path) -> None:
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.iterdir():
        target_path = target_dir / source_path.name
        if source_path.is_dir():
            merge_cache_dir(target_path, source_path)
        elif source_path.suffix.lower() == ".json":
            merge_json_payload(target_path, source_path)
        else:
            copy_if_missing(source_path, target_path)


def job_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(record.get("job_id", "")).strip(),
        "owner_email": normalize_email(record.get("owner_email")),
        "status": str(record.get("status", "queued")),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "metadata": deepcopy(record.get("metadata") or {}),
        "prepared_payload_summary": deepcopy(record.get("prepared_payload_summary") or {}),
        "error": record.get("error"),
    }


def rebuild_job_index(jobs_dir: Path) -> None:
    records: list[dict[str, Any]] = []
    for job_path in jobs_dir.glob("*.json"):
        if job_path.name == "index.json":
            continue
        payload = load_json(job_path)
        if isinstance(payload, dict) and str(payload.get("job_id") or "").strip():
            records.append(payload)
    records.sort(
        key=lambda item: str(item.get("created_at") or item.get("started_at") or item.get("finished_at") or ""),
        reverse=True,
    )
    write_json(jobs_dir / "index.json", [job_summary(record) for record in records])


def merge_jobs_dir(target_dir: Path, source_dir: Path) -> None:
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.glob("*.json"):
        if source_path.name == "index.json":
            continue
        copy_if_missing(source_path, target_dir / source_path.name)
    rebuild_job_index(target_dir)


def side_tool_summary(tool_key: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(record.get("run_id") or ""),
        "tool_key": tool_key,
        "owner_email": normalize_email(record.get("owner_email")),
        "title": str(record.get("title") or ""),
        "created_at": record.get("created_at"),
        "summary": deepcopy(record.get("summary") or {}),
    }


def rebuild_side_tool_index(tool_dir: Path, tool_key: str) -> None:
    records: list[dict[str, Any]] = []
    for record_path in tool_dir.glob("*.json"):
        if record_path.name == "index.json":
            continue
        payload = load_json(record_path)
        if isinstance(payload, dict) and str(payload.get("run_id") or "").strip():
            records.append(payload)
    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    write_json(tool_dir / "index.json", [side_tool_summary(tool_key, record) for record in records[:100]])


def merge_side_tools_dir(target_dir: Path, source_dir: Path) -> None:
    if not source_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_tool_dir in source_dir.iterdir():
        if not source_tool_dir.is_dir():
            continue
        target_tool_dir = target_dir / source_tool_dir.name
        target_tool_dir.mkdir(parents=True, exist_ok=True)
        for source_path in source_tool_dir.glob("*.json"):
            if source_path.name == "index.json":
                continue
            copy_if_missing(source_path, target_tool_dir / source_path.name)
        rebuild_side_tool_index(target_tool_dir, source_tool_dir.name)


def existing_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    return path if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge BRP runtime data directories without deleting source data.")
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--side-tools-dir", required=True)
    parser.add_argument("--client-cache-dir", required=True)
    parser.add_argument("--backend-cache-dir", required=True)
    parser.add_argument("--source-jobs", action="append", default=[])
    parser.add_argument("--source-side-tools", action="append", default=[])
    parser.add_argument("--source-client-cache", action="append", default=[])
    parser.add_argument("--source-backend-cache", action="append", default=[])
    args = parser.parse_args()

    jobs_dir = Path(args.jobs_dir).expanduser()
    side_tools_dir = Path(args.side_tools_dir).expanduser()
    client_cache_dir = Path(args.client_cache_dir).expanduser()
    backend_cache_dir = Path(args.backend_cache_dir).expanduser()

    for source in [existing_path(path) for path in args.source_jobs]:
        if source is not None:
            merge_jobs_dir(jobs_dir, source)
    rebuild_job_index(jobs_dir)

    for source in [existing_path(path) for path in args.source_side_tools]:
        if source is not None:
            merge_side_tools_dir(side_tools_dir, source)

    for source in [existing_path(path) for path in args.source_client_cache]:
        if source is not None:
            merge_cache_dir(client_cache_dir, source)

    for source in [existing_path(path) for path in args.source_backend_cache]:
        if source is not None:
            merge_cache_dir(backend_cache_dir, source)

    print(f"jobs={sum(1 for path in jobs_dir.glob('*.json') if path.name != 'index.json')}")
    print(f"side_tools={sum(1 for path in side_tools_dir.glob('*/*.json') if path.name != 'index.json')}")
    print(f"client_cache={sum(1 for path in client_cache_dir.rglob('*') if path.is_file())}")
    print(f"backend_cache={sum(1 for path in backend_cache_dir.rglob('*') if path.is_file())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
