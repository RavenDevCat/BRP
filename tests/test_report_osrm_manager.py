from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import report_osrm_manager  # noqa: E402


class ReportOsrmManagerTests(unittest.TestCase):
    def test_detects_python_job_worker(self) -> None:
        self.assertTrue(
            report_osrm_manager._is_active_worker_process(
                "python3",
                "python3 /opt/brp/staging/app/apps/backend/backend_job_runner.py",
            )
        )

    def test_ignores_non_worker_commands(self) -> None:
        self.assertFalse(
            report_osrm_manager._is_active_worker_process(
                "bash",
                "bash -c cd /opt/brp/staging/app && rg -n backend_job_runner.py apps/backend",
            )
        )

    def test_cleanup_idle_skips_active_workers_without_stopping_osrm(self) -> None:
        with (
            mock.patch.object(
                report_osrm_manager,
                "_active_worker_processes",
                return_value=["123 python python apps/backend/backend_job_runner.py"],
            ),
            mock.patch.object(report_osrm_manager.osrm_manager, "cleanup_idle_regions") as cleanup_idle,
            mock.patch.object(report_osrm_manager.osrm_manager, "cleanup_stale_locks") as cleanup_locks,
        ):
            result = report_osrm_manager.cleanup_idle(force=False)

        self.assertEqual(result["status"], "skipped_active_workers")
        self.assertEqual(result["stopped_regions"], [])
        cleanup_idle.assert_not_called()
        cleanup_locks.assert_not_called()

    def test_cleanup_idle_force_runs_even_with_active_workers(self) -> None:
        with (
            mock.patch.object(
                report_osrm_manager,
                "_active_worker_processes",
                return_value=["123 python python apps/backend/backend_job_runner.py"],
            ),
            mock.patch.object(
                report_osrm_manager.osrm_manager,
                "cleanup_idle_regions",
                return_value=["shanghai"],
            ) as cleanup_idle,
            mock.patch.object(
                report_osrm_manager.osrm_manager,
                "cleanup_stale_locks",
                return_value=["shanghai.lock"],
            ) as cleanup_locks,
        ):
            result = report_osrm_manager.cleanup_idle(force=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stopped_regions"], ["shanghai"])
        self.assertEqual(result["removed_stale_locks"], ["shanghai.lock"])
        cleanup_idle.assert_called_once()
        cleanup_locks.assert_called_once()


if __name__ == "__main__":
    unittest.main()
