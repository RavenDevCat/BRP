"""Routes-only transport; no geocoding, provider fallback or automatic retry."""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

import requests

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELDS = ",".join(("routes.duration", "routes.distanceMeters", "routes.legs.duration",
                   "routes.legs.distanceMeters", "routes.legs.startLocation",
                   "routes.legs.endLocation", "routes.legs.polyline", "fallbackInfo"))
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                          "192.168.0.0/16", "100.64.0.0/10", "::1/128"))


def relay_url(variable="BRP_GOOGLE_ROUTES_RELAY_URL"):
    value = os.environ.get(variable, "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path or not parsed.port):
        raise ValueError("google_relay_url_invalid")
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ValueError("google_relay_requires_tls_or_private_ip") from None
        if not any(address in network for network in PRIVATE_NETWORKS):
            raise ValueError("google_relay_requires_tls_or_private_ip")
    if not os.environ.get("BRP_GOOGLE_ROUTES_RELAY_TOKEN", "").strip():
        raise ValueError("google_relay_token_missing")
    return value


def post_routes(body, budget_id):
    relay = relay_url()
    if relay:
        # Never send a Google key to the relay or inherit unrelated HTTP proxies.
        with requests.Session() as session:
            session.trust_env = False
            return session.post(relay + "/compute-routes",
                json={"budget_id": budget_id, "request": body}, headers={
                    "Authorization": "Bearer " + os.environ["BRP_GOOGLE_ROUTES_RELAY_TOKEN"]},
                timeout=(5, 35), allow_redirects=False)
    return requests.post(ENDPOINT, json=body, headers={
        "X-Goog-Api-Key": os.environ["BRP_GOOGLE_ROUTES_API_KEY"],
        "X-Goog-FieldMask": FIELDS}, timeout=(5, 25), allow_redirects=False)


def post_geocode(params, budget_id):
    relay = relay_url("BRP_GOOGLE_MODE_GEOCODE_RELAY_URL") or relay_url()
    with requests.Session() as session:
        session.trust_env = False
        if relay:
            return session.post(relay + "/geocode", json={"budget_id": budget_id, "request": params},
                headers={"Authorization": "Bearer " + os.environ["BRP_GOOGLE_ROUTES_RELAY_TOKEN"]},
                timeout=(5, 35), allow_redirects=False)
        return session.get("https://maps.googleapis.com/maps/api/geocode/json",
            params={**params, "key": os.environ["BRP_GOOGLE_ROUTES_API_KEY"]},
            timeout=(5, 25), allow_redirects=False)
