#!/usr/bin/env python3
"""Report whether a BRP environment matches its expected runtime shape.

This script is read-only. It does not call provider APIs, start services, stop
processes, or edit runtime state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EnvironmentSpec:
    name: str
    backend_port: int
    frontend_port: int
    expected_markets: tuple[str, ...]
    deployment_tier: str
    platform_kind: str
    require_kr_tasks: bool = False
    require_kr_relay_redirect: bool = False


ENVIRONMENT_SPECS: dict[str, EnvironmentSpec] = {
    "cn-staging": EnvironmentSpec(
        name="cn-staging",
        backend_port=8001,
        frontend_port=8501,
        expected_markets=("BK", "CN", "KR"),
        deployment_tier="staging",
        platform_kind="linux",
    ),
    "cn-prod": EnvironmentSpec(
        name="cn-prod",
        backend_port=8000,
        frontend_port=8500,
        expected_markets=("BK", "CN"),
        deployment_tier="production",
        platform_kind="linux",
        require_kr_relay_redirect=True,
    ),
    "kr-prod": EnvironmentSpec(
        name="kr-prod",
        backend_port=8001,
        frontend_port=8501,
        expected_markets=("KR",),
        deployment_tier="production",
        platform_kind="windows",
        require_kr_tasks=True,
    ),
}

ENV_KEYS_FOR_TIER = (
    "BRP_DEPLOYMENT_TIER",
    "BRP_DEPLOYMENT_ENV",
    "BRP_ENV",
    "APP_ENV",
    "ENVIRONMENT",
)
LEGACY_PROXY_FILES = (
    "ops/scripts/serve_react_static.py",
    "ops/scripts/run_react_static.ps1",
)
LEGACY_KR_TASKS = ("BRP-React-Public", "BRP-React-Preview")


def parse_env_file(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = path.expanduser()
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def env_value(key: str, env_values: dict[str, str]) -> str:
    return os.environ.get(key) or env_values.get(key, "")


def normalize_market(value: str) -> str:
    return value.strip().upper()


def parse_market_scope(value: str) -> tuple[str, ...]:
    markets = sorted({normalize_market(item) for item in value.split(",") if item.strip()})
    return tuple(markets)


def infer_environment(root_dir: Path = ROOT_DIR) -> str:
    if os.name == "nt":
        return "kr-prod"
    parts = {part.casefold() for part in root_dir.resolve().parts}
    if "staging" in parts:
        return "cn-staging"
    if "prod" in parts or "production" in parts:
        return "cn-prod"
    return "cn-staging"


def normalize_tier(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"prod", "production"}:
        return "production"
    if normalized in {"stage", "staging"}:
        return "staging"
    return normalized


def configured_deployment_tier(env_values: dict[str, str], root_dir: Path, spec: EnvironmentSpec) -> str:
    for key in ENV_KEYS_FOR_TIER:
        value = env_value(key, env_values)
        if value.strip():
            return normalize_tier(value)
    if spec.name == "kr-prod":
        return "production"
    parts = {part.casefold() for part in root_dir.resolve().parts}
    if "prod" in parts or "production" in parts:
        return "production"
    if "staging" in parts:
        return "staging"
    return spec.deployment_tier


def expected_market_scope(env_values: dict[str, str], spec: EnvironmentSpec) -> tuple[tuple[str, ...], str]:
    configured = env_value("BRP_TRAFFIC_STATUS_MARKETS", env_values).strip()
    if configured:
        return parse_market_scope(configured), "BRP_TRAFFIC_STATUS_MARKETS"
    return tuple(sorted(spec.expected_markets)), "implicit"


def run_command(args: list[str], *, cwd: Path | None = None, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def git_head(root_dir: Path) -> tuple[str | None, str | None]:
    result = run_command(["git", "rev-parse", "--short", "HEAD"], cwd=root_dir)
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()
    return result.stdout.strip(), None


def git_origin_head(root_dir: Path) -> tuple[str | None, str | None]:
    result = run_command(["git", "rev-parse", "--short", "origin/main"], cwd=root_dir)
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()
    return result.stdout.strip(), None


def dist_contains_marker(root_dir: Path, marker: str | None) -> tuple[bool, str]:
    if not marker:
        return False, "missing git head"
    assets_dir = root_dir / "apps" / "web" / "dist" / "assets"
    if not assets_dir.exists():
        return False, f"missing {assets_dir}"
    candidates = sorted(assets_dir.glob("index-*.js"))
    if not candidates:
        candidates = sorted(assets_dir.glob("*.js"))
    for path in candidates:
        try:
            if marker in path.read_text(encoding="utf-8", errors="ignore"):
                return True, str(path.relative_to(root_dir))
        except OSError as exc:
            return False, f"{path}: {exc}"
    return False, f"marker {marker} not found in current dist assets"


def connectable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "brp-env-parity/1.0"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(512)
            return {
                "ok": True,
                "status_code": response.status,
                "content_type": response.headers.get("content-type", ""),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "body": body.decode("utf-8", errors="ignore"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(512)
        return {
            "ok": False,
            "status_code": exc.code,
            "content_type": exc.headers.get("content-type", ""),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "body": body.decode("utf-8", errors="ignore"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "content_type": "",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def backend_health(port: int) -> dict[str, Any]:
    probe = http_probe(f"http://127.0.0.1:{port}/health")
    body = str(probe.get("body") or "")
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}
    probe["payload"] = payload
    probe["healthy"] = probe.get("status_code") == 200 and payload.get("status") == "ok"
    return probe


def frontend_origin_status(port: int) -> dict[str, Any]:
    probe = http_probe(f"http://127.0.0.1:{port}/")
    probe["alive"] = probe.get("status_code") in {200, 401}
    return probe


def windows_task_states(task_names: tuple[str, ...]) -> dict[str, str]:
    if os.name != "nt":
        return {}
    joined = ",".join(f"'{name}'" for name in task_names)
    script = (
        f"$names=@({joined}); "
        "foreach ($n in $names) { "
        "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; "
        "if ($t) { Write-Output ($n + '=' + $t.State) } "
        "else { Write-Output ($n + '=ABSENT') } "
        "}"
    )
    result = run_command(["powershell", "-NoProfile", "-Command", script], timeout=10.0)
    states: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        states[key.strip()] = value.strip()
    if result.returncode != 0:
        states["_error"] = result.stderr.strip() or result.stdout.strip()
    return states


def read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    except OSError:
        return ""


def cn_nginx_kr_relay_status() -> dict[str, Any]:
    relay_link = Path("/etc/nginx/sites-enabled/brp-kr-relay.conf")
    snippets: list[str] = []
    for path in Path("/etc/nginx/sites-enabled").glob("*.conf"):
        content = read_text_if_exists(path)
        if content:
            snippets.append(content)
    joined = "\n".join(snippets)
    legacy_patterns = ("100.87.225.85:4173", "127.0.0.1:8502", ":4173")
    return {
        "relay_conf_exists": relay_link.exists(),
        "legacy_patterns": [pattern for pattern in legacy_patterns if pattern in joined],
        "redirect_present": "brp-kr.ravenapis.com" in joined and "return 308" in joined,
    }


def add_check(checks: list[dict[str, Any]], status: str, name: str, message: str, **details: Any) -> None:
    checks.append({"status": status, "name": name, "message": message, "details": details})


def build_report(
    *,
    environment: str,
    root_dir: Path = ROOT_DIR,
    env_file: Path | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    spec = ENVIRONMENT_SPECS[environment]
    env_path = env_file or root_dir / "ops" / "env" / "local.env"
    env_values = parse_env_file(env_path)
    checks: list[dict[str, Any]] = []

    actual_platform = "windows" if os.name == "nt" else "linux"
    if actual_platform == spec.platform_kind:
        add_check(checks, "ok", "platform", f"{actual_platform} matches {spec.platform_kind}")
    else:
        add_check(checks, "fail", "platform", f"{actual_platform} does not match {spec.platform_kind}")

    tier = configured_deployment_tier(env_values, root_dir, spec)
    if tier == spec.deployment_tier:
        add_check(checks, "ok", "deployment_tier", f"{tier} matches {spec.deployment_tier}")
    else:
        add_check(checks, "fail", "deployment_tier", f"{tier or 'unset'} does not match {spec.deployment_tier}")

    market_scope, market_source = expected_market_scope(env_values, spec)
    expected_markets = tuple(sorted(spec.expected_markets))
    if market_scope == expected_markets:
        add_check(
            checks,
            "ok",
            "traffic_market_scope",
            f"{','.join(market_scope)} via {market_source}",
        )
    else:
        add_check(
            checks,
            "fail",
            "traffic_market_scope",
            f"{','.join(market_scope) or 'none'} does not match {','.join(expected_markets)}",
            source=market_source,
        )

    configured_port = env_value("BRP_BACKEND_PORT", env_values).strip()
    if configured_port:
        try:
            port_value = int(configured_port)
        except ValueError:
            add_check(checks, "fail", "backend_env_port", f"BRP_BACKEND_PORT is not numeric: {configured_port}")
        else:
            status = "ok" if port_value == spec.backend_port else "fail"
            add_check(
                checks,
                status,
                "backend_env_port",
                f"BRP_BACKEND_PORT={port_value}, expected {spec.backend_port}",
            )
    else:
        add_check(checks, "warn", "backend_env_port", "BRP_BACKEND_PORT is unset; relying on script default")

    head, head_error = git_head(root_dir)
    if head:
        add_check(checks, "ok", "git_head", head)
    else:
        add_check(checks, "fail", "git_head", head_error or "could not read git head")
    origin_head, origin_error = git_origin_head(root_dir)
    if origin_head:
        status = "ok" if not head or origin_head == head else "warn"
        add_check(checks, status, "git_origin_main", origin_head, head=head)
    else:
        add_check(checks, "warn", "git_origin_main", origin_error or "could not read origin/main")
    if expected_head:
        status = "ok" if head == expected_head else "fail"
        add_check(checks, status, "expected_head", f"head={head}, expected={expected_head}")

    marker = expected_head or head
    marker_ok, marker_message = dist_contains_marker(root_dir, marker)
    add_check(
        checks,
        "ok" if marker_ok else "fail",
        "frontend_dist_marker",
        marker_message,
        marker=marker,
    )

    health = backend_health(spec.backend_port)
    add_check(
        checks,
        "ok" if health.get("healthy") else "fail",
        "backend_health",
        f"http://127.0.0.1:{spec.backend_port}/health status={health.get('status_code')}",
        probe=health,
    )

    frontend = frontend_origin_status(spec.frontend_port)
    add_check(
        checks,
        "ok" if frontend.get("alive") else "fail",
        "frontend_origin",
        f"http://127.0.0.1:{spec.frontend_port}/ status={frontend.get('status_code')}",
        probe={key: value for key, value in frontend.items() if key != "body"},
    )

    for relative in LEGACY_PROXY_FILES:
        exists = (root_dir / relative).exists()
        add_check(
            checks,
            "fail" if exists else "ok",
            f"legacy_file:{relative}",
            "absent" if not exists else "present",
        )

    kr_example = root_dir / "ops" / "cloudflared" / "kr-config.example.yml"
    kr_example_content = read_text_if_exists(kr_example)
    add_check(
        checks,
        "fail" if "127.0.0.1:4173" in kr_example_content else "ok",
        "kr_cloudflared_example",
        "no retired 4173 service" if "127.0.0.1:4173" not in kr_example_content else "still references 4173",
    )

    if spec.require_kr_relay_redirect:
        relay = cn_nginx_kr_relay_status()
        relay_ok = (
            not relay["relay_conf_exists"]
            and not relay["legacy_patterns"]
            and bool(relay["redirect_present"])
        )
        add_check(
            checks,
            "ok" if relay_ok else "fail",
            "cn_kr_relay_retired",
            "CN redirects KR legacy hostname to Raven KR" if relay_ok else "CN KR relay config needs review",
            relay=relay,
        )

    if spec.require_kr_tasks:
        states = windows_task_states(("BRP-Nginx-Public", *LEGACY_KR_TASKS))
        if "_error" in states:
            add_check(checks, "fail", "kr_task_query", states["_error"])
        nginx_state = states.get("BRP-Nginx-Public", "UNKNOWN")
        add_check(
            checks,
            "ok" if nginx_state in {"Ready", "Running"} else "fail",
            "kr_nginx_task",
            f"BRP-Nginx-Public={nginx_state}",
        )
        for task_name in LEGACY_KR_TASKS:
            state = states.get(task_name, "UNKNOWN")
            add_check(
                checks,
                "ok" if state == "ABSENT" else "fail",
                f"kr_legacy_task:{task_name}",
                f"{task_name}={state}",
            )
        legacy_port_open = connectable("127.0.0.1", 4173)
        add_check(
            checks,
            "fail" if legacy_port_open else "ok",
            "kr_retired_4173_port",
            "closed" if not legacy_port_open else "open",
        )

    failures = [row for row in checks if row["status"] == "fail"]
    warnings = [row for row in checks if row["status"] == "warn"]
    return {
        "status": "fail" if failures else "ok",
        "environment": spec.name,
        "host": platform.node(),
        "root": str(root_dir),
        "env_file": str(env_path),
        "expected": {
            "backend_port": spec.backend_port,
            "frontend_port": spec.frontend_port,
            "markets": list(spec.expected_markets),
            "deployment_tier": spec.deployment_tier,
            "platform": spec.platform_kind,
        },
        "summary": {
            "failures": len(failures),
            "warnings": len(warnings),
            "checks": len(checks),
        },
        "checks": checks,
    }


def print_text_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("BRP Environment Parity Report")
    print(f"environment: {report['environment']}")
    print(f"status: {report['status']}")
    print(f"root: {report['root']}")
    print(
        f"checks: {summary['checks']} total, "
        f"{summary['failures']} fail, {summary['warnings']} warn"
    )
    for row in report["checks"]:
        label = row["status"].upper()
        print(f"[{label}] {row['name']}: {row['message']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check BRP environment parity for CN staging, CN prod, or KR prod."
    )
    parser.add_argument(
        "--environment",
        choices=sorted(ENVIRONMENT_SPECS),
        default=None,
        help="Environment to check. Defaults to path/OS inference.",
    )
    parser.add_argument("--root", type=Path, default=ROOT_DIR)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--no-strict", action="store_true", help="Always exit 0 after printing the report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root_dir = args.root.expanduser().resolve()
    environment = args.environment or infer_environment(root_dir)
    report = build_report(
        environment=environment,
        root_dir=root_dir,
        env_file=args.env_file,
        expected_head=args.expected_head.strip() or None,
    )
    if args.print_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text_report(report)
    if args.no_strict:
        return 0
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
