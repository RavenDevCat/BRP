from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import api_app  # noqa: E402
import backend_service  # noqa: E402


@contextmanager
def patched_backend(**values: Any) -> Iterator[None]:
    originals = {name: getattr(backend_service, name) for name in values}
    try:
        for name, value in values.items():
            setattr(backend_service, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(backend_service, name, value)


def auth_headers(user_email: str = "admin@example.com") -> dict[str, str]:
    return {
        "Authorization": "Bearer secret",
        "X-BRP-User-Email": user_email,
    }


class FastApiThinShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api_app.app)

    def test_health_is_available_with_and_without_api_prefix_without_auth(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret"):
            self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
            self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})

    def test_authorized_routes_keep_legacy_error_shape(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret"):
            response = self.client.get("/api/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Unauthorized backend request."})

    def test_me_uses_current_user_headers_and_admin_flag(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret", ADMIN_EMAILS={"admin@example.com"}):
            response = self.client.get("/api/me", headers=auth_headers())

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["email"], "admin@example.com")
        self.assertIs(payload["is_admin"], True)
        self.assertEqual(payload["auth_mode"], backend_service.AUTH_PROVIDER)
        self.assertIn("auth", payload)

    def test_auth_config_and_deployment_features_match_legacy_payloads(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret"):
            auth_response = self.client.get("/api/auth/config", headers=auth_headers())
            features_response = self.client.get(
                "/api/deployment-features", headers=auth_headers()
            )

        self.assertEqual(auth_response.status_code, 200)
        self.assertEqual(auth_response.json(), backend_service._auth_config_payload())
        self.assertEqual(features_response.status_code, 200)
        self.assertEqual(
            features_response.json(), backend_service._deployment_features_payload()
        )

    def test_admin_status_routes_require_admin(self) -> None:
        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
        ):
            response = self.client.get(
                "/api/osrm-manager/status",
                headers=auth_headers("user@example.com"),
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("only available to admins", response.json()["error"])

    def test_osrm_manager_status_uses_existing_payload_builder(self) -> None:
        class StubOsrmManager:
            @staticmethod
            def manager_status() -> dict[str, object]:
                return {
                    "on_demand_enabled": True,
                    "available_memory_mb": 1024,
                    "lock_wait_seconds": 60,
                    "max_running_regions": 1,
                    "running_managed_regions": ["shanghai"],
                    "locks": [],
                    "regions": [
                        {
                            "region": "shanghai",
                            "idle_expired": False,
                            "container_status": {"running": True},
                        }
                    ],
                }

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
            osrm_manager=StubOsrmManager,
        ):
            response = self.client.get(
                "/api/osrm-manager/status",
                headers=auth_headers(),
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["running_regions"], ["shanghai"])

    def test_traffic_rollout_status_preserves_query_params(self) -> None:
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
                    "status": "ready",
                    "api_budget": {
                        "provider_api_called": False,
                        "osrm_started": False,
                    },
                }

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
            _load_traffic_rollout_status_module=lambda: StubReportModule,
        ):
            response = self.client.get(
                "/api/traffic-rollout/status?include_osrm=false&min_geo_ratio=0.80",
                headers=auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(calls[0]["min_geo_ratio"], 0.8)
        self.assertFalse(calls[0]["include_osrm"])

    def test_template_downloads_keep_attachment_headers(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret"):
            response = self.client.get(
                "/api/workbooks/template", headers=auth_headers()
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            backend_service.WORKBOOK_CONTENT_TYPE,
        )
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_fleet_vehicle_catalog_is_available(self) -> None:
        with patched_backend(SERVICE_TOKEN="secret"):
            response = self.client.get(
                "/api/fleet-planner/vehicle-catalog?market=KR&monitor_seats=1",
                headers=auth_headers(),
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["summary"]["market"], "KR")
        self.assertGreaterEqual(payload["summary"]["vehicle_count"], 1)
        self.assertIsInstance(payload["catalog"], list)


if __name__ == "__main__":
    unittest.main()
