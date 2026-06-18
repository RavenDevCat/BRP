from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import report_traffic_profile_network as report  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_network_report_builds_profile_and_corridor_buckets(tmp_path: Path) -> None:
    _write(
        tmp_path / "sample.json",
        {
            "measured_at": "2026-06-18T06:20:00+08:00",
            "local_date": "2026-06-18",
            "market": "CN",
            "country": "China",
            "city": "Shanghai",
            "period": "am_peak",
            "provider": "amap",
            "routes": [
                {
                    "route_id": "R1",
                    "source_id": "job-a",
                    "factor": 1.5,
                    "osrm_duration_s": 100.0,
                    "route_fingerprint": {
                        "grid_degrees": 0.01,
                        "cell_count": 2,
                        "corridor_cells": ["3100:12100", "3101:12101"],
                        "stop_cells": ["3100:12100"],
                        "center": {"lat": 31.005, "lng": 121.005},
                    },
                },
                {
                    "route_id": "R2",
                    "source_id": "job-a",
                    "factor": 2.0,
                    "osrm_duration_s": 300.0,
                    "route_fingerprint": {
                        "grid_degrees": 0.01,
                        "cell_count": 1,
                        "corridor_cells": ["3100:12100"],
                        "center": {"lat": 31.006, "lng": 121.006},
                    },
                },
                {
                    "route_id": "legacy",
                    "factor": 3.0,
                    "osrm_duration_s": 100.0,
                },
            ],
        },
    )
    _write(
        tmp_path / "dry_run.json",
        {
            "measured_at": "2026-06-18T06:20:00+08:00",
            "local_date": "2026-06-18",
            "market": "CN",
            "city": "Shanghai",
            "period": "am_peak",
            "dry_run": True,
            "routes": [{"route_id": "ignored", "factor": 9.0, "osrm_duration_s": 100.0}],
        },
    )

    result = report.build_network_report(
        tmp_path,
        require_profiles=["CN:Shanghai:am_peak"],
        min_geo_route_ratio=0.5,
        min_bucket_route_count=2,
    )

    assert result["read_only"] is True
    assert result["provider_api_called"] is False
    assert result["osrm_started"] is False
    assert result["status"] == "ready"
    assert result["route_sample_count"] == 3
    profile = result["profiles"][0]
    assert profile["profile_key"] == "CN:Shanghai:am_peak"
    assert profile["route_sample_count"] == 3
    assert profile["geo_route_sample_count"] == 2
    assert profile["scale_only_route_sample_count"] == 1
    assert profile["usable_corridor_bucket_count"] == 1
    assert profile["top_corridor_buckets"][0]["cell"] == "3100:12100"
    assert profile["top_corridor_buckets"][0]["route_sample_count"] == 2
    assert round(profile["factor"]["weighted_mean"], 3) == 2.1


def test_network_report_filters_by_measurement_time_and_reports_missing_requirement(tmp_path: Path) -> None:
    _write(
        tmp_path / "old.json",
        {
            "measured_at": "2026-06-17T06:20:00+08:00",
            "market": "CN",
            "city": "Shanghai",
            "period": "am_peak",
            "routes": [
                {
                    "route_id": "R1",
                    "factor": 1.5,
                    "osrm_duration_s": 100.0,
                    "route_fingerprint": {"cell_count": 1, "corridor_cells": ["3100:12100"]},
                }
            ],
        },
    )

    result = report.build_network_report(
        tmp_path,
        require_profiles=["CN:Shanghai:am_peak"],
        min_measured_at="2026-06-18T00:00:00+08:00",
        min_geo_route_ratio=1.0,
    )

    assert result["status"] == "blocked"
    assert result["profile_count"] == 0
    assert result["required_profiles"][0]["reason"] == "missing_profile"


def test_main_returns_nonzero_for_failed_required_profile(tmp_path: Path, capsys) -> None:
    exit_code = report.main(
        [
            "--sample-dir",
            str(tmp_path),
            "--require-profile",
            "CN:Shanghai:pm_peak",
            "--min-geo-route-ratio",
            "1.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "requirement_failed CN:Shanghai:pm_peak" in captured.err
