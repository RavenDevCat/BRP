import importlib
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))


def load_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_OSRM_ON_DEMAND_ENABLED", "true")
    monkeypatch.setenv("OSRM_LOCAL_DATA_DIR", str(tmp_path / "osrm-data"))
    monkeypatch.setenv("BRP_OSRM_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("BRP_OSRM_MANAGER_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("BRP_OSRM_IDLE_TTL_SECONDS", "10")
    monkeypatch.setenv("BRP_OSRM_STALE_LOCK_TTL_SECONDS", "10")
    monkeypatch.setenv("BRP_OSRM_MIN_AVAILABLE_MB", "0")
    sys.modules.pop("osrm_manager", None)
    return importlib.import_module("osrm_manager")


def test_default_state_path_uses_shared_runtime_on_linux(monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_OSRM_ON_DEMAND_ENABLED", "true")
    monkeypatch.setenv("OSRM_LOCAL_DATA_DIR", str(tmp_path / "osrm-data"))
    monkeypatch.setenv("BRP_OSRM_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("BRP_OSRM_MANAGER_STATE_PATH", raising=False)
    sys.modules.pop("osrm_manager", None)

    manager = importlib.import_module("osrm_manager")

    if os.name == "nt":
        assert str(manager.OSRM_STATE_PATH).endswith("state\\osrm_manager\\state.json")
    else:
        assert str(manager.OSRM_STATE_PATH) == "/opt/brp/shared/runtime/osrm_manager/state.json"


def test_ensure_region_starts_only_once_when_ready_cache_is_warm(monkeypatch, tmp_path):
    manager = load_manager(monkeypatch, tmp_path)
    config = manager.REGIONS["bangkok"]
    (config.dataset_dir).mkdir(parents=True, exist_ok=True)
    (config.dataset_dir / config.dataset_file).write_text("stub", encoding="utf-8")

    started = []
    ready_calls = {"count": 0}

    def fake_ready(_config, _base_url):
        ready_calls["count"] += 1
        return bool(started)

    def fake_start(_config):
        started.append(_config.region)

    monkeypatch.setattr(manager, "_is_osrm_ready", fake_ready)
    monkeypatch.setattr(manager, "_start_region", fake_start)
    monkeypatch.setattr(manager, "_wait_until_ready", lambda _config, _base_url: None)

    manager.ensure_region("bangkok")
    manager.ensure_region("bangkok")

    assert started == ["bangkok"]
    assert ready_calls["count"] >= 1


def test_cleanup_idle_regions_only_stops_managed_expired_entries(monkeypatch, tmp_path):
    manager = load_manager(monkeypatch, tmp_path)
    now = time.time()
    state_path = Path(manager.OSRM_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        """
{
  "regions": {
    "bangkok": {
      "region": "bangkok",
      "container": "osrm-bangkok",
      "port": 5007,
      "status": "ready",
      "managed": true,
      "last_seen_at": 1
    },
    "shanghai": {
      "region": "shanghai",
      "container": "osrm-shanghai",
      "port": 5002,
      "status": "ready",
      "managed": false,
      "last_seen_at": 1
    },
    "suzhou": {
      "region": "suzhou",
      "container": "osrm-suzhou",
      "port": 5004,
      "status": "ready",
      "managed": true,
      "last_seen_at": REPLACE_NOW
    }
  }
}
""".replace("REPLACE_NOW", str(now)),
        encoding="utf-8",
    )

    stopped = []

    def fake_run(command, **_kwargs):
        stopped.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(manager.subprocess, "run", fake_run)

    stopped_regions = manager._cleanup_idle_regions(exclude_region="beijing")

    assert stopped_regions == ["bangkok"]
    assert stopped == [["docker", "rm", "-f", "osrm-bangkok"]]
    state = manager._load_state()
    assert state["regions"]["bangkok"]["status"] == "stopped_idle"
    assert state["regions"]["shanghai"]["status"] == "ready"
    assert state["regions"]["suzhou"]["status"] == "ready"


def test_lock_status_reports_unlocked_stale_lock(monkeypatch, tmp_path):
    manager = load_manager(monkeypatch, tmp_path)
    lock_dir = Path(manager.OSRM_LOCK_DIR)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "bangkok.lock"
    lock_path.write_text("", encoding="utf-8")
    old = time.time() - 30
    os.utime(lock_path, (old, old))

    locks = manager.lock_status()

    assert len(locks) == 1
    lock = locks[0]
    assert lock["path"] == str(lock_path)
    assert lock["name"] == "bangkok.lock"
    assert lock["present"] is True
    assert lock["locked"] is False
    assert lock["stale"] is True
    assert lock["age_s"] >= 10


def test_cleanup_stale_locks_skips_locked_files(monkeypatch, tmp_path):
    if os.name == "nt":
        return
    import fcntl

    manager = load_manager(monkeypatch, tmp_path)
    lock_dir = Path(manager.OSRM_LOCK_DIR)
    lock_dir.mkdir(parents=True, exist_ok=True)
    stale_lock = lock_dir / "bangkok.lock"
    locked_lock = lock_dir / "shanghai.lock"
    stale_lock.write_text("", encoding="utf-8")
    locked_lock.write_text("", encoding="utf-8")
    old = time.time() - 30
    os.utime(stale_lock, (old, old))
    os.utime(locked_lock, (old, old))

    handle = locked_lock.open("a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        removed = manager.cleanup_stale_locks()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    assert removed == [str(stale_lock)]
    assert not stale_lock.exists()
    assert locked_lock.exists()


def test_manager_status_reports_state_dataset_and_container(monkeypatch, tmp_path):
    manager = load_manager(monkeypatch, tmp_path)
    config = manager.REGIONS["bangkok"]
    config.dataset_dir.mkdir(parents=True, exist_ok=True)
    (config.dataset_dir / config.dataset_file).write_text("stub", encoding="utf-8")
    state_path = Path(manager.OSRM_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        """
{
  "regions": {
    "bangkok": {
      "region": "bangkok",
      "container": "osrm-bangkok",
      "port": 5007,
      "status": "ready",
      "managed": true,
      "last_seen_at": 1
    }
  }
}
""",
        encoding="utf-8",
    )

    def fake_inspect(command, **_kwargs):
        assert command == ["docker", "inspect", "osrm-bangkok"]

        class Result:
            returncode = 0
            stdout = '[{"State":{"Status":"running","Running":true,"StartedAt":"today"},"Config":{"Image":"osrm/osrm-backend"}}]'

        return Result()

    def fake_run(command, **kwargs):
        if command == ["docker", "inspect", "osrm-bangkok"]:
            return fake_inspect(command, **kwargs)

        class Result:
            returncode = 1
            stdout = ""

        return Result()

    monkeypatch.setattr(manager.subprocess, "run", fake_run)

    report = manager.manager_status()
    bangkok = next(row for row in report["regions"] if row["region"] == "bangkok")

    assert report["state_path"] == str(state_path)
    assert bangkok["dataset_exists"] is True
    assert bangkok["managed"] is True
    assert bangkok["state_status"] == "ready"
    assert bangkok["idle_expired"] is True
    assert bangkok["container_status"]["status"] == "running"
    assert report["max_running_regions"] == manager.OSRM_MAX_RUNNING_REGIONS
    assert report["running_managed_regions"] == ["bangkok"]


def test_ensure_region_refuses_when_running_region_limit_is_reached(monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_OSRM_MAX_RUNNING_REGIONS", "1")
    manager = load_manager(monkeypatch, tmp_path)
    bangkok = manager.REGIONS["bangkok"]
    bangkok.dataset_dir.mkdir(parents=True, exist_ok=True)
    (bangkok.dataset_dir / bangkok.dataset_file).write_text("stub", encoding="utf-8")
    state_path = Path(manager.OSRM_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        """
{
  "regions": {
    "suzhou": {
      "region": "suzhou",
      "container": "osrm-suzhou",
      "port": 5004,
      "status": "ready",
      "managed": true,
      "last_seen_at": REPLACE_NOW
    }
  }
}
""".replace("REPLACE_NOW", str(time.time())),
        encoding="utf-8",
    )

    def fake_ready(_config, _base_url):
        return False

    def fake_container_status(config):
        if config.region == "suzhou":
            return {"present": True, "running": True, "status": "running"}
        return {"present": False}

    monkeypatch.setattr(manager, "_is_osrm_ready", fake_ready)
    monkeypatch.setattr(manager, "_container_runtime_status", fake_container_status)

    with pytest.raises(manager.OsrmManagerError, match="BRP_OSRM_MAX_RUNNING_REGIONS=1"):
        manager.ensure_region("bangkok")

    state = manager._load_state()
    bangkok_state = state["regions"]["bangkok"]
    assert bangkok_state["status"] == "error"
    assert "Active managed regions: suzhou" in bangkok_state["last_error"]


def test_file_lock_times_out_instead_of_waiting_forever(monkeypatch, tmp_path):
    if os.name == "nt":
        return
    import fcntl

    monkeypatch.setenv("BRP_OSRM_LOCK_WAIT_SECONDS", "0.01")
    manager = load_manager(monkeypatch, tmp_path)
    lock_path = Path(manager.OSRM_LOCK_DIR) / "global-start.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(manager.OsrmManagerError, match="queued behind global-start.lock"):
            with manager._file_lock(lock_path):
                pass
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
