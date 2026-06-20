from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import api_app  # noqa: E402
import backend_service  # noqa: E402


class FakeJobStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    def list_jobs(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "job_id": job_id,
                "owner_email": record.get("owner_email"),
                "shared_with_all": bool(record.get("shared_with_all")),
                "status": record.get("status", "succeeded"),
            }
            for job_id, record in self.records.items()
            if include_all
            or record.get("owner_email") == user_email
            or bool(record.get("shared_with_all"))
        ]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        record = self.records.get(job_id)
        return dict(record) if record else None

    def delete_job(self, job_id: str) -> bool:
        self.deleted.append(job_id)
        self.records.pop(job_id, None)
        return True

    def create_job(
        self,
        config_payload: dict[str, Any],
        prepared_payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        owner_email: str = "",
    ) -> dict[str, Any]:
        job_id = "new-job"
        record = {
            "job_id": job_id,
            "owner_email": owner_email,
            "status": "queued",
            "config": dict(config_payload or {}),
            "prepared_payload": dict(prepared_payload or {}),
            "metadata": dict(metadata or {}),
            "prepared_payload_summary": {"stop_count": 0},
            "error": None,
        }
        self.records[job_id] = record
        self.created.append(record)
        return {
            "job_id": job_id,
            "owner_email": owner_email,
            "status": "queued",
            "metadata": dict(metadata or {}),
            "prepared_payload_summary": {"stop_count": 0},
            "error": None,
        }

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        record = self.records.get(job_id)
        if not record:
            return None
        record.update(changes)
        self.updated.append((job_id, dict(changes)))
        return dict(record)

    def begin_ai_audit(
        self,
        job_id: str,
        *,
        force: bool = False,
        required_languages: list[str] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        record = self.records.get(job_id)
        if not record:
            return "missing", None
        required_keys = {
            backend_service._ai_audit_language_key(language)
            for language in (required_languages or ["English"])
        }
        existing_reports = backend_service._ai_audit_report_map(record)
        if required_keys and all(key in existing_reports for key in required_keys) and not force:
            return "cached", dict(record)
        if str(record.get("ai_audit_status", "")).strip().lower() == "running":
            return "running", dict(record)
        record["ai_audit_status"] = "running"
        return "started", dict(record)


class FakeHistoryStore:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = dict(records or {})
        self.deleted: list[str] = []

    def list(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "run_id": run_id,
                "owner_email": record.get("owner_email"),
                "shared_with_all": bool(record.get("shared_with_all")),
                "summary": dict(record.get("summary") or {}),
            }
            for run_id, record in self.records.items()
            if include_all
            or record.get("owner_email") == user_email
            or bool(record.get("shared_with_all"))
        ]

    def get(self, run_id: str) -> dict[str, Any] | None:
        record = self.records.get(run_id)
        return dict(record) if record else None

    def delete(self, run_id: str) -> bool:
        self.deleted.append(run_id)
        self.records.pop(run_id, None)
        return True


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

    def test_jobs_history_read_and_delete_keep_access_rules(self) -> None:
        store = FakeJobStore()
        store.records["owned"] = {
            "job_id": "owned",
            "owner_email": "admin@example.com",
            "status": "succeeded",
        }
        store.records["other"] = {
            "job_id": "other",
            "owner_email": "other@example.com",
            "status": "succeeded",
        }

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS=set(),
            JOB_STORE=store,
            _cancel_job=lambda job_id: {"job_id": job_id, "status": "canceled"},
        ):
            list_response = self.client.get("/api/jobs", headers=auth_headers())
            owned_response = self.client.get("/api/jobs/owned", headers=auth_headers())
            forbidden_response = self.client.get("/api/jobs/other", headers=auth_headers())
            delete_response = self.client.delete("/api/jobs/owned", headers=auth_headers())

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([row["job_id"] for row in list_response.json()["jobs"]], ["owned"])
        self.assertEqual(owned_response.status_code, 200)
        self.assertEqual(owned_response.json()["job_id"], "owned")
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(delete_response.json(), {"deleted": True, "job_id": "owned"})
        self.assertEqual(store.deleted, ["owned"])

    def test_distance_history_read_create_and_delete_keep_legacy_store_selection(self) -> None:
        route_store = FakeHistoryStore(
            {
                "route-run": {
                    "run_id": "route-run",
                    "owner_email": "admin@example.com",
                    "summary": {"tool_mode": "route_cost"},
                    "route_cost_result": {"ok": True},
                }
            }
        )

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
            ROUTE_COST_HISTORY_STORE=route_store,
            REFERENCE_DISTANCE_HISTORY_STORE=FakeHistoryStore(),
            DISTANCE_CHECKER_HISTORY_STORE=FakeHistoryStore(),
            _handle_distance_checker_history_create=lambda payload, user_email: {
                "run_id": "new-run",
                "owner_email": user_email,
                "summary": {"tool_mode": payload.get("tool_mode")},
            },
        ):
            list_response = self.client.get(
                "/api/distance-checker/route-cost-history", headers=auth_headers()
            )
            get_response = self.client.get(
                "/api/distance-checker/route-cost-history/route-run",
                headers=auth_headers(),
            )
            create_response = self.client.post(
                "/api/distance-checker/history",
                headers=auth_headers(),
                json={"tool_mode": "route_cost"},
            )
            delete_response = self.client.delete(
                "/api/distance-checker/route-cost-history/route-run",
                headers=auth_headers(),
            )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["jobs"][0]["run_id"], "route-run")
        self.assertEqual(get_response.json()["route_cost_result"], {"ok": True})
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["summary"]["tool_mode"], "route_cost")
        self.assertEqual(delete_response.json(), {"deleted": True, "run_id": "route-run"})
        self.assertEqual(route_store.deleted, ["route-run"])

    def test_fleet_history_hydrates_and_protects_shared_seed_delete(self) -> None:
        fleet_store = FakeHistoryStore(
            {
                "seed": {
                    "run_id": "seed",
                    "owner_email": "seed@example.com",
                    "shared_with_all": True,
                    "global_plan_result": {"summary": {}, "routes": []},
                }
            }
        )

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS=set(),
            FLEET_PLANNER_HISTORY_STORE=fleet_store,
            _hydrate_fleet_planner_history_record=lambda record: {
                **record,
                "hydrated": True,
            },
        ):
            get_response = self.client.get(
                "/api/fleet-planner/history/seed",
                headers=auth_headers("user@example.com"),
            )
            delete_response = self.client.delete(
                "/api/fleet-planner/history/seed",
                headers=auth_headers("user@example.com"),
            )

        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.json()["hydrated"])
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(fleet_store.deleted, [])

    def test_job_map_export_artifact_and_traffic_routes_use_existing_builders(self) -> None:
        job_store = FakeJobStore()
        job_store.records["job-1"] = {
            "job_id": "job-1",
            "owner_email": "admin@example.com",
            "result": {},
        }
        artifact_path = ROOT / "tests" / "_tmp_map_artifact.html"
        artifact_path.write_text("<html>map</html>", encoding="utf-8")

        try:
            with patched_backend(
                SERVICE_TOKEN="secret",
                ADMIN_EMAILS={"admin@example.com"},
                JOB_STORE=job_store,
                _build_job_map_data=lambda record, scenario: (
                    {"scenario": scenario, "job_id": record["job_id"]},
                    None,
                ),
                _resolve_job_map_artifact=lambda record, key: (artifact_path, None),
                _build_time_impact_workbook_export=lambda record, scenario: (
                    b"PKworkbook",
                    None,
                ),
                _job_traffic_attribution_payload=lambda record, **kwargs: {
                    "job_id": record["job_id"],
                    **kwargs,
                },
            ):
                map_response = self.client.get(
                    "/api/jobs/job-1/map-data/original", headers=auth_headers()
                )
                artifact_response = self.client.get(
                    "/api/jobs/job-1/artifacts/current_plan?download=1",
                    headers=auth_headers(),
                )
                export_response = self.client.get(
                    "/api/jobs/job-1/exports/time-impact-original",
                    headers=auth_headers(),
                )
                traffic_response = self.client.get(
                    "/api/jobs/job-1/traffic-attribution?route_evidence=true&top_matches=yes",
                    headers=auth_headers(),
                )
        finally:
            artifact_path.unlink(missing_ok=True)

        self.assertEqual(map_response.json(), {"scenario": "original", "job_id": "job-1"})
        self.assertIn("attachment;", artifact_response.headers["content-disposition"])
        self.assertEqual(artifact_response.text, "<html>map</html>")
        self.assertEqual(export_response.content, b"PKworkbook")
        self.assertIn("attachment;", export_response.headers["content-disposition"])
        self.assertEqual(
            traffic_response.json(),
            {
                "job_id": "job-1",
                "include_route_evidence": True,
                "include_top_matches": True,
            },
        )

    def test_map_tile_route_uses_tile_loader_and_cache_headers(self) -> None:
        with patched_backend(
            SERVICE_TOKEN="secret",
            _load_or_fetch_map_tile=lambda z, x, y: (
                f"{z}/{x}/{y}".encode("utf-8"),
                True,
            ),
        ):
            response = self.client.get(
                "/api/map-tiles/1/2/3.png", headers=auth_headers()
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"1/2/3")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertIn("immutable", response.headers["cache-control"])

    def test_side_tool_post_routes_delegate_to_existing_handlers(self) -> None:
        calls: list[tuple[str, dict[str, Any], str]] = []

        def make_handler(name: str):
            def handler(payload: dict[str, Any], user_email: str = "") -> dict[str, Any]:
                calls.append((name, payload, user_email))
                return {"handler": name, "payload": payload, "user_email": user_email}

            return handler

        with patched_backend(
            SERVICE_TOKEN="secret",
            _handle_distance_workbook_preview=make_handler("distance-preview"),
            _handle_reference_distance_check=make_handler("reference"),
            _handle_current_plan_route_cost=make_handler("route-cost"),
            _handle_fleet_planner_preview=make_handler("fleet-preview"),
            _handle_fleet_planner_geocode=make_handler("fleet-geocode"),
            _handle_fleet_planner_clusters=make_handler("fleet-clusters"),
            _handle_fleet_planner_route_preview=make_handler("fleet-route-preview"),
            _handle_fleet_planner_global_plan=make_handler("fleet-global-plan"),
            _handle_fleet_planner_history_create=make_handler("fleet-history"),
        ):
            endpoints = [
                "/api/distance-checker/workbook-preview",
                "/api/distance-checker/reference",
                "/api/distance-checker/route-cost",
                "/api/fleet-planner/preview",
                "/api/fleet-planner/geocode",
                "/api/fleet-planner/clusters",
                "/api/fleet-planner/route-preview",
                "/api/fleet-planner/global-plan",
            ]
            responses = [
                self.client.post(endpoint, headers=auth_headers(), json={"x": endpoint})
                for endpoint in endpoints
            ]
            history_response = self.client.post(
                "/api/fleet-planner/history",
                headers=auth_headers(),
                json={"title": "save"},
            )

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(history_response.status_code, 201)
        self.assertEqual(calls[-1], ("fleet-history", {"title": "save"}, "admin@example.com"))


    def test_core_job_post_routes_delegate_to_legacy_workflows(self) -> None:
        calls: list[tuple[str, Any]] = []
        store = FakeJobStore()

        def build_config(config_payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(("build-config", config_payload))
            return {"built": config_payload}

        def run_planner(prepared_payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
            calls.append(("compute", {"prepared": prepared_payload, "config": config}))
            return {"ok": True, "config": config}

        def preview_handler(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(("preview", payload))
            return {"source_label": payload.get("file_name", "upload.xlsx")}

        def submit_handler(payload: dict[str, Any], user_email: str) -> dict[str, Any]:
            calls.append(("submit", {"payload": payload, "user_email": user_email}))
            return {"job": {"job_id": "submitted"}, "owner_email": user_email}

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
            JOB_STORE=store,
            _build_planner_config=build_config,
            run_backend_planner_with_prepared_data=run_planner,
            _handle_workbook_preview=preview_handler,
            _handle_workbook_submit=submit_handler,
            _spawn_job_worker=lambda job_id: {"worker_pid": 321, "job_id": job_id},
            _cancel_job=lambda job_id: {"job_id": job_id, "status": "canceled"},
        ):
            compute_response = self.client.post(
                "/api/compute",
                headers=auth_headers(),
                json={"config": {"target_duration": 60}, "prepared_payload": {"stops": []}},
            )
            preview_response = self.client.post(
                "/api/workbooks/preview",
                headers=auth_headers(),
                json={"file_name": "routes.xlsx"},
            )
            submit_response = self.client.post(
                "/api/workbooks/submit",
                headers=auth_headers(),
                json={"file_name": "routes.xlsx", "job_custom_name": "June test"},
            )
            job_response = self.client.post(
                "/api/jobs",
                headers=auth_headers(),
                json={
                    "config": {"market": "CN"},
                    "prepared_payload": {"stop_count": 2},
                    "metadata": {"job_name": "Direct job"},
                },
            )
            cancel_response = self.client.post(
                "/api/jobs/new-job/cancel", headers=auth_headers()
            )

        self.assertEqual(compute_response.status_code, 200)
        self.assertEqual(compute_response.json()["config"], {"built": {"target_duration": 60}})
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.json()["source_label"], "routes.xlsx")
        self.assertEqual(submit_response.status_code, 202)
        self.assertEqual(submit_response.json()["owner_email"], "admin@example.com")
        self.assertEqual(job_response.status_code, 202)
        self.assertEqual(job_response.json()["job_id"], "new-job")
        self.assertEqual(job_response.json()["worker_pid"], 321)
        self.assertEqual(store.created[0]["owner_email"], "admin@example.com")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json(), {"job_id": "new-job", "status": "canceled"})
        self.assertEqual(calls[0], ("build-config", {"target_duration": 60}))

    def test_ai_audit_generates_dual_language_reports_for_kr_jobs_and_uses_cache(self) -> None:
        store = FakeJobStore()
        store.records["kr-job"] = {
            "job_id": "kr-job",
            "owner_email": "admin@example.com",
            "status": "succeeded",
            "metadata": {"country": "KR"},
            "result": {},
        }
        generated: list[tuple[str, bool, dict[str, Any] | None]] = []

        def generate_report(
            record: dict[str, Any], *, force: bool = False, language: str = "English"
        ) -> dict[str, Any]:
            prior_report = record.get("ai_audit_report")
            generated.append((language, force, prior_report if isinstance(prior_report, dict) else None))
            return {"language": language, "summary": f"{language} report"}

        with patched_backend(
            SERVICE_TOKEN="secret",
            ADMIN_EMAILS={"admin@example.com"},
            JOB_STORE=store,
            generate_ai_audit_report=generate_report,
            utc_now_iso=lambda: "2026-06-21T00:00:00Z",
        ):
            response = self.client.post(
                "/api/jobs/kr-job/ai-audit",
                headers=auth_headers(),
                json={"language": "ko"},
            )
            cached_response = self.client.post(
                "/api/jobs/kr-job/ai-audit",
                headers=auth_headers(),
                json={"language": "en"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ai_audit_status"], "succeeded")
        self.assertEqual(payload["ai_audit_report"]["language"], "Korean")
        self.assertEqual(set(payload["ai_audit_reports"].keys()), {"en", "ko"})
        self.assertEqual([item[0] for item in generated], ["English", "Korean"])
        self.assertEqual(store.records["kr-job"]["ai_audit_status"], "succeeded")
        self.assertEqual(cached_response.status_code, 200)
        self.assertTrue(cached_response.json()["cached"])
        self.assertEqual(len(generated), 2)


    def test_worker_termination_uses_taskkill_on_windows(self) -> None:
        with mock.patch.object(backend_service, "_process_is_alive", return_value=True), \
            mock.patch.object(backend_service.os, "name", "nt"), \
            mock.patch.object(backend_service.subprocess, "run") as run_mock:
            backend_service._terminate_worker_process(1234)

        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0], ["taskkill", "/PID", "1234", "/T", "/F"])

    def test_worker_termination_falls_back_when_sigkill_is_unavailable(self) -> None:
        calls: list[tuple[int, int]] = []

        def fake_kill(pid: int, kill_signal: int) -> None:
            calls.append((pid, kill_signal))

        with mock.patch.object(backend_service, "_process_is_alive", return_value=True), \
            mock.patch.object(backend_service.os, "name", "posix"), \
            mock.patch.object(backend_service.signal, "SIGKILL", None, create=True), \
            mock.patch.object(backend_service.os, "kill", side_effect=fake_kill):
            backend_service._terminate_worker_process(4321)

        self.assertEqual(calls, [(4321, backend_service.signal.SIGTERM)])


if __name__ == "__main__":
    unittest.main()
