from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

try:
    from . import backend_service
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    import backend_service  # type: ignore


class BackendHttpError(Exception):
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload.get("error") or payload))


@dataclass(frozen=True)
class UserContext:
    email: str
    is_admin: bool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The scheduler is idempotent in backend_service. Keep uvicorn workers at 1
    # until the job queue and file-backed stores are explicitly multi-worker safe.
    backend_service._start_job_scheduler()
    yield


app = FastAPI(
    title="BRP Backend API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


def _json_response(status_code: int, payload: dict[str, Any] | list[Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=backend_service._json_safe(payload),
    )


def _bytes_response(
    status_code: int,
    body: bytes,
    *,
    content_type: str,
    filename: str | None = None,
    inline: bool = True,
    cache_control: str = "no-store",
) -> Response:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
    }
    if filename:
        disposition = "inline" if inline else "attachment"
        headers["Content-Disposition"] = (
            f"{disposition}; filename*=UTF-8''{quote(filename)}"
        )
    return Response(
        content=body,
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )


def _redirect_response(location: str, status_code: int = 302) -> RedirectResponse:
    response = RedirectResponse(url=location, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _current_user_email_from_request(request: Request) -> str:
    headers = request.headers
    if backend_service.AUTH_PROVIDER == "local":
        return (
            backend_service._normalize_email(headers.get("X-BRP-User-Email"))
            or backend_service.DEV_USER_EMAIL
        )
    return (
        backend_service._normalize_email(headers.get("X-BRP-User-Email"))
        or backend_service._normalize_email(
            headers.get("Cf-Access-Authenticated-User-Email")
        )
        or backend_service.DEV_USER_EMAIL
    )


def require_authorized_request(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    if not backend_service.SERVICE_TOKEN:
        return
    expected = f"Bearer {backend_service.SERVICE_TOKEN}"
    if str(authorization or "").strip() != expected:
        raise BackendHttpError(401, {"error": "Unauthorized backend request."})


def current_user_context(request: Request) -> UserContext:
    user_email = _current_user_email_from_request(request)
    return UserContext(
        email=user_email,
        is_admin=backend_service._is_admin_email(user_email),
    )


def require_admin_context(
    _authorized: None = Depends(require_authorized_request),
    context: UserContext = Depends(current_user_context),
) -> UserContext:
    if not context.is_admin:
        raise BackendHttpError(
            403,
            {"error": "This backend endpoint is only available to admins."},
        )
    return context


def _api_route(method: str, path: str, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        route_kwargs = {"response_model": None, **kwargs}
        app.add_api_route(path, func, methods=[method], **route_kwargs)
        app.add_api_route(f"/api{path}", func, methods=[method], **route_kwargs)
        return func

    return decorator


@app.exception_handler(BackendHttpError)
def backend_http_error_handler(
    _request: Request, exc: BackendHttpError
) -> JSONResponse:
    return _json_response(exc.status_code, exc.payload)


@app.exception_handler(Exception)
def backend_unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _json_response(
        500,
        {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        },
    )


@_api_route("GET", "/health")
def health() -> JSONResponse:
    return _json_response(200, {"status": "ok"})


@_api_route("GET", "/auth/config", dependencies=[Depends(require_authorized_request)])
def auth_config() -> JSONResponse:
    return _json_response(200, backend_service._auth_config_payload())


@_api_route("GET", "/auth/login", dependencies=[Depends(require_authorized_request)])
def auth_login() -> Response:
    if backend_service.AUTH_PROVIDER == "microsoft_sso_pending":
        return _json_response(
            501,
            {
                "error": "Microsoft SSO is not configured yet.",
                "auth": backend_service._auth_config_payload(),
            },
        )
    return _redirect_response(backend_service._auth_login_url())


@_api_route("GET", "/auth/logout", dependencies=[Depends(require_authorized_request)])
def auth_logout() -> RedirectResponse:
    return _redirect_response(backend_service._auth_logout_url())


@_api_route("GET", "/me", dependencies=[Depends(require_authorized_request)])
def current_user(context: UserContext = Depends(current_user_context)) -> JSONResponse:
    return _json_response(
        200,
        {
            "email": context.email,
            "is_admin": context.is_admin,
            "auth_mode": backend_service.AUTH_PROVIDER,
            "auth": backend_service._auth_config_payload(),
        },
    )


@_api_route(
    "GET", "/google-geocode-usage", dependencies=[Depends(require_authorized_request)]
)
def google_geocode_usage() -> JSONResponse:
    return _json_response(200, backend_service._google_geocode_usage_payload())


@_api_route(
    "GET", "/deployment-features", dependencies=[Depends(require_authorized_request)]
)
def deployment_features() -> JSONResponse:
    return _json_response(200, backend_service._deployment_features_payload())


@_api_route("GET", "/osrm-manager/status")
def osrm_manager_status(
    context: UserContext = Depends(require_admin_context),
) -> JSONResponse:
    _ = context
    return _json_response(200, backend_service._osrm_manager_status_payload())


@_api_route("GET", "/traffic-rollout/status")
def traffic_rollout_status(
    request: Request,
    context: UserContext = Depends(require_admin_context),
) -> JSONResponse:
    _ = context
    return _json_response(
        200,
        backend_service._traffic_rollout_status_payload(dict(request.query_params)),
    )


@_api_route("GET", "/workbooks/template", dependencies=[Depends(require_authorized_request)])
def workbook_template() -> Response:
    return _bytes_response(
        200,
        backend_service.build_excel_template_bytes(),
        content_type=backend_service.WORKBOOK_CONTENT_TYPE,
        filename="brp_planning_template.xlsx",
        inline=False,
    )


@_api_route(
    "GET",
    "/fleet-planner/demand-template",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_demand_template() -> Response:
    demand_input = backend_service._client_module("demand_input")
    return _bytes_response(
        200,
        demand_input.build_demand_template_workbook_bytes(),
        content_type=backend_service.WORKBOOK_CONTENT_TYPE,
        filename="brp_demand_template.xlsx",
        inline=False,
    )


@_api_route(
    "GET",
    "/fleet-planner/vehicle-catalog",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_vehicle_catalog(request: Request) -> JSONResponse:
    return _json_response(
        200,
        backend_service._handle_fleet_planner_vehicle_catalog(dict(request.query_params)),
    )
