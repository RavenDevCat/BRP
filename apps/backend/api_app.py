from __future__ import annotations

import traceback
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

try:
    from .api_models import (
        AiAuditRequest,
        ComputeRequest,
        CreateJobRequest,
        FlexiblePayload,
        payload_to_dict,
    )
    from . import backend_service
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from api_models import (  # type: ignore
        AiAuditRequest,
        ComputeRequest,
        CreateJobRequest,
        FlexiblePayload,
        payload_to_dict,
    )
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


def _payload_dict(payload: Any) -> dict[str, Any]:
    return payload_to_dict(payload)


def _query_dict(request: Request) -> dict[str, str]:
    return {str(key): str(value) for key, value in request.query_params.items()}


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


def _job_for_context(job_id: str, context: UserContext) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    job_record = backend_service.JOB_STORE.get_job(normalized_job_id)
    if not job_record:
        raise BackendHttpError(404, {"error": f"Job not found: {normalized_job_id}"})
    if not backend_service._can_access_job(
        job_record, context.email, include_all=context.is_admin
    ):
        raise BackendHttpError(
            403, {"error": f"Job is not available for user: {context.email}"}
        )
    return job_record


def _distance_history_not_found_error(tool_mode: str, run_id: str) -> str:
    if tool_mode == "reference":
        return f"Reference Distance history run not found: {run_id}"
    if tool_mode == "route_cost":
        return f"Route Cost history run not found: {run_id}"
    return f"Distance & Cost history run not found: {run_id}"


def _distance_history_unavailable_error(
    tool_mode: str, run_id: str, user_email: str
) -> str:
    _ = run_id
    if tool_mode == "reference":
        return f"Reference Distance history run is not available for user: {user_email}"
    if tool_mode == "route_cost":
        return f"Route Cost history run is not available for user: {user_email}"
    return f"Distance & Cost history run is not available for user: {user_email}"


def _distance_history_for_context(
    run_id: str, tool_mode: str, context: UserContext
) -> tuple[dict[str, Any], Any]:
    normalized_run_id = str(run_id or "").strip()
    record, store = backend_service._get_distance_history_record(
        normalized_run_id, tool_mode
    )
    if not record or not store:
        raise BackendHttpError(
            404,
            {"error": _distance_history_not_found_error(tool_mode, normalized_run_id)},
        )
    if not backend_service._can_access_job(
        record, context.email, include_all=context.is_admin
    ):
        raise BackendHttpError(
            403,
            {
                "error": _distance_history_unavailable_error(
                    tool_mode, normalized_run_id, context.email
                )
            },
        )
    return record, store


def _fleet_history_for_context(
    run_id: str, context: UserContext
) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    record = backend_service.FLEET_PLANNER_HISTORY_STORE.get(normalized_run_id)
    if not record:
        raise BackendHttpError(
            404, {"error": f"Fleet Planner history run not found: {normalized_run_id}"}
        )
    if not backend_service._can_access_job(
        record, context.email, include_all=context.is_admin
    ):
        raise BackendHttpError(
            403,
            {
                "error": f"Fleet Planner history run is not available for user: {context.email}"
            },
        )
    return record


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


@_api_route(
    "GET",
    "/distance-checker/reference-history",
    dependencies=[Depends(require_authorized_request)],
)
def list_reference_distance_history(
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service._list_distance_history(
                "reference", user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route(
    "GET",
    "/distance-checker/reference-history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def get_reference_distance_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    record, _store = _distance_history_for_context(run_id, "reference", context)
    return _json_response(200, record)


@_api_route(
    "DELETE",
    "/distance-checker/reference-history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def delete_reference_distance_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    _record, store = _distance_history_for_context(run_id, "reference", context)
    store.delete(str(run_id or "").strip())
    return _json_response(200, {"deleted": True, "run_id": str(run_id or "").strip()})


@_api_route(
    "GET",
    "/distance-checker/route-cost-history",
    dependencies=[Depends(require_authorized_request)],
)
def list_route_cost_history(
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service._list_distance_history(
                "route_cost", user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route(
    "GET",
    "/distance-checker/route-cost-history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def get_route_cost_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    record, _store = _distance_history_for_context(run_id, "route_cost", context)
    return _json_response(200, record)


@_api_route(
    "DELETE",
    "/distance-checker/route-cost-history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def delete_route_cost_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    _record, store = _distance_history_for_context(run_id, "route_cost", context)
    store.delete(str(run_id or "").strip())
    return _json_response(200, {"deleted": True, "run_id": str(run_id or "").strip()})


@_api_route(
    "GET",
    "/distance-checker/history",
    dependencies=[Depends(require_authorized_request)],
)
def list_distance_history(
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service._list_distance_history(
                "", user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route(
    "GET",
    "/distance-checker/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def get_distance_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    record, _store = _distance_history_for_context(run_id, "", context)
    return _json_response(200, record)


@_api_route(
    "DELETE",
    "/distance-checker/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def delete_distance_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    _record, store = _distance_history_for_context(run_id, "", context)
    store.delete(str(run_id or "").strip())
    return _json_response(200, {"deleted": True, "run_id": str(run_id or "").strip()})


@_api_route(
    "GET",
    "/fleet-planner/history",
    dependencies=[Depends(require_authorized_request)],
)
def list_fleet_planner_history(
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service.FLEET_PLANNER_HISTORY_STORE.list(
                user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route(
    "GET",
    "/fleet-planner/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def get_fleet_planner_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    record = _fleet_history_for_context(run_id, context)
    return _json_response(200, backend_service._hydrate_fleet_planner_history_record(record))


@_api_route(
    "DELETE",
    "/fleet-planner/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def delete_fleet_planner_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    record = _fleet_history_for_context(run_id, context)
    if bool(record.get("shared_with_all")) and not context.is_admin:
        raise BackendHttpError(
            403,
            {"error": "Shared Fleet Planner seed runs can only be deleted by an admin."},
        )
    normalized_run_id = str(run_id or "").strip()
    backend_service.FLEET_PLANNER_HISTORY_STORE.delete(normalized_run_id)
    return _json_response(200, {"deleted": True, "run_id": normalized_run_id})


@_api_route("GET", "/jobs", dependencies=[Depends(require_authorized_request)])
def list_jobs(context: UserContext = Depends(current_user_context)) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service.JOB_STORE.list_jobs(
                user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route("GET", "/jobs/{job_id}/exports/{export_key}")
def get_job_export(
    job_id: str,
    export_key: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> Response:
    job_record = _job_for_context(job_id, context)
    normalized_export_key = str(export_key or "").strip().lower()
    if normalized_export_key == "free-optimization-template":
        workbook_bytes, export_error = (
            backend_service._build_free_baseline_template_export(job_record)
        )
        filename = f"free_optimization_baseline_{job_id}.xlsx"
    elif normalized_export_key.startswith("scenario-template-"):
        scenario_key = normalized_export_key[len("scenario-template-") :]
        workbook_bytes, export_error = backend_service._build_scenario_template_export(
            job_record, scenario_key
        )
        filename = f"{scenario_key}_template_{job_id}.xlsx"
    elif normalized_export_key == "time-impact" or normalized_export_key.startswith(
        "time-impact-"
    ):
        scenario_key = (
            normalized_export_key[len("time-impact-") :]
            if normalized_export_key.startswith("time-impact-")
            else "original"
        )
        workbook_bytes, export_error = backend_service._build_time_impact_workbook_export(
            job_record, scenario_key
        )
        filename = f"time_impact_{scenario_key}_{job_id}.xlsx"
    else:
        raise BackendHttpError(404, {"error": f"Unknown export: {normalized_export_key}"})
    if export_error or not workbook_bytes:
        raise BackendHttpError(
            404, {"error": export_error or "Export is not available."}
        )
    return _bytes_response(
        200,
        workbook_bytes,
        content_type=backend_service.WORKBOOK_CONTENT_TYPE,
        filename=filename,
        inline=False,
    )


@_api_route("GET", "/jobs/{job_id}/artifacts/{artifact_key}")
def get_job_artifact(
    request: Request,
    job_id: str,
    artifact_key: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> Response:
    job_record = _job_for_context(job_id, context)
    query_params = _query_dict(request)
    if query_params.get("refresh") in {"1", "true", "yes"}:
        job_record = backend_service._rerender_job_map_artifacts(job_id, job_record)
    artifact_path, artifact_error = backend_service._resolve_job_map_artifact(
        job_record, str(artifact_key or "").strip()
    )
    if artifact_error and "file is missing" in artifact_error:
        job_record = backend_service._rerender_job_map_artifacts(job_id, job_record)
        artifact_path, artifact_error = backend_service._resolve_job_map_artifact(
            job_record, str(artifact_key or "").strip()
        )
    if artifact_error or not artifact_path:
        raise BackendHttpError(
            404,
            {"error": artifact_error or f"Artifact not found: {artifact_key}"},
        )
    inline = query_params.get("download") not in {"1", "true", "yes"}
    return _bytes_response(
        200,
        artifact_path.read_bytes(),
        content_type="text/html; charset=utf-8",
        filename=artifact_path.name,
        inline=inline,
    )


@_api_route("GET", "/jobs/{job_id}/map-data/{scenario_key}")
def get_job_map_data(
    job_id: str,
    scenario_key: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    job_record = _job_for_context(job_id, context)
    map_data, map_data_error = backend_service._build_job_map_data(
        job_record, str(scenario_key or "").strip()
    )
    if map_data_error or not map_data:
        raise BackendHttpError(
            404,
            {"error": map_data_error or f"Map data not found: {scenario_key}"},
        )
    return _json_response(200, map_data)


@_api_route("GET", "/jobs/{job_id}/traffic-attribution")
def get_job_traffic_attribution(
    request: Request,
    job_id: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    job_record = _job_for_context(job_id, context)
    query_params = _query_dict(request)
    include_route_evidence = query_params.get("route_evidence") in {
        "1",
        "true",
        "yes",
    }
    include_top_matches = query_params.get("top_matches") in {
        "1",
        "true",
        "yes",
    }
    return _json_response(
        200,
        backend_service._job_traffic_attribution_payload(
            job_record,
            include_route_evidence=include_route_evidence,
            include_top_matches=include_top_matches,
        ),
    )


@_api_route("GET", "/jobs/{job_id}")
def get_job(
    job_id: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    return _json_response(200, _job_for_context(job_id, context))


@_api_route("DELETE", "/jobs/{job_id}")
def delete_job(
    job_id: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    job_record = _job_for_context(job_id, context)
    _ = job_record
    normalized_job_id = str(job_id or "").strip()
    backend_service._cancel_job(normalized_job_id)
    backend_service.JOB_STORE.delete_job(normalized_job_id)
    return _json_response(200, {"deleted": True, "job_id": normalized_job_id})


@_api_route("GET", "/map-tiles/{z}/{x}/{tile_name}")
def get_map_tile(
    z: int,
    x: int,
    tile_name: str,
    _authorized: None = Depends(require_authorized_request),
) -> Response:
    if not str(tile_name or "").endswith(".png"):
        raise BackendHttpError(404, {"error": f"Unknown map tile: {tile_name}"})
    raw_y = str(tile_name)[: -len(".png")]
    try:
        y = int(raw_y)
    except ValueError as exc:
        raise BackendHttpError(404, {"error": f"Unknown map tile: {tile_name}"}) from exc
    tile_body, from_cache = backend_service._load_or_fetch_map_tile(z, x, y)
    return _bytes_response(
        200,
        tile_body,
        content_type="image/png",
        cache_control=(
            "public, max-age=604800, immutable"
            if from_cache
            else "public, max-age=86400"
        ),
    )


@_api_route(
    "POST",
    "/distance-checker/workbook-preview",
    dependencies=[Depends(require_authorized_request)],
)
def distance_workbook_preview(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_distance_workbook_preview(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/distance-checker/reference",
    dependencies=[Depends(require_authorized_request)],
)
def reference_distance_check(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_reference_distance_check(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/distance-checker/route-cost",
    dependencies=[Depends(require_authorized_request)],
)
def current_plan_route_cost(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_current_plan_route_cost(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/distance-checker/history",
    dependencies=[Depends(require_authorized_request)],
)
@_api_route(
    "POST",
    "/distance-checker/reference-history",
    dependencies=[Depends(require_authorized_request)],
)
@_api_route(
    "POST",
    "/distance-checker/route-cost-history",
    dependencies=[Depends(require_authorized_request)],
)
def create_distance_history(
    payload: FlexiblePayload | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        201,
        backend_service._handle_distance_checker_history_create(
            _payload_dict(payload), user_email=context.email
        ),
    )


@_api_route(
    "POST",
    "/fleet-planner/preview",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_preview(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_fleet_planner_preview(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/fleet-planner/geocode",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_geocode(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_fleet_planner_geocode(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/fleet-planner/clusters",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_clusters(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_fleet_planner_clusters(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/fleet-planner/route-preview",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_route_preview(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_fleet_planner_route_preview(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/fleet-planner/global-plan",
    dependencies=[Depends(require_authorized_request)],
)
def fleet_planner_global_plan(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_fleet_planner_global_plan(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/fleet-planner/history",
    dependencies=[Depends(require_authorized_request)],
)
def create_fleet_planner_history(
    payload: FlexiblePayload | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        201,
        backend_service._handle_fleet_planner_history_create(
            _payload_dict(payload), user_email=context.email
        ),
    )


@_api_route(
    "GET",
    "/route-insert-advisor/capabilities",
    dependencies=[Depends(require_authorized_request)],
)
def route_insert_advisor_capabilities() -> JSONResponse:
    return _json_response(
        200,
        {
            "status": "interface_ready",
            "version": 1,
            "proposal_endpoint": "/route-insert-advisor/proposals",
            "mutates_original_plan": False,
            "supported_sources": [
                "audit_job_id",
                "fleet_planner_run_id",
                "workbook",
            ],
            "candidate_checks": [
                "walking_threshold",
                "capacity",
                "stop_limit",
                "time_window",
                "existing_rider_impact",
                "new_rider_time",
                "address_review",
            ],
        },
    )


@_api_route(
    "POST",
    "/route-insert-advisor/proposals",
    dependencies=[Depends(require_authorized_request)],
)
def route_insert_advisor_proposals(
    payload: FlexiblePayload | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    payload_dict = _payload_dict(payload)
    raw_stops = payload_dict.get("new_stops") or payload_dict.get("addresses") or []
    if isinstance(raw_stops, str):
        new_stop_count = len([line for line in raw_stops.splitlines() if line.strip()])
    elif isinstance(raw_stops, list):
        new_stop_count = len(raw_stops)
    else:
        new_stop_count = 0
    return _json_response(
        200,
        {
            "status": "interface_ready",
            "proposal_status": "not_implemented",
            "proposals": [],
            "summary": {
                "new_stop_count": new_stop_count,
                "requested_by": context.email,
                "mutates_original_plan": False,
            },
            "message": "Route Insert Advisor scoring is not enabled yet.",
        },
    )


@_api_route("POST", "/compute", dependencies=[Depends(require_authorized_request)])
def compute(payload: ComputeRequest | None = Body(default=None)) -> JSONResponse:
    payload_dict = _payload_dict(payload)
    config_payload = payload_dict.get("config") or {}
    prepared_payload = payload_dict.get("prepared_payload") or {}
    config = backend_service._build_planner_config(config_payload)
    result = backend_service.run_backend_planner_with_prepared_data(
        prepared_payload, config=config
    )
    return _json_response(200, result)


@_api_route(
    "POST",
    "/workbooks/preview",
    dependencies=[Depends(require_authorized_request)],
)
def workbook_preview(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_workbook_preview(_payload_dict(payload))
    )


@_api_route(
    "POST",
    "/workbooks/submit",
    dependencies=[Depends(require_authorized_request)],
)
def workbook_submit(
    payload: FlexiblePayload | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        202,
        backend_service._handle_workbook_submit(
            _payload_dict(payload), user_email=context.email
        ),
    )


@_api_route(
    "POST",
    "/geocode-cache/clear",
    dependencies=[Depends(require_authorized_request)],
)
def geocode_cache_clear(
    payload: FlexiblePayload | None = Body(default=None),
) -> JSONResponse:
    return _json_response(
        200, backend_service._handle_geocode_cache_clear(_payload_dict(payload))
    )


@_api_route("POST", "/jobs", dependencies=[Depends(require_authorized_request)])
def create_job(
    payload: CreateJobRequest | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    payload_dict = _payload_dict(payload)
    config_payload = payload_dict.get("config") or {}
    prepared_payload = payload_dict.get("prepared_payload") or {}
    metadata = payload_dict.get("metadata") or {}
    summary = backend_service.JOB_STORE.create_job(
        config_payload,
        prepared_payload,
        metadata=metadata,
        owner_email=context.email,
    )
    spawned = backend_service._spawn_job_worker(str(summary["job_id"]))
    if spawned:
        summary["worker_pid"] = spawned.get("worker_pid")
    return _json_response(202, summary)


@_api_route("POST", "/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    normalized_job_id = str(job_id or "").strip()
    _job_for_context(normalized_job_id, context)
    updated = backend_service._cancel_job(normalized_job_id)
    if not updated:
        raise BackendHttpError(404, {"error": f"Job not found: {normalized_job_id}"})
    return _json_response(200, updated)


@_api_route("POST", "/jobs/{job_id}/release")
def release_scheduled_job(
    job_id: str,
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    normalized_job_id = str(job_id or "").strip()
    job_record = _job_for_context(normalized_job_id, context)
    if str(job_record.get("status", "")).strip().lower() != "scheduled":
        raise BackendHttpError(
            409,
            {"error": "Only scheduled jobs can be released manually."},
        )
    updated = backend_service._release_scheduled_job(normalized_job_id)
    if not updated:
        raise BackendHttpError(404, {"error": f"Job not found: {normalized_job_id}"})
    return _json_response(200, updated)


@_api_route("POST", "/jobs/{job_id}/ai-audit")
def generate_job_ai_audit(
    job_id: str,
    payload: AiAuditRequest | None = Body(default=None),
    context: UserContext = Depends(current_user_context),
    _authorized: None = Depends(require_authorized_request),
) -> JSONResponse:
    normalized_job_id = str(job_id or "").strip()
    job_record = _job_for_context(normalized_job_id, context)
    payload_dict = _payload_dict(payload)
    requested_key, requested_language = backend_service._normalize_ai_audit_language(
        str(payload_dict.get("language") or "").strip() or None
    )
    force_ai_audit = bool(payload_dict.get("force"))
    if backend_service._is_korean_ai_audit_job(job_record):
        required_languages = ["English", "Korean"]
    elif requested_key == "zh" or backend_service._is_chinese_ai_audit_job(job_record):
        required_languages = ["English", "Chinese"]
    else:
        required_languages = [requested_language]
    audit_state, audit_record = backend_service.JOB_STORE.begin_ai_audit(
        normalized_job_id,
        force=force_ai_audit,
        required_languages=required_languages,
    )
    if audit_state == "missing" or not audit_record:
        raise BackendHttpError(404, {"error": f"Job not found: {normalized_job_id}"})

    reports_by_language = backend_service._ai_audit_report_map(audit_record)
    selected_report = backend_service._select_ai_audit_report(
        reports_by_language, requested_key
    )
    if audit_state == "cached":
        return _json_response(
            200,
            {
                "job_id": normalized_job_id,
                "ai_audit_status": "succeeded",
                "ai_audit_report": selected_report,
                "ai_audit_reports": reports_by_language,
                "cached": True,
            },
        )
    if audit_state == "running":
        return _json_response(
            202,
            {
                "job_id": normalized_job_id,
                "ai_audit_status": "running",
                "ai_audit_report": selected_report,
                "ai_audit_reports": reports_by_language,
                "message": "AI audit generation is already running for this job.",
            },
        )

    try:
        for language in required_languages:
            language_key, language_label = backend_service._normalize_ai_audit_language(
                language
            )
            if not force_ai_audit and language_key in reports_by_language:
                continue
            report_source_record = deepcopy(audit_record)
            report_source_record["ai_audit_report"] = reports_by_language.get(
                language_key
            )
            reports_by_language[language_key] = backend_service.generate_ai_audit_report(
                report_source_record,
                force=force_ai_audit,
                language=language_label,
            )
    except Exception as exc:
        backend_service.JOB_STORE.update_job(
            normalized_job_id,
            ai_audit_status="failed",
            ai_audit_finished_at=backend_service.utc_now_iso(),
            ai_audit_error=str(exc),
        )
        raise

    selected_report = backend_service._select_ai_audit_report(
        reports_by_language, requested_key
    )
    updated = backend_service.JOB_STORE.update_job(
        normalized_job_id,
        ai_audit_report=selected_report,
        ai_audit_reports=reports_by_language,
        ai_audit_status="succeeded",
        ai_audit_finished_at=backend_service.utc_now_iso(),
        ai_audit_error=None,
    )
    if updated:
        updated["config"] = None
        updated["prepared_payload"] = None
        return _json_response(
            200,
            {
                "job_id": normalized_job_id,
                "ai_audit_status": "succeeded",
                "ai_audit_report": selected_report,
                "ai_audit_reports": reports_by_language,
            },
        )
    raise BackendHttpError(404, {"error": f"Job not found: {normalized_job_id}"})
