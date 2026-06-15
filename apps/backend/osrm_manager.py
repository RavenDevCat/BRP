from __future__ import annotations

import fcntl
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
        READY_CACHE[region] = now + OSRM_READY_CACHE_SECONDS
        return

    OSRM_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with _file_lock(OSRM_LOCK_DIR / f"{region}.lock"):
        now = time.monotonic()
        cached_until = READY_CACHE.get(region, 0.0)
        if cached_until > now:
            return
        if _is_osrm_ready(config, url):
            READY_CACHE[region] = now + OSRM_READY_CACHE_SECONDS
            return
        with _file_lock(OSRM_LOCK_DIR / "global-start.lock"):
            if _is_osrm_ready(config, url):
                READY_CACHE[region] = time.monotonic() + OSRM_READY_CACHE_SECONDS
                return
            _start_region(config)
            _wait_until_ready(config, url)
            READY_CACHE[region] = time.monotonic() + OSRM_READY_CACHE_SECONDS


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    with path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
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
    raise RuntimeError(
        f"OSRM region {config.region} did not become ready within "
        f"{OSRM_START_TIMEOUT_SECONDS:.0f}s ({last_error or 'health check failed'})."
    )


def _is_port_open(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=1.0):
        return True


def _start_region(config: RegionConfig) -> None:
    dataset_path = config.dataset_dir / config.dataset_file
    if not dataset_path.exists():
        raise RuntimeError(f"OSRM dataset for {config.region} not found: {dataset_path}")
    _log(f"starting {config.region} OSRM container {config.container} on {config.port}")
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
        raise RuntimeError(f"Failed to start OSRM region {config.region}: {detail}")
