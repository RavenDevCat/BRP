from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _default_state_path() -> Path:
    if os.name == "nt":
        return Path(__file__).resolve().parents[2] / "state" / "osrm_manager" / "state.json"
    return Path("/opt/brp/shared/runtime/osrm_manager/state.json")


ON_DEMAND_ENABLED = _env_bool("BRP_OSRM_ON_DEMAND_ENABLED", False)
OSRM_LOCAL_DATA_DIR = Path(os.environ.get("OSRM_LOCAL_DATA_DIR", "/opt/brp/osrm-data")).expanduser()
OSRM_BIND_HOST = os.environ.get("OSRM_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
OSRM_DOCKER_PLATFORM = os.environ.get("OSRM_DOCKER_PLATFORM", "linux/amd64").strip() or "linux/amd64"
OSRM_MAX_TABLE_SIZE = os.environ.get("OSRM_MAX_TABLE_SIZE", "1000").strip() or "1000"
OSRM_DOCKER_IMAGE = os.environ.get("OSRM_DOCKER_IMAGE", "osrm/osrm-backend").strip() or "osrm/osrm-backend"
OSRM_START_TIMEOUT_SECONDS = float(os.environ.get("BRP_OSRM_START_TIMEOUT_SECONDS", "90") or 90)
OSRM_HEALTH_TIMEOUT_SECONDS = float(os.environ.get("BRP_OSRM_HEALTH_TIMEOUT_SECONDS", "2.5") or 2.5)
OSRM_READY_CACHE_SECONDS = float(os.environ.get("BRP_OSRM_READY_CACHE_SECONDS", "30") or 30)
OSRM_LOCK_DIR = Path(os.environ.get("BRP_OSRM_LOCK_DIR", "/tmp/brp-osrm-locks")).expanduser()
OSRM_STATE_PATH = Path(os.environ.get("BRP_OSRM_MANAGER_STATE_PATH", str(_default_state_path()))).expanduser()
OSRM_MIN_AVAILABLE_MB = int(os.environ.get("BRP_OSRM_MIN_AVAILABLE_MB", "1024") or 1024)
OSRM_IDLE_TTL_SECONDS = float(os.environ.get("BRP_OSRM_IDLE_TTL_SECONDS", "3600") or 3600)
OSRM_STALE_LOCK_TTL_SECONDS = float(os.environ.get("BRP_OSRM_STALE_LOCK_TTL_SECONDS", "3600") or 3600)
OSRM_LOCK_WAIT_SECONDS = float(os.environ.get("BRP_OSRM_LOCK_WAIT_SECONDS", "120") or 120)
OSRM_MAX_RUNNING_REGIONS = int(os.environ.get("BRP_OSRM_MAX_RUNNING_REGIONS", "0") or 0)


@dataclass(frozen=True)
class RegionConfig:
    region: str
    container: str
    port: int
    dataset_dir: Path
    dataset_file: str
    sample_lng: float
    sample_lat: float


def _region_config_map() -> dict[str, RegionConfig]:
    root = OSRM_LOCAL_DATA_DIR
    bangkok_port = int(os.environ.get("OSRM_BANGKOK_PORT", os.environ.get("OSRM_THAILAND_PORT", "5007")) or 5007)
    return {
        "shanghai": RegionConfig(
            "shanghai",
            "osrm-shanghai",
            int(os.environ.get("OSRM_SHANGHAI_PORT", "5002") or 5002),
            Path(os.environ.get("OSRM_SHANGHAI_DATASET_DIR", str(root / "shanghai"))).expanduser(),
            os.environ.get("OSRM_SHANGHAI_DATASET_FILE", "shanghai-latest.osrm"),
            121.4737,
            31.2304,
        ),
        "beijing": RegionConfig(
            "beijing",
            "osrm-beijing",
            int(os.environ.get("OSRM_BEIJING_PORT", "5003") or 5003),
            Path(os.environ.get("OSRM_BEIJING_DATASET_DIR", str(root / "beijing"))).expanduser(),
            os.environ.get("OSRM_BEIJING_DATASET_FILE", "beijing-latest.osrm"),
            116.4074,
            39.9042,
        ),
        "suzhou": RegionConfig(
            "suzhou",
            "osrm-suzhou",
            int(os.environ.get("OSRM_SUZHOU_PORT", "5004") or 5004),
            Path(os.environ.get("OSRM_SUZHOU_DATASET_DIR", str(root / "suzhou"))).expanduser(),
            os.environ.get("OSRM_SUZHOU_DATASET_FILE", "jiangsu-latest.osrm"),
            120.5853,
            31.2989,
        ),
        "xian": RegionConfig(
            "xian",
            "osrm-xian",
            int(os.environ.get("OSRM_XIAN_PORT", "5005") or 5005),
            Path(os.environ.get("OSRM_XIAN_DATASET_DIR", str(root / "xian"))).expanduser(),
            os.environ.get("OSRM_XIAN_DATASET_FILE", "shaanxi-latest.osrm"),
            108.9398,
            34.3416,
        ),
        "south-korea": RegionConfig(
            "south-korea",
            "osrm-south-korea",
            int(os.environ.get("OSRM_SOUTH_KOREA_PORT", "5006") or 5006),
            Path(os.environ.get("OSRM_SOUTH_KOREA_DATASET_DIR", str(root / "south-korea"))).expanduser(),
            os.environ.get("OSRM_SOUTH_KOREA_DATASET_FILE", "south-korea-latest.osrm"),
            126.9780,
            37.5665,
        ),
        "bangkok": RegionConfig(
            "bangkok",
            "osrm-bangkok",
            bangkok_port,
            Path(
                os.environ.get(
                    "OSRM_BANGKOK_DATASET_DIR",
                    os.environ.get("OSRM_THAILAND_DATASET_DIR", str(root / "bangkok")),
                )
            ).expanduser(),
            os.environ.get(
                "OSRM_BANGKOK_DATASET_FILE",
                os.environ.get("OSRM_THAILAND_DATASET_FILE", "thailand-bangkok.osrm"),
            ),
            100.5018,
            13.7563,
        ),
    }


REGIONS = _region_config_map()
PORT_REGION = {config.port: config.region for config in REGIONS.values()}
READY_CACHE: dict[str, float] = {}


def _log(message: str) -> None:
    print(f"[osrm-manager] {message}", flush=True)


class OsrmManagerError(RuntimeError):
    """Operator-readable OSRM manager failure."""


def region_for_base_url(base_url: str) -> str | None:
    try:
        parsed = urlparse(base_url)
        port = parsed.port
    except Exception:
        return None
    if port is None:
        return None
    return PORT_REGION.get(port)


def ensure_osrm_base_url(base_url: str) -> None:
    region = region_for_base_url(base_url)
    if not region:
        return
    ensure_region(region, base_url=base_url)


def ensure_region(region: str, *, base_url: str | None = None) -> None:
    if not ON_DEMAND_ENABLED:
        return
    config = REGIONS.get(region)
    if config is None:
        return
    url = base_url or f"http://127.0.0.1:{config.port}"
    now = time.monotonic()
    cached_until = READY_CACHE.get(region, 0.0)
    if cached_until > now:
        return
    if _is_osrm_ready(config, url):
        _record_region_status(config, "ready", base_url=url, managed=False)
        READY_CACHE[region] = now + OSRM_READY_CACHE_SECONDS
        return

    OSRM_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with _file_lock(OSRM_LOCK_DIR / f"{region}.lock"):
        now = time.monotonic()
        cached_until = READY_CACHE.get(region, 0.0)
        if cached_until > now:
            return
        if _is_osrm_ready(config, url):
            _record_region_status(config, "ready", base_url=url, managed=False)
            READY_CACHE[region] = now + OSRM_READY_CACHE_SECONDS
            return
        with _file_lock(OSRM_LOCK_DIR / "global-start.lock"):
            _cleanup_idle_regions(exclude_region=region)
            if _is_osrm_ready(config, url):
                _record_region_status(config, "ready", base_url=url, managed=False)
                READY_CACHE[region] = time.monotonic() + OSRM_READY_CACHE_SECONDS
                return
            _start_region(config)
            _wait_until_ready(config, url)
            _record_region_status(config, "ready", base_url=url, managed=True)
            READY_CACHE[region] = time.monotonic() + OSRM_READY_CACHE_SECONDS


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    with path.open("w") as handle:
        if OSRM_LOCK_WAIT_SECONDS < 0:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + OSRM_LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise OsrmManagerError(
                            f"Routing engine startup is queued behind {path.name} for more than "
                            f"{OSRM_LOCK_WAIT_SECONDS:.0f}s. Try again shortly or inspect OSRM manager locks."
                        ) from exc
                    time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_osrm_ready(config: RegionConfig, base_url: str) -> bool:
    try:
        response = requests.get(
            f"{base_url}/nearest/v1/driving/{config.sample_lng},{config.sample_lat}",
            params={"number": "1"},
            timeout=OSRM_HEALTH_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        return payload.get("code") == "Ok"
    except Exception:
        return False


def _wait_until_ready(config: RegionConfig, base_url: str) -> None:
    deadline = time.monotonic() + OSRM_START_TIMEOUT_SECONDS
    last_error = ""
    while time.monotonic() < deadline:
        if _is_osrm_ready(config, base_url):
            _log(f"{config.region} ready on {base_url}")
            return
        try:
            if not _is_port_open(config.port):
                last_error = f"port {config.port} not open yet"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1.0)
    message = (
        f"Routing engine unavailable for {config.region}: OSRM did not become ready within "
        f"{OSRM_START_TIMEOUT_SECONDS:.0f}s ({last_error or 'health check failed'})."
    )
    _record_region_status(config, "error", base_url=base_url, error=message, managed=True)
    raise OsrmManagerError(message)


def _is_port_open(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=1.0):
        return True


def _start_region(config: RegionConfig) -> None:
    dataset_path = config.dataset_dir / config.dataset_file
    if not dataset_path.exists():
        message = f"Routing engine unavailable for {config.region}: OSRM dataset not found at {dataset_path}"
        _record_region_status(config, "error", error=message, managed=False)
        raise OsrmManagerError(message)
    _assert_memory_available(config)
    _assert_running_region_capacity(config)
    _log(f"starting {config.region} OSRM container {config.container} on {config.port}")
    _record_region_status(config, "starting", managed=True)
    subprocess.run(
        ["docker", "rm", "-f", config.container],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        config.container,
        "--platform",
        OSRM_DOCKER_PLATFORM,
        "--restart",
        "unless-stopped",
        "-p",
        f"{OSRM_BIND_HOST}:{config.port}:5000",
        "-v",
        f"{config.dataset_dir}:/data:ro",
        OSRM_DOCKER_IMAGE,
        "osrm-routed",
        "--algorithm",
        "mld",
        "--max-table-size",
        OSRM_MAX_TABLE_SIZE,
        f"/data/{config.dataset_file}",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit {result.returncode}"
        message = f"Routing engine unavailable for {config.region}: failed to start OSRM container ({detail})"
        _record_region_status(config, "error", error=message, managed=True)
        raise OsrmManagerError(message)


def _assert_memory_available(config: RegionConfig) -> None:
    if OSRM_MIN_AVAILABLE_MB <= 0:
        return
    available_mb = _available_memory_mb()
    if available_mb is None:
        return
    if available_mb < OSRM_MIN_AVAILABLE_MB:
        message = (
            f"Routing engine unavailable for {config.region}: host has {available_mb:.0f} MB available, "
            f"below BRP_OSRM_MIN_AVAILABLE_MB={OSRM_MIN_AVAILABLE_MB}. Try again after freeing memory "
            "or starting the region manually during a maintenance window."
        )
        _record_region_status(config, "error", error=message, managed=False)
        raise OsrmManagerError(message)


def _available_memory_mb() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2:
                    return float(parts[1]) / 1024.0
    except Exception:
        return None
    return None


def _container_runtime_status(config: RegionConfig) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["docker", "inspect", config.container],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"present": False, "error": str(exc)}
    if result.returncode != 0:
        return {"present": False}
    try:
        payload = json.loads(result.stdout)
        item = payload[0] if isinstance(payload, list) and payload else {}
        state = item.get("State") if isinstance(item, dict) else {}
        config_payload = item.get("Config") if isinstance(item, dict) else {}
        return {
            "present": True,
            "status": state.get("Status") if isinstance(state, dict) else None,
            "running": bool(state.get("Running")) if isinstance(state, dict) else False,
            "started_at": state.get("StartedAt") if isinstance(state, dict) else None,
            "finished_at": state.get("FinishedAt") if isinstance(state, dict) else None,
            "image": config_payload.get("Image") if isinstance(config_payload, dict) else None,
        }
    except Exception as exc:
        return {"present": True, "error": str(exc)}


def _running_managed_regions(*, exclude_region: str | None = None) -> list[str]:
    state = _load_state()
    regions = state.get("regions")
    if not isinstance(regions, dict):
        return []
    running: list[str] = []
    for region, raw in regions.items():
        if region == exclude_region or not isinstance(raw, dict) or not raw.get("managed"):
            continue
        config = REGIONS.get(region)
        if config is None:
            continue
        container_status = _container_runtime_status(config)
        if isinstance(container_status, dict) and container_status.get("running"):
            running.append(region)
    return sorted(running)


def _assert_running_region_capacity(config: RegionConfig) -> None:
    if OSRM_MAX_RUNNING_REGIONS <= 0:
        return
    running_regions = _running_managed_regions(exclude_region=config.region)
    if len(running_regions) < OSRM_MAX_RUNNING_REGIONS:
        return
    message = (
        f"Routing engine unavailable for {config.region}: OSRM manager already has "
        f"{len(running_regions)} running managed region(s), meeting "
        f"BRP_OSRM_MAX_RUNNING_REGIONS={OSRM_MAX_RUNNING_REGIONS}. "
        f"Active managed regions: {', '.join(running_regions) or '-'}. "
        "Try again after idle cleanup or raise the limit during a maintenance window."
    )
    _record_region_status(config, "error", error=message, managed=False)
    raise OsrmManagerError(message)


def manager_status() -> dict[str, object]:
    state = _load_state()
    regions_state = state.get("regions") if isinstance(state, dict) else {}
    if not isinstance(regions_state, dict):
        regions_state = {}
    now = time.time()
    regions: list[dict[str, object]] = []
    for region, config in sorted(REGIONS.items()):
        raw_state = regions_state.get(region)
        state_entry = raw_state if isinstance(raw_state, dict) else {}
        last_seen = float(state_entry.get("last_seen_at") or 0)
        last_seen_age_s = (now - last_seen) if last_seen > 0 else None
        managed = bool(state_entry.get("managed"))
        regions.append(
            {
                "region": region,
                "container": config.container,
                "port": config.port,
                "base_url": state_entry.get("base_url") or f"http://127.0.0.1:{config.port}",
                "dataset_path": str(config.dataset_dir / config.dataset_file),
                "dataset_exists": (config.dataset_dir / config.dataset_file).exists(),
                "state_status": state_entry.get("status"),
                "managed": managed,
                "last_seen_at": state_entry.get("last_seen_at"),
                "last_seen_age_s": last_seen_age_s,
                "idle_expired": bool(
                    managed
                    and OSRM_IDLE_TTL_SECONDS > 0
                    and last_seen_age_s is not None
                    and last_seen_age_s >= OSRM_IDLE_TTL_SECONDS
                ),
                "last_error": state_entry.get("last_error"),
                "container_status": _container_runtime_status(config),
            }
        )
    return {
        "on_demand_enabled": ON_DEMAND_ENABLED,
        "state_path": str(OSRM_STATE_PATH),
        "lock_dir": str(OSRM_LOCK_DIR),
        "idle_ttl_seconds": OSRM_IDLE_TTL_SECONDS,
        "stale_lock_ttl_seconds": OSRM_STALE_LOCK_TTL_SECONDS,
        "lock_wait_seconds": OSRM_LOCK_WAIT_SECONDS,
        "min_available_mb": OSRM_MIN_AVAILABLE_MB,
        "max_running_regions": OSRM_MAX_RUNNING_REGIONS,
        "available_memory_mb": _available_memory_mb(),
        "running_managed_regions": _running_managed_regions(),
        "locks": lock_status(),
        "regions": regions,
    }


def _load_state() -> dict[str, object]:
    try:
        if not OSRM_STATE_PATH.exists():
            return {"regions": {}}
        payload = json.loads(OSRM_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("regions"), dict):
            return payload
    except Exception:
        pass
    return {"regions": {}}


def _region_lock_is_held(region: str) -> bool:
    path = OSRM_LOCK_DIR / f"{region}.lock"
    try:
        if not path.exists():
            return False
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
    except Exception:
        return True


def _save_state(state: dict[str, object]) -> None:
    try:
        OSRM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = OSRM_STATE_PATH.with_suffix(OSRM_STATE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(OSRM_STATE_PATH)
    except Exception as exc:
        _log(f"failed to write state: {exc}")


def _record_region_status(
    config: RegionConfig,
    status: str,
    *,
    base_url: str | None = None,
    error: str | None = None,
    managed: bool,
) -> None:
    state = _load_state()
    regions = state.setdefault("regions", {})
    if not isinstance(regions, dict):
        regions = {}
        state["regions"] = regions
    previous = regions.get(config.region)
    previous_managed = bool(previous.get("managed")) if isinstance(previous, dict) else False
    entry = {
        "region": config.region,
        "container": config.container,
        "port": config.port,
        "status": status,
        "managed": bool(managed or previous_managed),
        "base_url": base_url or f"http://127.0.0.1:{config.port}",
        "last_seen_at": time.time(),
    }
    if error:
        entry["last_error"] = error
    elif status != "ready" and isinstance(previous, dict) and previous.get("last_error"):
        entry["last_error"] = previous["last_error"]
    regions[config.region] = entry
    _save_state(state)


def _cleanup_idle_regions(*, exclude_region: str | None = None) -> list[str]:
    stopped: list[str] = []
    if OSRM_IDLE_TTL_SECONDS <= 0:
        return stopped
    state = _load_state()
    regions = state.get("regions")
    if not isinstance(regions, dict):
        return stopped
    now = time.time()
    changed = False
    for region, raw in list(regions.items()):
        if region == exclude_region or not isinstance(raw, dict):
            continue
        if _region_lock_is_held(region):
            continue
        if not raw.get("managed"):
            continue
        last_seen = float(raw.get("last_seen_at") or 0)
        if now - last_seen < OSRM_IDLE_TTL_SECONDS:
            continue
        config = REGIONS.get(region)
        if config is None:
            continue
        _log(f"stopping idle {region} OSRM container after {OSRM_IDLE_TTL_SECONDS:.0f}s TTL")
        subprocess.run(
            ["docker", "rm", "-f", config.container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raw["status"] = "stopped_idle"
        raw["last_seen_at"] = now
        stopped.append(region)
        changed = True
    if changed:
        _save_state(state)
    return stopped


def cleanup_idle_regions(*, exclude_region: str | None = None) -> list[str]:
    return _cleanup_idle_regions(exclude_region=exclude_region)


def _lock_file_status(path: Path, now: float) -> dict[str, object]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "name": path.name,
            "present": False,
            "locked": False,
            "age_s": None,
            "stale": False,
        }
    locked = False
    try:
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                locked = True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        locked = True
    age_s = max(0.0, now - float(stat.st_mtime))
    stale = bool(
        not locked
        and OSRM_STALE_LOCK_TTL_SECONDS > 0
        and age_s >= OSRM_STALE_LOCK_TTL_SECONDS
    )
    return {
        "path": str(path),
        "name": path.name,
        "present": True,
        "locked": locked,
        "age_s": age_s,
        "stale": stale,
    }


def lock_status() -> list[dict[str, object]]:
    if not OSRM_LOCK_DIR.exists():
        return []
    now = time.time()
    locks: list[dict[str, object]] = []
    for path in sorted(OSRM_LOCK_DIR.glob("*.lock")):
        if path.is_file():
            locks.append(_lock_file_status(path, now))
    return locks


def cleanup_stale_locks() -> list[str]:
    removed: list[str] = []
    if OSRM_STALE_LOCK_TTL_SECONDS <= 0 or not OSRM_LOCK_DIR.exists():
        return removed
    for item in lock_status():
        if not item.get("stale"):
            continue
        path = Path(str(item.get("path") or ""))
        try:
            with path.open("a") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    path.unlink(missing_ok=True)
                    removed.append(str(path))
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            continue
        except Exception as exc:
            _log(f"failed to remove stale lock {path}: {exc}")
    return removed
