from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
sys.path.insert(0, str(ROOT / "apps" / "client"))

import backend_service  # noqa: E402


class UploadAddressReviewGateTests(unittest.TestCase):
    def fake_client_core(self, *, prepared_payload=None, geocode_warnings=None):
        class Runtime:
            GEOCODE_CACHE = {}
            GEOCODE_CACHE_PATH = Path("/tmp/client-cache.json")

            @staticmethod
            def geocode_cache_key(country: str, city: str, address: str) -> str:
                return f"client|{country}|{city}|{address}"

            @staticmethod
            def geocode_cache_lookup_keys(country: str, city: str, address: str) -> list[str]:
                return [f"client|{country}|{city}|{address}", f"legacy-client|{address}"]

            @staticmethod
            def save_json_cache(path: Path, payload: dict[str, object]) -> None:
                Runtime.saved = (path, dict(payload))

        class ClientCore:
            runtime = Runtime

            @staticmethod
            def prepare_client_payload(*_args, **_kwargs):
                return {
                    "prepared_payload": prepared_payload or {"original_points": []},
                    "geocode_warnings": geocode_warnings or [],
                    "excluded_stops": [],
                    "elapsed_seconds": 0.0,
                    "logs": "",
                }

        return ClientCore

    def current_plan(self) -> dict[str, object]:
        return {
            "input_records": [
                {"country": "China", "city": "Shanghai", "address": "Questionable stop", "source_excel_row": 12}
            ],
            "summary": {},
            "fleet": [],
        }

    def test_missing_geocode_blocks_review(self) -> None:
        client_core = self.fake_client_core()
        review = backend_service._build_address_review(
            client_core,
            list(self.current_plan()["input_records"]),
            {"original_points": []},
            [
                {
                    "country": "China",
                    "city": "Shanghai",
                    "address": "Questionable stop",
                    "warning": "Could not resolve address.",
                }
            ],
        )

        self.assertEqual(review["status"], "blocked")
        self.assertEqual(review["blocking_count"], 1)
        self.assertEqual(review["items"][0]["status"], "blocking")
        self.assertEqual(review["items"][0]["source_excel_rows"], "12")

    def test_submit_accepts_coordinate_notice_without_acknowledgement(self) -> None:
        client_core = self.fake_client_core(
            prepared_payload={
                "original_points": [
                    {
                        "requested_country": "China",
                        "requested_city": "Shanghai",
                        "requested_address": "Questionable stop",
                        "provider": "amap",
                        "lat": 31.2,
                        "lng": 121.4,
                        "geocode_status": "needs_review",
                    }
                ]
            },
            geocode_warnings=[
                {
                    "country": "China",
                    "city": "Shanghai",
                    "address": "Questionable stop",
                    "status": "needs_review",
                    "warning": "Resolved coordinate is far from the school.",
                }
            ],
        )
        patches = {
            "_read_current_plan_upload": lambda _payload: (client_core, "routes.xlsx", self.current_plan()),
            "_build_client_planner_config": lambda *_args, **_kwargs: object(),
            "_auto_current_plan_route_budget_details": lambda *_args: {"status": "ready", "minutes": 60},
            "_current_plan_preview_map": lambda *_args: ({}, None),
            "_spawn_job_worker": lambda *_args: None,
        }
        with mock.patch.multiple(backend_service, **patches), mock.patch.object(
            backend_service.JOB_STORE, "create_job", return_value={"job_id": "notice-job"}
        ) as create:
            result = backend_service._handle_workbook_submit({"config": {}}, user_email="user@example.com")
        self.assertEqual(result["job"]["job_id"], "notice-job")
        self.assertEqual(result["address_review"]["review_count"], 1)
        self.assertFalse(result["address_review"]["requires_acknowledgement"])
        self.assertEqual(create.call_args.kwargs["metadata"]["client_prep"]["readiness"]["status"], "ready")

    def test_preview_includes_current_plan_map_when_available(self) -> None:
        client_core = self.fake_client_core(
            prepared_payload={
                "original_points": [
                    {
                        "requested_country": "China",
                        "requested_city": "Shanghai",
                        "requested_address": "Questionable stop",
                        "lat": 31.2,
                        "lng": 121.4,
                    }
                ]
            },
        )
        map_payload = {
            "job_id": "workbook-preview",
            "scenario_key": "current_plan",
            "scenario_name": "Current Plan",
            "routes": [],
            "stops": [],
            "private_links": [],
            "summary": {
                "route_count": 0,
                "stop_count": 0,
                "passenger_count": 0,
                "distance_m": 0,
                "duration_s": 0,
            },
        }
        patches = {
            "_read_current_plan_upload": lambda _payload: (client_core, "routes.xlsx", self.current_plan()),
            "_build_client_planner_config": lambda *_args, **_kwargs: object(),
            "_auto_current_plan_route_budget_details": lambda *_args, **_kwargs: None,
            "_current_plan_preview_map": lambda *_args, **_kwargs: (map_payload, None),
        }

        with mock.patch.multiple(backend_service, **patches):
            preview = backend_service._workbook_preview_response({"config": {}})

        self.assertEqual(preview["current_plan_map"], map_payload)
        self.assertIsNone(preview["current_plan_map_error"])

    def test_cache_clear_removes_client_and_backend_entries(self) -> None:
        client_core = self.fake_client_core()
        client_core.runtime.GEOCODE_CACHE = {
            "client|China|Shanghai|Bad cached stop": {"requested_address": "Bad cached stop"},
            "keep-client": {"requested_address": "Other stop"},
        }
        backend_cache = {
            "China|Shanghai|Bad cached stop": {"address": "Bad cached stop"},
            "keep-backend": {"address": "Other stop"},
        }
        backend_saved: list[dict[str, object]] = []

        planner = SimpleNamespace(
            GEOCODE_CACHE=backend_cache,
            GEOCODE_CACHE_PATH=Path("/tmp/backend-cache.json"),
            geocode_cache_key=lambda country, city, address: f"{country}|{city}|{address}",
            save_json_cache=lambda _path, payload: backend_saved.append(dict(payload)),
        )

        with mock.patch.object(backend_service, "_client_core_module", return_value=client_core), mock.patch.object(
            backend_service, "load_legacy_planner", return_value=planner
        ):
            result = backend_service._handle_geocode_cache_clear(
                {"country": "China", "city": "Shanghai", "address": "Bad cached stop"}
            )

        self.assertEqual(result["cleared"], 2)
        self.assertNotIn("client|China|Shanghai|Bad cached stop", client_core.runtime.GEOCODE_CACHE)
        self.assertNotIn("China|Shanghai|Bad cached stop", backend_cache)
        self.assertIn("keep-client", client_core.runtime.GEOCODE_CACHE)
        self.assertIn("keep-backend", backend_cache)
        self.assertTrue(backend_saved)


@pytest.mark.parametrize("lat,lng", [
    (None, 121.4), ("invalid", 121.4), (float("nan"), 121.4),
    (31.2, float("inf")), (91, 121.4), (31.2, 181), (0, 0), (True, 121.4),
])
def test_present_but_invalid_coordinate_is_blocked(lat, lng):
    fixture = UploadAddressReviewGateTests()
    point = {**fixture.current_plan()["input_records"][0], "lat": lat, "lng": lng,
             "geocode_status": "needs_review"}
    review = backend_service._build_address_review(
        fixture.fake_client_core(), fixture.current_plan()["input_records"],
        {"original_points": [point]}, [],
    )
    assert review["blocking_count"] == 1
    assert review["review_count"] == 0
    assert backend_service._workbook_solver_readiness(
        address_review=review, acknowledged=True)["status"] == "invalid"


if __name__ == "__main__":
    unittest.main()
