from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import backend_service  # noqa: E402


class OsrmManagerApiTests(unittest.TestCase):
    def test_status_payload_summarizes_manager_report(self) -> None:
        class StubOsrmManager:
            @staticmethod
            def manager_status() -> dict[str, object]:
                return {
                    "on_demand_enabled": True,
                    "available_memory_mb": 2048,
                    "lock_wait_seconds": 120,
                    "max_running_regions": 2,
                    "running_managed_regions": ["suzhou"],
                    "locks": [
                        {"name": "shanghai.lock", "locked": False, "stale": True},
                        {"name": "suzhou.lock", "locked": True, "stale": False},
                    ],
                    "regions": [
                        {
                            "region": "shanghai",
                            "idle_expired": True,
                            "container_status": {"running": False},
                        },
                        {
                            "region": "suzhou",
                            "idle_expired": False,
                            "container_status": {"running": True},
                        },
                    ],
                }

        original = backend_service.osrm_manager
        try:
            backend_service.osrm_manager = StubOsrmManager
            payload = backend_service._osrm_manager_status_payload()
        finally:
            backend_service.osrm_manager = original

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["region_count"], 2)
        self.assertEqual(payload["summary"]["running_region_count"], 1)
        self.assertEqual(payload["summary"]["running_regions"], ["suzhou"])
        self.assertEqual(payload["summary"]["idle_expired_region_count"], 1)
        self.assertEqual(payload["summary"]["idle_expired_regions"], ["shanghai"])
        self.assertEqual(payload["summary"]["lock_count"], 2)
        self.assertEqual(payload["summary"]["locked_lock_count"], 1)
        self.assertEqual(payload["summary"]["stale_lock_count"], 1)
        self.assertEqual(payload["summary"]["available_memory_mb"], 2048)
        self.assertEqual(payload["summary"]["lock_wait_seconds"], 120)
        self.assertEqual(payload["summary"]["max_running_regions"], 2)
        self.assertEqual(payload["summary"]["running_managed_regions"], ["suzhou"])
        self.assertIs(payload["manager"]["on_demand_enabled"], True)

    def test_status_payload_returns_structured_error(self) -> None:
        class BrokenOsrmManager:
            @staticmethod
            def manager_status() -> dict[str, object]:
                raise RuntimeError("state read failed")

        original = backend_service.osrm_manager
        try:
            backend_service.osrm_manager = BrokenOsrmManager
            payload = backend_service._osrm_manager_status_payload()
        finally:
            backend_service.osrm_manager = original

        self.assertEqual(payload["status"], "error")
        self.assertIn("state read failed", payload["error"])


if __name__ == "__main__":
    unittest.main()
