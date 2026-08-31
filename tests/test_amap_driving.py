import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))

from amap_driving import (
    AMAP_DRIVING_STRATEGY,
    amap_distance_is_anomalous,
    amap_driving_path_stats,
    build_amap_driving_params,
    first_amap_driving_path,
)


def test_v5_driving_params_match_amap_app_recommended_strategy():
    params = build_amap_driving_params(
        [(31.1, 121.1), (31.2, 121.2), (31.3, 121.3)],
        include_geometry=True,
    )

    assert params["strategy"] == AMAP_DRIVING_STRATEGY == "32"
    assert params["show_fields"] == "cost,navi,polyline"
    assert params["waypoints"] == "121.200000,31.200000"


def test_v5_path_stats_read_nested_cost_duration():
    path = first_amap_driving_path(
        {"route": {"paths": [{"distance": "1234", "cost": {"duration": "567"}}]}}
    )

    assert path is not None
    assert amap_driving_path_stats(path) == {"duration_s": 567.0, "distance_m": 1234.0}


def test_distance_anomaly_requires_ratio_and_material_excess():
    assert amap_distance_is_anomalous(38000, 23600)
    assert not amap_distance_is_anomalous(28000, 23600)
    assert not amap_distance_is_anomalous(1500, 1000)
