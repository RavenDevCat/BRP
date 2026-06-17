from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "scripts"))

import report_osrm_manager  # noqa: E402


class ReportOsrmManagerTests(unittest.TestCase):
    def test_detects_python_job_and_sampler_workers(self) -> None:
        self.assertTrue(
            report_osrm_manager._is_active_worker_process(
                "python",
                "/opt/brp/staging/venv/bin/python apps/backend/live_traffic_sampler.py --dry-run",
            )
        )
        self.assertTrue(
            report_osrm_manager._is_active_worker_process(
                "python3",
                "python3 /opt/brp/staging/app/apps/backend/backend_job_runner.py",
            )
        )

    def test_detects_wrapper_when_it_is_shell_entrypoint(self) -> None:
        self.assertTrue(
            report_osrm_manager._is_active_worker_process(
                "bash",
                "bash /opt/brp/staging/app/ops/scripts/run_live_traffic_sampler.sh pm_peak",
            )
        )

    def test_ignores_diagnostics_that_only_mention_wrapper_names(self) -> None:
        self.assertFalse(
            report_osrm_manager._is_active_worker_process(
                "bash",
                "bash -c cd /opt/brp/staging/app && rg -n run_live_traffic_sampler.sh ops/scripts",
            )
        )
        self.assertFalse(
            report_osrm_manager._is_active_worker_process(
                "rg",
                "rg -n live_traffic_sampler.py apps/backend ops/scripts",
            )
        )


if __name__ == "__main__":
    unittest.main()
