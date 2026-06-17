from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_service  # noqa: E402


class TrafficRolloutStatusApiTests(unittest.TestCase):
    def test_payload_uses_read_only_rollout_status_builder(self) -> None:
        calls: list[dict[str, object]] = []

        class StubReadiness:
            DEFAULT_CUTOFF = "2026-06-17T19:00:00+08:00"
            DEFAULT_PROFILES = (("CN", "Shanghai", "am_peak"),)

            class report_live_traffic_readiness:
                DEFAULT_SAMPLE_DIR = Path("/tmp/traffic-samples")

        class StubReportModule:
            DEFAULT_LOCAL_TIMEZONE = "Asia/Shanghai"
            report_traffic_rollout_readiness = StubReadiness

            @staticmethod
            def build_status(**kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                return {
                    "status": "waiting",
                    "api_budget": {
                        "provider_api_called": False,
                        "osrm_started": False,
                    },
                }

        original_loader = backend_service._load_traffic_rollout_status_module
        try:
            backend_service._load_traffic_rollout_status_module = lambda: StubReportModule
            payload = backend_service._traffic_rollout_status_payload(
                {
                    "include_timers": "0",
                    "include_osrm": "false",
                    "include_budget": "yes",
                    "min_geo_ratio": "0.75",
                    "min_measured_at": "2026-06-18T00:00:00+08:00",
                    "local_timezone": "Asia/Shanghai",
                }
            )
        finally:
            backend_service._load_traffic_rollout_status_module = original_loader

        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(
            payload["endpoint"],
            {
                "read_only": True,
                "provider_api_called": False,
                "osrm_started": False,
            },
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["sample_dir"], Path("/tmp/traffic-samples"))
        self.assertEqual(calls[0]["min_measured_at"], "2026-06-18T00:00:00+08:00")
        self.assertEqual(calls[0]["profiles"], [("CN", "Shanghai", "am_peak")])
        self.assertEqual(calls[0]["min_geo_ratio"], 0.75)
        self.assertFalse(calls[0]["include_timers"])
        self.assertFalse(calls[0]["include_osrm"])
        self.assertTrue(calls[0]["include_budget"])
        self.assertEqual(calls[0]["local_timezone"], "Asia/Shanghai")

    def test_payload_returns_structured_error(self) -> None:
        original_loader = backend_service._load_traffic_rollout_status_module
        try:
            backend_service._load_traffic_rollout_status_module = lambda: (_ for _ in ()).throw(
                RuntimeError("script missing")
            )
            payload = backend_service._traffic_rollout_status_payload()
        finally:
            backend_service._load_traffic_rollout_status_module = original_loader

        self.assertEqual(payload["status"], "error")
        self.assertIn("script missing", payload["error"])
        self.assertEqual(
            payload["endpoint"],
            {
                "read_only": True,
                "provider_api_called": False,
                "osrm_started": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
