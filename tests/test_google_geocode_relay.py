from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "client"))
sys.path.insert(0, str(ROOT / "apps" / "backend"))
sys.path.insert(0, str(ROOT / "ops" / "relay"))

import client_runtime  # noqa: E402
import BusingProblem as planner  # noqa: E402
import google_geocode_relay as relay  # noqa: E402


class MockResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@contextmanager
def patched_module_attrs(module, **values):
    originals = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(module, name, value)


class BangkokGoogleRelayAdapterTests(unittest.TestCase):
    def test_client_runtime_uses_relay_for_bangkok_geocode(self) -> None:
        calls: list[dict] = []
        original_post = client_runtime.requests.post

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return MockResponse({"status": "OK", "results": []})

        try:
            client_runtime.requests.post = fake_post
            with patched_module_attrs(
                client_runtime,
                BK_GEOCODE_MODE="google_relay",
                GOOGLE_GEOCODE_RELAY_URL="http://relay.local/geocode",
                GOOGLE_GEOCODE_RELAY_TOKEN="secret",
                GOOGLE_GEOCODE_RELAY_TIMEOUT_SECONDS=3,
            ):
                payload = client_runtime.google_geocode_request_json(
                    {"address": "Sukhumvit 53", "components": "country:TH"},
                    country="Thailand",
                )
        finally:
            client_runtime.requests.post = original_post

        self.assertEqual(payload["status"], "OK")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "http://relay.local/geocode")
        self.assertEqual(calls[0]["json"]["country"], "Thailand")
        self.assertEqual(calls[0]["json"]["params"]["components"], "country:TH")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(calls[0]["timeout"], 3)

    def test_backend_runtime_uses_relay_for_bangkok_geocode(self) -> None:
        calls: list[dict] = []
        original_post = planner.requests.post

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return MockResponse({"status": "ZERO_RESULTS", "results": []})

        try:
            planner.requests.post = fake_post
            with patched_module_attrs(
                planner,
                BK_GEOCODE_MODE="relay",
                GOOGLE_GEOCODE_RELAY_URL="http://relay.local/geocode",
                GOOGLE_GEOCODE_RELAY_TOKEN="secret",
                GOOGLE_GEOCODE_RELAY_TIMEOUT_SECONDS=4,
            ):
                payload = planner.google_geocode_request_json(
                    {"address": "Bangkok Prep", "components": "country:TH"},
                    country="BK",
                )
        finally:
            planner.requests.post = original_post

        self.assertEqual(payload["status"], "ZERO_RESULTS")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["country"], "BK")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(calls[0]["timeout"], 4)


@contextmanager
def temporary_env(**values):
    old_values = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


class GoogleGeocodeRelayServerTests(unittest.TestCase):
    def test_relay_rejects_missing_token_and_non_bangkok_country(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, temporary_env(
            GOOGLE_GEOCODE_API_KEY="test-key",
            BRP_GOOGLE_GEOCODE_RELAY_TOKEN="secret",
            BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH=str(Path(temp_dir) / "usage.json"),
        ):
            config = relay.RelayConfig()
            server = relay.RelayServer(("127.0.0.1", 0), config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/geocode"
            try:
                missing_token_request = urllib.request.Request(
                    url,
                    data=json.dumps({"country": "Thailand", "params": {"address": "Sukhumvit 53"}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as missing_token_error:
                    urllib.request.urlopen(missing_token_request, timeout=5)
                self.assertEqual(missing_token_error.exception.code, 403)

                non_bangkok_request = urllib.request.Request(
                    url,
                    data=json.dumps({"country": "China", "params": {"address": "Shanghai"}}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer secret",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as non_bangkok_error:
                    urllib.request.urlopen(non_bangkok_request, timeout=5)
                self.assertEqual(non_bangkok_error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()

    def test_relay_passes_allowed_bangkok_request_to_google_adapter(self) -> None:
        calls: list[dict] = []
        original_call_google = relay.call_google

        def fake_call_google(config, params):
            calls.append({"params": params})
            return {"status": "OK", "results": []}

        try:
            relay.call_google = fake_call_google
            with tempfile.TemporaryDirectory() as temp_dir, temporary_env(
                GOOGLE_GEOCODE_API_KEY="test-key",
                BRP_GOOGLE_GEOCODE_RELAY_TOKEN="secret",
                BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH=str(Path(temp_dir) / "usage.json"),
            ):
                config = relay.RelayConfig()
                server = relay.RelayServer(("127.0.0.1", 0), config)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{server.server_address[1]}/geocode"
                try:
                    request = urllib.request.Request(
                        url,
                        data=json.dumps(
                            {
                                "country": "Thailand",
                                "params": {
                                    "address": "Sukhumvit 53",
                                    "components": "country:TH",
                                    "extra": "ignored",
                                },
                            }
                        ).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer secret",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()
        finally:
            relay.call_google = original_call_google

        self.assertEqual(payload["status"], "OK")
        self.assertEqual(calls, [{"params": {"address": "Sukhumvit 53", "components": "country:TH"}}])


if __name__ == "__main__":
    unittest.main()
