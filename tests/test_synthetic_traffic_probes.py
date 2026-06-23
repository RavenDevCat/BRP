from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "scripts" / "plan_synthetic_traffic_probes.py"
spec = importlib.util.spec_from_file_location("plan_synthetic_traffic_probes", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_synthetic_probe_baseline_is_sampler_ready_without_provider_calls() -> None:
    baseline = probe.build_probe_baseline("CN", "Shanghai", grid_size=3, direction="both")

    assert baseline["synthetic_probe_baseline"] is True
    assert baseline["route_count"] == 16
    assert baseline["point_count"] == 9
    assert baseline["routes"][0]["synthetic_probe"] is True
    assert len(baseline["routes"][0]["stops"]) == 3
    assert all("lat" in stop and "lng" in stop for route in baseline["routes"] for stop in route["stops"])
    assert any(route["route_id"].startswith("synthetic-001-to_school") for route in baseline["routes"])
    assert any(route["route_id"].startswith("synthetic-009-from_school") for route in baseline["routes"])
