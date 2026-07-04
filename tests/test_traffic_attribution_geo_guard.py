import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))

planner_core = importlib.import_module("planner_core")


def test_geo_attribution_rejects_low_overlap_distant_corridor():
    target = {
        "route_id": "target",
        "osrm_duration_s": 2400,
        "stop_count": 1,
        "route_fingerprint": {
            "corridor_cells": ["0:0"],
            "stop_cells": ["0:0"],
            "center": {"lat": 0.0, "lng": 0.0},
            "bbox": {"min_lat": 0.0, "max_lat": 0.01, "min_lng": 0.0, "max_lng": 0.01},
            "bearing_sector": 0,
            "school_bearing_sector": 0,
        },
    }
    candidate = {
        "route_id": "bad-but-scale-similar",
        "source_id": "sample",
        "factor": 2.7,
        "osrm_duration_s": 2400,
        "stop_count": 1,
        "route_fingerprint": {
            "corridor_cells": ["0:9"],
            "stop_cells": ["0:9"],
            "center": {"lat": 0.0, "lng": 0.07},
            "bbox": {"min_lat": 0.0, "max_lat": 0.01, "min_lng": 0.07, "max_lng": 0.08},
            "bearing_sector": 0,
            "school_bearing_sector": 0,
        },
    }

    assert planner_core._route_attributed_factor(target, [candidate]) is None
