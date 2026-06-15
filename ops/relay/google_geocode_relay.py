from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT_DIR / "ops" / "env" / "local.env"
DEFAULT_USAGE_PATH = ROOT_DIR / "state" / "google_geocode_relay_usage.json"
GOOGLE_GEOCODE_BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_ROUTES_BASE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.legs.duration,routes.legs.distanceMeters"
ALLOWED_PARAM_KEYS = {"address", "language", "components", "region"}
GOOGLE_SUCCESS_STATUSES = {"OK", "ZERO_RESULTS"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key] = value


def parse_bool(value: str, *, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_bangkok_market_country(country: str) -> bool:
    normalized = " ".join(country.strip().lower().split())
    return normalized in {
        "bangkok",
        "bk",
        "th",
        "thai",
        "thailand",
        "bangkok market",
        "ประเทศไทย",
        "ไทย",
    }


def current_day_key(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def current_month_key(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m")


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class UsageCounter:
    def __init__(self, path: Path, daily_limit: int, monthly_limit: int, timezone_name: str) -> None:
        self.path = path
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.timezone_name = timezone_name

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"days": {}, "months": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"days": {}, "months": {}}
        except Exception:
            return {"days": {}, "months": {}}

    def _save_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    @staticmethod
    def _coerce_count(value: Any) -> int:
        try:
            return max(0, int(value))
        except Exception:
            return 0

    def reserve(self) -> dict[str, Any]:
        day_key = current_day_key(self.timezone_name)
        month_key = current_month_key(self.timezone_name)
        with file_lock(self.path):
            payload = self._load_unlocked()
            days = payload.setdefault("days", {})
            months = payload.setdefault("months", {})
            day_used = self._coerce_count(days.get(day_key))
            month_used = self._coerce_count(months.get(month_key))
            if self.daily_limit > 0 and day_used >= self.daily_limit:
                raise RuntimeError(f"Daily relay usage limit reached ({self.daily_limit:,}).")
            if self.monthly_limit > 0 and month_used >= self.monthly_limit:
                raise RuntimeError(f"Monthly relay usage limit reached ({self.monthly_limit:,}).")
            days[day_key] = day_used + 1
            months[month_key] = month_used + 1
            payload["updated_at"] = datetime.now(ZoneInfo(self.timezone_name)).isoformat()
            self._save_unlocked(payload)
            return {
                "day_key": day_key,
                "day_used": day_used + 1,
                "daily_limit": self.daily_limit,
                "month_key": month_key,
                "month_used": month_used + 1,
                "monthly_limit": self.monthly_limit,
            }

    def snapshot(self) -> dict[str, Any]:
        day_key = current_day_key(self.timezone_name)
        month_key = current_month_key(self.timezone_name)
        with file_lock(self.path):
            payload = self._load_unlocked()
        days = payload.get("days") if isinstance(payload.get("days"), dict) else {}
        months = payload.get("months") if isinstance(payload.get("months"), dict) else {}
        return {
            "day_key": day_key,
            "day_used": self._coerce_count(days.get(day_key)),
            "daily_limit": self.daily_limit,
            "month_key": month_key,
            "month_used": self._coerce_count(months.get(month_key)),
            "monthly_limit": self.monthly_limit,
            "usage_path": str(self.path),
        }


class QpsGate:
    def __init__(self, max_qps: float) -> None:
        self.min_interval = 1.0 / max(0.1, max_qps)
        self.lock = threading.Lock()
        self.last_call_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait_for = self.min_interval - (now - self.last_call_at)
            if wait_for > 0:
                time.sleep(wait_for)
            self.last_call_at = time.monotonic()


class RelayConfig:
    def __init__(self) -> None:
        self.api_key = (
            os.environ.get("GOOGLE_GEOCODE_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        ).strip()
        self.routes_api_key = (
            os.environ.get("GOOGLE_ROUTES_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or self.api_key
            or ""
        ).strip()
        self.token = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TOKEN", "").strip()
        self.base_url = os.environ.get("GOOGLE_GEOCODE_BASE_URL", GOOGLE_GEOCODE_BASE_URL).strip()
        self.routes_base_url = os.environ.get("GOOGLE_ROUTES_BASE_URL", GOOGLE_ROUTES_BASE_URL).strip()
        self.timeout_seconds = float(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_UPSTREAM_TIMEOUT_SECONDS", "8") or 8)
        self.allow_non_bangkok = parse_bool(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_ALLOW_NON_BK", "false"))
        self.timezone_name = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
        usage_path = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH", "").strip()
        self.usage_path = Path(usage_path).expanduser() if usage_path else DEFAULT_USAGE_PATH
        self.daily_limit = int(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_DAILY_LIMIT", "1000") or 1000)
        self.monthly_limit = int(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_MONTHLY_LIMIT", "10000") or 10000)
        self.max_qps = float(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_MAX_QPS", "2.8") or 2.8)
        self.usage = UsageCounter(self.usage_path, self.daily_limit, self.monthly_limit, self.timezone_name)
        self.qps_gate = QpsGate(self.max_qps)

    def health_payload(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key and self.token),
            "service": "google-geocode-relay",
            "api_key_configured": bool(self.api_key),
            "routes_api_key_configured": bool(self.routes_api_key),
            "token_configured": bool(self.token),
            "allow_non_bangkok": self.allow_non_bangkok,
            "usage": self.usage.snapshot(),
        }


def response_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def sanitize_params(raw_params: Any) -> dict[str, str]:
    if not isinstance(raw_params, dict):
        raise ValueError("params must be an object.")
    params: dict[str, str] = {}
    for key, value in raw_params.items():
        key_text = str(key).strip()
        if key_text not in ALLOWED_PARAM_KEYS:
            continue
        if value is None:
            continue
        value_text = str(value).strip()
        if value_text:
            params[key_text] = value_text
    if not params.get("address"):
        raise ValueError("params.address is required.")
    return params


def parse_google_duration_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return max(0.0, float(text))
    except Exception:
        return 0.0


def sanitize_route_points(raw_points: Any) -> list[dict[str, float]]:
    if not isinstance(raw_points, list):
        raise ValueError("points must be an array.")
    if len(raw_points) < 2:
        raise ValueError("at least two route points are required.")
    if len(raw_points) > 27:
        raise ValueError("at most 27 route points are allowed.")
    points: list[dict[str, float]] = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict):
            raise ValueError(f"point {index} must be an object.")
        lat = float(point.get("lat"))
        lon = float(point.get("lon"))
        if not (5.0 <= lat <= 22.5 and 95.0 <= lon <= 107.5):
            raise ValueError(f"point {index} is outside the Thailand/Bangkok relay bounds.")
        points.append({"lat": lat, "lon": lon})
    return points


def google_routes_waypoint(point: dict[str, float]) -> dict[str, Any]:
    return {
        "location": {
            "latLng": {
                "latitude": point["lat"],
                "longitude": point["lon"],
            }
        }
    }


def call_google_routes(
    config: RelayConfig,
    points: list[dict[str, float]],
    *,
    departure_time: str = "",
    routing_preference: str = "TRAFFIC_AWARE_OPTIMAL",
) -> dict[str, Any]:
    config.qps_gate.wait()
    usage = config.usage.reserve()
    body: dict[str, Any] = {
        "origin": google_routes_waypoint(points[0]),
        "destination": google_routes_waypoint(points[-1]),
        "travelMode": "DRIVE",
        "routingPreference": routing_preference or "TRAFFIC_AWARE_OPTIMAL",
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    if len(points) > 2:
        body["intermediates"] = [google_routes_waypoint(point) for point in points[1:-1]]
    if departure_time:
        body["departureTime"] = departure_time
    request = urllib.request.Request(
        config.routes_base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": config.routes_api_key,
            "X-Goog-FieldMask": GOOGLE_ROUTES_FIELD_MASK,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not routes:
        raise RuntimeError("Google Routes returned no routes.")
    route = routes[0]
    legs = []
    for leg in route.get("legs") or []:
        legs.append(
            {
                "duration_s": parse_google_duration_seconds(leg.get("duration")),
                "distance_m": float(leg.get("distanceMeters") or 0.0),
            }
        )
    return {
        "duration_s": parse_google_duration_seconds(route.get("duration")),
        "distance_m": float(route.get("distanceMeters") or 0.0),
        "legs": legs,
        "relay_usage": usage,
        "provider": "google_routes",
        "routing_preference": routing_preference or "TRAFFIC_AWARE_OPTIMAL",
    }


def call_google(config: RelayConfig, params: dict[str, str]) -> dict[str, Any]:
    config.qps_gate.wait()
    usage = config.usage.reserve()
    query = urllib.parse.urlencode({**params, "key": config.api_key})
    request_url = f"{config.base_url}?{query}"
    request = urllib.request.Request(request_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        body = response.read()
    payload = json.loads(body.decode("utf-8"))
    if isinstance(payload, dict):
        payload.setdefault("relay_usage", usage)
    return payload


class GoogleGeocodeRelayHandler(BaseHTTPRequestHandler):
    server_version = "BRPGoogleGeocodeRelay/1.0"

    @property
    def config(self) -> RelayConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = response_bytes(payload)
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/health":
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self.write_json(HTTPStatus.OK, self.config.health_payload())

    def do_POST(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        if request_path not in {"/geocode", "/routes"}:
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            if request_path == "/routes":
                self.handle_routes()
                return
            self.handle_geocode()
        except ValueError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except PermissionError as exc:
            self.write_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except RuntimeError as exc:
            self.write_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": str(exc)})
        except Exception as exc:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": f"upstream Google request failed: {exc}"})

    def authenticate(self) -> None:
        config = self.config
        if not config.token:
            raise ValueError("Relay token is not configured.")
        auth_header = self.headers.get("Authorization", "")
        expected = f"Bearer {config.token}"
        if auth_header != expected:
            raise PermissionError("invalid bearer token")

    def read_json_body(self, max_size: int = 32768) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or "0")
        if content_length <= 0 or content_length > max_size:
            raise ValueError("invalid request body size")
        payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object.")
        return payload

    def handle_routes(self) -> None:
        config = self.config
        if not config.routes_api_key:
            raise ValueError("Google Routes API key is not configured.")
        self.authenticate()
        payload = self.read_json_body(max_size=65536)
        country = str(payload.get("country", "")).strip()
        if not config.allow_non_bangkok and not is_bangkok_market_country(country):
            raise ValueError("relay is restricted to Bangkok/Thailand route requests.")
        points = sanitize_route_points(payload.get("points"))
        routing_preference = str(payload.get("routing_preference") or "TRAFFIC_AWARE_OPTIMAL").strip()
        if routing_preference not in {"TRAFFIC_AWARE", "TRAFFIC_AWARE_OPTIMAL"}:
            routing_preference = "TRAFFIC_AWARE_OPTIMAL"
        departure_time = str(payload.get("departure_time") or "").strip()
        result = call_google_routes(
            config,
            points,
            departure_time=departure_time,
            routing_preference=routing_preference,
        )
        self.write_json(HTTPStatus.OK, result)

    def handle_geocode(self) -> None:
        config = self.config
        if not config.api_key:
            raise ValueError("Google geocoding API key is not configured.")
        self.authenticate()
        payload = self.read_json_body()
        country = str(payload.get("country", "")).strip()
        if not config.allow_non_bangkok and not is_bangkok_market_country(country):
            raise ValueError("relay is restricted to Bangkok/Thailand geocoding requests.")
        params = sanitize_params(payload.get("params"))
        result = call_google(config, params)
        if not isinstance(result, dict):
            raise ValueError("Google returned a non-object payload.")
        status = str(result.get("status", "")).strip().upper()
        if status and status not in GOOGLE_SUCCESS_STATUSES:
            self.write_json(HTTPStatus.OK, result)
            return
        self.write_json(HTTPStatus.OK, result)


class RelayServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: RelayConfig) -> None:
        super().__init__(server_address, GoogleGeocodeRelayHandler)
        self.config = config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BRP Google geocode relay for temporary Bangkok routing.")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(Path(args.env_file).expanduser())
    host = args.host or os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_PORT", "8811") or 8811)
    config = RelayConfig()
    server = RelayServer((host, port), config)
    print(
        f"BRP Google geocode relay listening on http://{host}:{port} "
        f"(api_key_configured={bool(config.api_key)}, token_configured={bool(config.token)})",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
