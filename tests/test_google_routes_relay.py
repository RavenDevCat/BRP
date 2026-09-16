"""No-network relay and deployed-client contract tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops/relay"))
import google_routes_relay as relay
import google_routes_transport as transport
import google_final_validation as google
from test_google_final_validation import response, body_points


@pytest.fixture
def envelope():
    point = lambda lat, lng: {"location": {"latLng": {"latitude": lat, "longitude": lng}}}
    return {"budget_id": "acceptance", "request": {
        "origin": point(31.2304, 121.4737), "destination": point(31.2240, 121.4800),
        "intermediates": [], "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "departureTime": (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
        "optimizeWaypointOrder": False, "polylineEncoding": "GEO_JSON_LINESTRING"}}

@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_TOKEN", "test-relay-token")
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_API_KEY", "test-google-key")
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_QUOTA_DB", str(tmp_path / "relay.sqlite"))
    cfg = relay.RelayConfig()
    monkeypatch.setattr(cfg.store, "reserve_rate_limit", lambda *args: None)
    return cfg

def usage(cfg):
    return cfg.store.get_usage(relay.PROVIDER, relay.COUNTER, "campaign", "google-final-pilot-v1")

def auth():
    return {"Authorization": "Bearer test-relay-token"}

def fake_session(monkeypatch, call):
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            assert self.trust_env is False
            return call(url, kwargs)
    monkeypatch.setattr(requests, "Session", Session)

def test_relay_forwards_native_response_and_charges(cfg, envelope, monkeypatch):
    calls = []
    def upstream(url, kwargs):
        calls.append(kwargs)
        assert url == transport.ENDPOINT
        assert kwargs["headers"] == {"X-Goog-Api-Key": cfg.key, "X-Goog-FieldMask": transport.FIELDS}
        assert kwargs["json"] == envelope["request"]
        assert kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=200, json=lambda: response(body_points(kwargs["json"])))
    fake_session(monkeypatch, upstream)
    api = TestClient(relay.create_app(cfg))
    result = api.post("/compute-routes", json=envelope, headers=auth())
    assert result.status_code == 200
    assert len(google.parse_response(result.json(), body_points(envelope["request"]))) == 1
    assert len(calls) == 1 and usage(cfg)["succeeded"] == 1
    assert "test-google-key" not in result.text
    assert "test-relay-token" not in api.get("/health").text

@pytest.mark.parametrize("case", ["no_auth", "bad_auth", "url", "mode", "order", "fields", "count", "nan", "past", "naive", "oversized", "budget"])
def test_invalid_request_never_calls_google(cfg, envelope, monkeypatch, case):
    def forbidden(*args): raise AssertionError("Invalid request reached Google")
    fake_session(monkeypatch, forbidden)
    headers = auth()
    if case == "no_auth": headers = {}
    if case == "bad_auth": headers["Authorization"] = "Bearer wrong"
    if case == "url": envelope["request"]["url"] = "https://unexpected.invalid"
    if case == "mode": envelope["request"]["travelMode"] = "TWO_WHEELER"
    if case == "order": envelope["request"]["optimizeWaypointOrder"] = True
    if case == "fields": envelope["request"]["fields"] = "*"
    if case == "count": envelope["request"]["intermediates"] = [envelope["request"]["origin"]]*26
    if case == "nan": envelope["request"]["origin"]["location"]["latLng"]["latitude"] = "nan"
    if case == "past": envelope["request"]["departureTime"] = "2000-01-01T00:00:00Z"
    if case == "naive": envelope["request"]["departureTime"] = "2030-01-01T00:00:00"
    if case == "oversized": envelope["extra"] = "x"*20000
    if case == "budget": envelope["budget_id"] = "not/a/budget"
    result = TestClient(relay.create_app(cfg)).post("/compute-routes", json=envelope, headers=headers)
    assert result.status_code in {400, 403, 413}
    assert usage(cfg).get("attempted", 0) == 0

@pytest.mark.parametrize("status", [403, 429, 500, 302, "timeout", "bad_json"])
def test_failures_charged_once_without_sensitive_errors(cfg, envelope, monkeypatch, status):
    calls = []
    def upstream(*args):
        calls.append(1)
        if status == "timeout": raise requests.Timeout("test-google-key")
        def data(): raise ValueError("test-google-key")
        return SimpleNamespace(status_code=200 if status == "bad_json" else status, json=data)
    fake_session(monkeypatch, upstream)
    result = TestClient(relay.create_app(cfg)).post("/compute-routes", json=envelope, headers=auth())
    assert result.status_code >= 400 and "test-google-key" not in result.text
    assert usage(cfg)["failed"] == 1 and usage(cfg)["attempted"] == 1 and len(calls) == 1

def test_relay_quota_persists_across_restart(cfg, envelope, monkeypatch):
    from google_routes_quota import MONTHLY_LIMIT
    from zoneinfo import ZoneInfo
    month = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    cfg.store.reserve_usage(relay.PROVIDER, relay.COUNTER, [("month", month, MONTHLY_LIMIT)], count=MONTHLY_LIMIT)
    restarted = relay.RelayConfig()
    monkeypatch.setattr(restarted.store, "reserve_rate_limit", lambda *args: None)
    fake_session(monkeypatch, lambda *args: pytest.fail("Exhausted month reached Google"))
    result = TestClient(relay.create_app(restarted)).post("/compute-routes", json=envelope, headers=auth())
    assert result.status_code == 429
    assert restarted.store.get_usage(relay.PROVIDER, relay.COUNTER, "month", month)["attempted"] == MONTHLY_LIMIT

def test_client_relay_no_google_key_or_proxy_leak(monkeypatch, envelope):
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_URL", "http://127.0.0.1:8813")
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_TOKEN", "test-relay-token")
    monkeypatch.delenv("BRP_GOOGLE_ROUTES_API_KEY", raising=False)
    def outbound(url, kwargs):
        assert url == "http://127.0.0.1:8813/compute-routes"
        assert kwargs["headers"] == auth()
        assert kwargs["json"] == envelope
        assert kwargs["allow_redirects"] is False
        return "native-result"
    fake_session(monkeypatch, outbound)
    assert transport.post_routes(envelope["request"], envelope["budget_id"]) == "native-result"

@pytest.mark.parametrize("url", ["http://example.com:8813", "http://8.8.8.8:8813", "file:///tmp/key", "https://user:pass@example.com:443", "https://example.com:443/path", "https://example.com:443?key=value"])
def test_invalid_relay_configuration_fails_closed(monkeypatch, url):
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_URL", url)
    monkeypatch.setenv("BRP_GOOGLE_FINAL_ENABLED", "true")
    monkeypatch.setenv("BRP_GOOGLE_FINAL_DATA_USE_APPROVED", "true")
    assert google.availability()["reason"] == "google_relay_configuration_invalid"

def test_missing_relay_token_does_not_fallback_to_direct(monkeypatch, envelope):
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_URL", "http://127.0.0.1:8813")
    monkeypatch.delenv("BRP_GOOGLE_ROUTES_RELAY_TOKEN", raising=False)
    with pytest.raises(ValueError, match="token_missing"):
        transport.post_routes(envelope["request"], "budget")

def test_direct_transport_preserved(monkeypatch, envelope):
    monkeypatch.delenv("BRP_GOOGLE_ROUTES_RELAY_URL", raising=False)
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_API_KEY", "direct-test-key")
    calls = []
    def post(url, **kwargs):
        calls.append(1)
        assert url == transport.ENDPOINT
        assert kwargs["headers"]["X-Goog-Api-Key"] == "direct-test-key"
        return "direct"
    monkeypatch.setattr(requests, "post", post)
    assert transport.post_routes(envelope["request"], "budget") == "direct"
    assert len(calls) == 1
