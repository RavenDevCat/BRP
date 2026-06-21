from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "apps" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from quota_store_sqlite import SqliteQuotaStore  # noqa: E402


DEFAULT_ENV_PATH = ROOT_DIR / "ops" / "env" / "local.env"
DEFAULT_USAGE_PATH = ROOT_DIR / "state" / "google_geocode_relay_usage.json"
GOOGLE_GEOCODE_BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ALLOWED_PARAM_KEYS = {"address", "language", "components", "region"}
GOOGLE_SUCCESS_STATUSES = {"OK", "ZERO_RESULTS"}
RELAY_USAGE_PROVIDER = "google_geocode_relay"
RELAY_USAGE_COUNTER = "geocode"
RELAY_USAGE_SKU_ESTIMATE = "google_geocoding"
RELAY_RATE_LIMIT_NAME = "google-geocode-relay-upstream"


class GeocodeRequest(BaseModel):
    country: str = ""
    params: dict[str, Any]


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


class RelayUsageCounter:
    def __init__(self, path: Path, daily_limit: int, monthly_limit: int, timezone_name: str) -> None:
        self.path = path
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.timezone_name = timezone_name
        self.store = SqliteQuotaStore()

    def _migrate_legacy_usage(self) -> None:
        self.store.migrate_nested_period_usage(
            source_path=self.path,
            provider=RELAY_USAGE_PROVIDER,
            counter=RELAY_USAGE_COUNTER,
            day_key="days",
            month_key="months",
            provider_label="google-geocode-relay",
            sku_estimate=RELAY_USAGE_SKU_ESTIMATE,
        )

    def _usage_count(self, period_type: str, period_key: str) -> int:
        usage = self.store.get_usage(RELAY_USAGE_PROVIDER, RELAY_USAGE_COUNTER, period_type, period_key)
        return int(usage.get("attempted", usage.get("used", 0)) or 0)

    def reserve(self) -> dict[str, Any]:
        self._migrate_legacy_usage()
        day_key = current_day_key(self.timezone_name)
        month_key = current_month_key(self.timezone_name)
        day_used = self._usage_count("day", day_key)
        month_used = self._usage_count("month", month_key)
        if self.daily_limit > 0 and day_used >= self.daily_limit:
            raise RuntimeError(f"Daily relay usage limit reached ({self.daily_limit:,}).")
        if self.monthly_limit > 0 and month_used >= self.monthly_limit:
            raise RuntimeError(f"Monthly relay usage limit reached ({self.monthly_limit:,}).")
        result = self.store.reserve_usage(
            RELAY_USAGE_PROVIDER,
            RELAY_USAGE_COUNTER,
            [("day", day_key, self.daily_limit), ("month", month_key, self.monthly_limit)],
            count=1,
            provider_label="google-geocode-relay",
            sku_estimate=RELAY_USAGE_SKU_ESTIMATE,
        )
        return {
            "day_key": day_key,
            "day_used": int(result.get("day", {}).get("attempted", day_used + 1) or 0),
            "daily_limit": self.daily_limit,
            "month_key": month_key,
            "month_used": int(result.get("month", {}).get("attempted", month_used + 1) or 0),
            "monthly_limit": self.monthly_limit,
        }

    def mark_result(self, *, succeeded: bool) -> None:
        self._migrate_legacy_usage()
        self.store.mark_usage_result(
            RELAY_USAGE_PROVIDER,
            RELAY_USAGE_COUNTER,
            [("day", current_day_key(self.timezone_name)), ("month", current_month_key(self.timezone_name))],
            succeeded=succeeded,
            provider_label="google-geocode-relay",
            sku_estimate=RELAY_USAGE_SKU_ESTIMATE,
        )

    def snapshot(self) -> dict[str, Any]:
        self._migrate_legacy_usage()
        day_key = current_day_key(self.timezone_name)
        month_key = current_month_key(self.timezone_name)
        return {
            "day_key": day_key,
            "day_used": self._usage_count("day", day_key),
            "daily_limit": self.daily_limit,
            "month_key": month_key,
            "month_used": self._usage_count("month", month_key),
            "monthly_limit": self.monthly_limit,
            "usage_path": str(self.path),
            "store_path": str(self.store.db_path),
        }


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
        self.usage = RelayUsageCounter(self.usage_path, self.daily_limit, self.monthly_limit, self.timezone_name)
        self.rate_store = SqliteQuotaStore()

    def health_payload(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key and self.token),
            "service": "google-geocode-relay",
            "api_key_configured": bool(self.api_key),
            "token_configured": bool(self.token),
            "allow_non_bangkok": self.allow_non_bangkok,
            "usage": self.usage.snapshot(),
        }

    def wait_for_rate_limit(self) -> None:
        self.rate_store.reserve_rate_limit(RELAY_RATE_LIMIT_NAME, self.max_qps)


def error_response(status: HTTPStatus, message: str) -> JSONResponse:
    return JSONResponse(status_code=status.value, content={"error": message})


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
    config.wait_for_rate_limit()
    usage = config.usage.reserve()
    try:
        query = urllib.parse.urlencode({**params, "key": config.api_key})
        request_url = f"{config.base_url}?{query}"
        request = urllib.request.Request(request_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read()
        payload = json.loads(body.decode("utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("relay_usage", usage)
        config.usage.mark_result(succeeded=True)
        return payload
    except Exception:
        config.usage.mark_result(succeeded=False)
        raise


def config_for_request(request: Request) -> RelayConfig:
    config = getattr(request.app.state, "relay_config", None)
    if config is None:
        config = RelayConfig()
        request.app.state.relay_config = config
    return config


def create_app(config: RelayConfig | None = None) -> FastAPI:
    api = FastAPI(title="BRP Google Geocode Relay", version="2.0")
    if config is not None:
        api.state.relay_config = config

    @api.get("/health")
    def health(request: Request) -> dict[str, Any]:
        return config_for_request(request).health_payload()

    @api.post("/geocode", response_model=None)
    def geocode(
        payload: GeocodeRequest,
        request: Request,
        authorization: str = Header(default="", alias="Authorization"),
    ) -> Any:
        config = config_for_request(request)
        if not config.api_key:
            return error_response(HTTPStatus.BAD_REQUEST, "Google geocoding API key is not configured.")
        if not config.token:
            return error_response(HTTPStatus.BAD_REQUEST, "Relay token is not configured.")
        if authorization != f"Bearer {config.token}":
            return error_response(HTTPStatus.FORBIDDEN, "invalid bearer token")
        country = str(payload.country or "").strip()
        if not config.allow_non_bangkok and not is_bangkok_market_country(country):
            return error_response(HTTPStatus.BAD_REQUEST, "relay is restricted to Bangkok/Thailand geocoding requests.")
        try:
            params = sanitize_params(payload.params)
        except ValueError as exc:
            return error_response(HTTPStatus.BAD_REQUEST, str(exc))
        try:
            result = call_google(config, params)
        except RuntimeError as exc:
            return error_response(HTTPStatus.TOO_MANY_REQUESTS, str(exc))
        except Exception as exc:
            return error_response(HTTPStatus.BAD_GATEWAY, f"upstream Google geocode request failed: {exc}")
        if not isinstance(result, dict):
            return error_response(HTTPStatus.BAD_REQUEST, "Google returned a non-object payload.")
        return result

    return api


app = create_app()


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
    print(
        f"Starting BRP Google geocode FastAPI relay on http://{host}:{port} "
        f"(token_configured={bool(os.environ.get('BRP_GOOGLE_GEOCODE_RELAY_TOKEN', '').strip())})",
        flush=True,
    )
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
