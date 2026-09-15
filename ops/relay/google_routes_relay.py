"""Private, authenticated Compute Routes relay with an independent egress cap."""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import math
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
from google_routes_transport import ENDPOINT, FIELDS
from quota_store_sqlite import SqliteQuotaStore

PROVIDER = "google_routes_relay"
COUNTER = "compute_routes_pro"
MAX_BODY_BYTES = 16384


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) != {"budget_id", "request"}:
        raise ValueError("invalid_envelope")
    budget = payload["budget_id"]
    if not isinstance(budget, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", budget):
        raise ValueError("invalid_budget_id")
    body = payload["request"]
    fields = {"origin", "destination", "intermediates", "travelMode",
              "routingPreference", "departureTime", "optimizeWaypointOrder", "polylineEncoding"}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError("invalid_route_fields")
    if (body["travelMode"] != "DRIVE" or body["routingPreference"] != "TRAFFIC_AWARE_OPTIMAL"
            or body["optimizeWaypointOrder"] is not False or body["polylineEncoding"] != "GEO_JSON_LINESTRING"):
        raise ValueError("invalid_route_policy")
    intermediates = body["intermediates"]
    if not isinstance(intermediates, list) or len(intermediates) > 25:
        raise ValueError("invalid_waypoints")
    for point in [body["origin"], *intermediates, body["destination"]]:
        if not isinstance(point, dict) or set(point) != {"location"}:
            raise ValueError("invalid_waypoint")
        loc = point["location"]
        if not isinstance(loc, dict) or set(loc) != {"latLng"}:
            raise ValueError("invalid_location")
        coords = loc["latLng"]
        if not isinstance(coords, dict) or set(coords) != {"latitude", "longitude"}:
            raise ValueError("invalid_coordinates")
        for name, limit in (("latitude", 90), ("longitude", 180)):
            number = coords[name]
            if type(number) not in {float, int} or not math.isfinite(number) or abs(number) > limit:
                raise ValueError("invalid_coordinates")
    departure = datetime.fromisoformat(body["departureTime"].replace("Z", "+00:00"))
    if departure.tzinfo is None or departure <= datetime.now(timezone.utc):
        raise ValueError("invalid_departure")
    return budget, body


class RelayConfig:
    def __init__(self):
        self.token = os.environ.get("BRP_GOOGLE_ROUTES_RELAY_TOKEN", "").strip()
        self.key = os.environ.get("BRP_GOOGLE_ROUTES_API_KEY", "").strip()
        path = os.environ.get("BRP_GOOGLE_ROUTES_RELAY_QUOTA_DB", "").strip()
        if not self.token or not self.key or not path:
            raise RuntimeError("Routes relay requires a key, token and dedicated quota path")
        self.store = SqliteQuotaStore(path)
        self.limit = int(os.environ.get("BRP_GOOGLE_ROUTES_RELAY_CAMPAIGN_LIMIT", "500"))
        if not 1 <= self.limit <= 500:
            raise RuntimeError("Routes relay campaign limit must be 1..500")

    def forward(self, budget, body):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        periods = [("task", budget, 200), ("day", now.date().isoformat(), 500),
                   ("month", now.strftime("%Y-%m"), 500), ("campaign", "google-final-pilot-v1", self.limit)]
        self.store.reserve_rate_limit("google-final-routes", 2.0)
        self.store.reserve_usage(PROVIDER, COUNTER, periods, sku_estimate=COUNTER)
        succeeded = False
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(ENDPOINT, json=body, headers={
                    "X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS},
                    timeout=(5, 25), allow_redirects=False)
            if response.status_code != 200:
                # Do not reflect provider messages, request details or credentials.
                return JSONResponse({"error": "google_upstream_rejected"}, status_code=response.status_code
                                    if 400 <= response.status_code < 600 else 502)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid_response")
            succeeded = True
            return JSONResponse(payload)
        except (requests.RequestException, ValueError):
            return JSONResponse({"error": "google_upstream_unavailable"}, status_code=502)
        finally:
            self.store.mark_usage_result(PROVIDER, COUNTER, [(kind, key) for kind, key, _ in periods],
                                         succeeded=succeeded)


def create_app(config=None):
    api = FastAPI(title="BRP Google Routes Relay", docs_url=None, redoc_url=None, openapi_url=None)
    def current():
        if not hasattr(api.state, "config"):
            api.state.config = config or RelayConfig()
        return api.state.config

    @api.get("/health")
    def health():
        try:
            current()
        except RuntimeError:
            return JSONResponse({"ok": False, "service": "google-routes-relay"}, status_code=503)
        return {"ok": True, "service": "google-routes-relay"}

    @api.post("/compute-routes")
    async def compute(request: Request, authorization: str = Header(default="")):
        cfg = current()
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + cfg.token).encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=403)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BODY_BYTES:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            import json
            budget, body = validate_request(json.loads(data))
        except (ValueError, TypeError, AttributeError, KeyError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        # Requests and SQLite rate reservations are blocking; never block the ASGI loop.
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(cfg.forward, budget, body)
        except RuntimeError:
            return JSONResponse({"error": "relay_quota_exhausted"}, status_code=429)

    return api


app = create_app()
