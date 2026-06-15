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
        self.token = os.environ.get("BRP_GOOGLE_GEOCODE_RELAY_TOKEN", "").strip()
        self.base_url = os.environ.get("GOOGLE_GEOCODE_BASE_URL", GOOGLE_GEOCODE_BASE_URL).strip()
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
        if urllib.parse.urlparse(self.path).path != "/geocode":
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            self.handle_geocode()
        except ValueError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except PermissionError as exc:
            self.write_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except RuntimeError as exc:
            self.write_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": str(exc)})
        except Exception as exc:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": f"upstream Google geocode request failed: {exc}"})

    def handle_geocode(self) -> None:
        config = self.config
        if not config.api_key:
            raise ValueError("Google geocoding API key is not configured.")
        if not config.token:
            raise ValueError("Relay token is not configured.")
        auth_header = self.headers.get("Authorization", "")
        expected = f"Bearer {config.token}"
        if auth_header != expected:
            raise PermissionError("invalid bearer token")
        content_length = int(self.headers.get("Content-Length") or "0")
        if content_length <= 0 or content_length > 32768:
            raise ValueError("invalid request body size")
        body = self.rfile.read(content_length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object.")
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
