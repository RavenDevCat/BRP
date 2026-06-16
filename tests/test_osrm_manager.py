import importlib
import sys
import time
from pathlib import Path


def load_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_OSRM_ON_DEMAND_ENABLED", "true")
    monkeypatch.setenv("BRP_OSRM_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("BRP_OSRM_MANAGER_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("BRP_OSRM_IDLE_TTL_SECONDS", "10")
    monkeypatch.setenv("BRP_OSRM_MIN_AVAILABLE_MB", "0")
    sys.modules.pop("osrm_manager", None)
    return importlib.import_module("osrm_manager")


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

    manager._cleanup_idle_regions(exclude_region="beijing")

    assert stopped == [["docker", "rm", "-f", "osrm-bangkok"]]
    state = manager._load_state()
    assert state["regions"]["bangkok"]["status"] == "stopped_idle"
    assert state["regions"]["shanghai"]["status"] == "ready"
    assert state["regions"]["suzhou"]["status"] == "ready"
