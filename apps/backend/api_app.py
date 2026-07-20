from __future__ import annotations

import traceback
import math
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass
from itertools import permutations
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
    from .operations_review import build_operations_review
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from api_models import (  # type: ignore
        AiAuditRequest,
        ComputeRequest,
        CreateJobRequest,
        FlexiblePayload,
        payload_to_dict,
    )
    import backend_service  # type: ignore
    from operations_review import build_operations_review  # type: ignore


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


def _route_insert_history_for_context(
    run_id: str, context: UserContext
) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    record = backend_service.ROUTE_INSERT_ADVISOR_HISTORY_STORE.get(
        normalized_run_id
    )
    if not record:
        raise BackendHttpError(
            404,
            {
                "error": f"Route Insert Advisor history run not found: {normalized_run_id}"
            },
        )
    if not context.is_admin and backend_service._normalize_email(
        record.get("owner_email")
    ) != backend_service._normalize_email(context.email):
        raise BackendHttpError(
            403,
            {
                "error": f"Route Insert Advisor history run is not available for user: {context.email}"
            },
        )
    return record


HISTORY_GROUP_SCOPES = {
    "route_audit",
    "fleet_planner",
    "distance_reference",
    "distance_route_cost",
    "route_insert_advisor",
}


def _history_entries_for_scope(
    scope: str, context: UserContext
) -> list[dict[str, Any]]:
    if scope not in HISTORY_GROUP_SCOPES:
        raise BackendHttpError(404, {"error": f"Unknown history scope: {scope}"})
    if scope == "route_audit":
        return backend_service.JOB_STORE.list_jobs(
            user_email=context.email, include_all=context.is_admin
        )
    if scope == "fleet_planner":
        return backend_service.FLEET_PLANNER_HISTORY_STORE.list(
            user_email=context.email, include_all=context.is_admin
        )
    if scope == "distance_reference":
        return backend_service._list_distance_history(
            "reference", user_email=context.email, include_all=context.is_admin
        )
    if scope == "distance_route_cost":
        return backend_service._list_distance_history(
            "route_cost", user_email=context.email, include_all=context.is_admin
        )
    if scope == "route_insert_advisor":
        return backend_service.ROUTE_INSERT_ADVISOR_HISTORY_STORE.list(
            user_email=context.email, include_all=context.is_admin
        )
    return []


def _history_item_ids_for_scope(scope: str, context: UserContext) -> set[str]:
    id_key = "job_id" if scope == "route_audit" else "run_id"
    return {
        str(entry.get(id_key) or "").strip()
        for entry in _history_entries_for_scope(scope, context)
        if str(entry.get(id_key) or "").strip()
    }


def _history_group_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise BackendHttpError(400, {"error": "History group name is required."})
    if len(name) > 80:
        raise BackendHttpError(
            400, {"error": "History group name must be 80 characters or fewer."}
        )
    return name


def _history_group_item_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise BackendHttpError(400, {"error": "History item_ids must be a list."})
    item_ids = list(
        dict.fromkeys(
            str(item_id or "").strip()
            for item_id in value
            if str(item_id or "").strip()
        )
    )
    if not item_ids:
        raise BackendHttpError(
            400, {"error": "Select at least one history item to group."}
        )
    if len(item_ids) > 200 or any(len(item_id) > 128 for item_id in item_ids):
        raise BackendHttpError(400, {"error": "Invalid history group selection."})
    return item_ids


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


@_api_route(
    "GET",
    "/history-groups/{scope}",
    dependencies=[Depends(require_authorized_request)],
)
def list_history_groups(
    scope: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    visible_ids = _history_item_ids_for_scope(scope, context)
    groups = backend_service._runtime_sqlite_store().list_history_groups(
        scope, context.email
    )
    return _json_response(
        200,
        {
            "groups": [
                {
                    **group,
                    "item_ids": [
                        item_id
                        for item_id in list(group.get("item_ids") or [])
                        if item_id in visible_ids
                    ],
                }
                for group in groups
            ]
        },
    )


@_api_route(
    "POST",
    "/history-groups/{scope}",
    dependencies=[Depends(require_authorized_request)],
)
def assign_history_group(
    scope: str,
    payload: FlexiblePayload = Body(...),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    body = _payload_dict(payload)
    name = _history_group_name(body.get("name"))
    item_ids = _history_group_item_ids(body.get("item_ids"))
    unavailable = sorted(set(item_ids) - _history_item_ids_for_scope(scope, context))
    if unavailable:
        raise BackendHttpError(
            400,
            {
                "error": "Some selected history items are no longer available.",
                "item_ids": unavailable,
            },
        )
    try:
        group = backend_service._runtime_sqlite_store().assign_history_group(
            scope, context.email, name, item_ids
        )
    except ValueError as exc:
        raise BackendHttpError(400, {"error": str(exc)}) from exc
    return _json_response(200, {"group": group})


@_api_route(
    "PATCH",
    "/history-groups/{scope}/{group_id}",
    dependencies=[Depends(require_authorized_request)],
)
def rename_history_group(
    scope: str,
    group_id: str,
    payload: FlexiblePayload = Body(...),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    if scope not in HISTORY_GROUP_SCOPES:
        raise BackendHttpError(404, {"error": f"Unknown history scope: {scope}"})
    try:
        group = backend_service._runtime_sqlite_store().rename_history_group(
            scope,
            context.email,
            str(group_id or "").strip(),
            _history_group_name(_payload_dict(payload).get("name")),
        )
    except ValueError as exc:
        raise BackendHttpError(400, {"error": str(exc)}) from exc
    if not group:
        raise BackendHttpError(404, {"error": "History group not found."})
    return _json_response(200, {"group": group})


@_api_route("GET", "/osrm-manager/status")
def osrm_manager_status(
    context: UserContext = Depends(require_admin_context),
) -> JSONResponse:
    _ = context
    return _json_response(200, backend_service._osrm_manager_status_payload())


@_api_route("GET", "/provider-status")
def provider_status(
    context: UserContext = Depends(require_admin_context),
) -> JSONResponse:
    _ = context
    return _json_response(200, backend_service._provider_status_payload())


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


@_api_route(
    "GET",
    "/route-insert-advisor/history",
    dependencies=[Depends(require_authorized_request)],
)
def list_route_insert_history(
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    return _json_response(
        200,
        {
            "jobs": backend_service.ROUTE_INSERT_ADVISOR_HISTORY_STORE.list(
                user_email=context.email, include_all=context.is_admin
            )
        },
    )


@_api_route(
    "GET",
    "/route-insert-advisor/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def get_route_insert_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    return _json_response(200, _route_insert_history_for_context(run_id, context))


@_api_route(
    "DELETE",
    "/route-insert-advisor/history/{run_id}",
    dependencies=[Depends(require_authorized_request)],
)
def delete_route_insert_history(
    run_id: str, context: UserContext = Depends(current_user_context)
) -> JSONResponse:
    _route_insert_history_for_context(run_id, context)
    normalized_run_id = str(run_id or "").strip()
    backend_service.ROUTE_INSERT_ADVISOR_HISTORY_STORE.delete(normalized_run_id)
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


@_api_route(
    "POST",
    "/operations-review/preview",
    dependencies=[Depends(require_authorized_request)],
)
def preview_operations_review(
    payload: FlexiblePayload = Body(...),
    context: UserContext = Depends(current_user_context),
) -> JSONResponse:
    job_ids = [
        str(job_id or "").strip()
        for job_id in list(_payload_dict(payload).get("job_ids") or [])
        if str(job_id or "").strip()
    ]
    job_ids = list(dict.fromkeys(job_ids))
    if len(job_ids) < 2:
        raise BackendHttpError(
            400, {"error": "Select at least two jobs for operations review."}
        )
    if len(job_ids) > 14:
        raise BackendHttpError(
            400, {"error": "Operations review supports up to 14 jobs at a time."}
        )
    try:
        review = build_operations_review(
            [_job_for_context(job_id, context) for job_id in job_ids]
        )
    except ValueError as exc:
        raise BackendHttpError(400, {"error": str(exc)}) from exc
    return _json_response(200, review)


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


def _insert_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _insert_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _insert_haversine_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1 = _insert_float(a.get("lat"))
    lng1 = _insert_float(a.get("lng"))
    lat2 = _insert_float(b.get("lat"))
    lng2 = _insert_float(b.get("lng"))
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return 0.0
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    h = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def _insert_stop_inputs(raw_stops: Any) -> list[dict[str, Any]]:
    if isinstance(raw_stops, str):
        return [
            {"address": line.strip(), "passenger_count": 1}
            for line in raw_stops.splitlines()
            if line.strip()
        ]
    if not isinstance(raw_stops, list):
        return []
    stops: list[dict[str, Any]] = []
    for item in raw_stops:
        if isinstance(item, str):
            address = item.strip()
            if address:
                stops.append({"address": address, "passenger_count": 1})
        elif isinstance(item, dict):
            address = str(item.get("address") or item.get("name") or "").strip()
            if address or (_insert_float(item.get("lat")) and _insert_float(item.get("lng"))):
                stops.append(dict(item, address=address))
    return stops


def _insert_geocode_stops(
    stops: list[dict[str, Any]], default_country: str, default_city: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    planner = None
    resolved: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, stop in enumerate(stops):
        lat = _insert_float(stop.get("lat"))
        lng = _insert_float(stop.get("lng"))
        address = str(stop.get("address") or "").strip()
        country = str(stop.get("country") or default_country or "China").strip()
        city = str(stop.get("city") or default_city or "Shanghai").strip()
        riders = max(
            1,
            _insert_int(
                stop.get("passenger_count") or stop.get("riders") or stop.get("students"),
                1,
            ),
        )
        if lat is None or lng is None:
            if not address:
                warnings.append({"index": index, "reason": "missing_address"})
                continue
            try:
                planner = planner or backend_service.load_legacy_planner()
                point = planner.geocode_query(country, city, address)
                lat = _insert_float(point.get("plot_lat") or point.get("lat"))
                lng = _insert_float(point.get("plot_lng") or point.get("lng"))
                address = str(point.get("formatted_address") or address).strip()
            except Exception as exc:
                warnings.append({"index": index, "address": address, "reason": str(exc)})
                continue
        if lat is None or lng is None:
            warnings.append({"index": index, "address": address, "reason": "missing_coordinate"})
            continue
        resolved.append(
            {
                "index": index,
                "address": address,
                "country": country,
                "city": city,
                "lat": lat,
                "lng": lng,
                "passenger_count": riders,
            }
        )
    return resolved, warnings


def _insert_stop_limit(route: dict[str, Any], constraints: dict[str, Any]) -> int | None:
    value = constraints.get("stop_limit")
    if value in (None, "", 0, "0"):
        value = route.get("max_stops")
    limit = _insert_int(value, 0)
    return limit if limit > 0 else None


def _insert_coord_payload(point: dict[str, Any], country: str, city: str) -> dict[str, Any]:
    lat = _insert_float(point.get("lat"))
    lng = _insert_float(point.get("lng"))
    return {
        "address": str(point.get("address") or "").strip(),
        "country": str(point.get("country") or country).strip(),
        "city": str(point.get("city") or city).strip(),
        "lat": lat,
        "lng": lng,
        "plot_lat": lat,
        "plot_lng": lng,
    }


def _refine_insert_proposals_with_osrm(
    proposals: list[dict[str, Any]],
    *,
    country: str,
    city: str,
    limit: int,
) -> None:
    candidates = [
        item
        for item in proposals
        if item.get("type") == "insert_stop" and item.get("insert_after_point") and item.get("insert_before_point")
    ][: max(0, limit)]
    if not candidates:
        return
    planner = backend_service.load_legacy_planner()
    previous_osrm_base_url = getattr(planner, "OSRM_BASE_URL", "")
    try:
        for item in candidates:
            before = _insert_coord_payload(dict(item.get("insert_after_point") or {}), country, city)
            new_stop = _insert_coord_payload(dict(item.get("new_stop") or {}), country, city)
            after = _insert_coord_payload(dict(item.get("insert_before_point") or {}), country, city)
            points = [before, new_stop, after]
            try:
                resolver = getattr(planner, "resolve_osrm_base_url", None)
                if callable(resolver):
                    planner.OSRM_BASE_URL = resolver(points)
                time_matrix, distance_matrix = planner.build_osrm_full_matrix(points)
                delta_s = max(0, int(time_matrix[0][1]) + int(time_matrix[1][2]) - int(time_matrix[0][2]))
                delta_m = max(0, int(distance_matrix[0][1]) + int(distance_matrix[1][2]) - int(distance_matrix[0][2]))
                item["delta_duration_s"] = delta_s
                item["delta_distance_m"] = delta_m
                item["estimated_route_duration_s"] = round(
                    (_insert_float(item.get("base_route_duration_s")) or 0.0) + delta_s
                )
                item["estimated_route_distance_m"] = round(
                    (_insert_float(item.get("base_route_distance_m")) or 0.0) + delta_m
                )
                item["impact_source"] = "osrm"
                item["refined"] = True
                item["score"] = delta_s + (0 if item.get("feasible") else 1_000_000)
            except Exception as exc:
                warnings = list(item.get("warnings") or [])
                warnings.append("osrm_refine_failed")
                item["warnings"] = warnings
                item["impact_source"] = "direct_estimate"
                item["refined"] = False
                item["refine_error"] = str(exc)
    finally:
        if hasattr(planner, "OSRM_BASE_URL"):
            planner.OSRM_BASE_URL = previous_osrm_base_url


def _insert_map_data_with_new_stop_markers(
    map_data: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    marker_by_key: dict[str, dict[str, Any]] = {}
    for proposal in proposals:
        new_stop = dict(proposal.get("new_stop") or {})
        lat = _insert_float(new_stop.get("lat"))
        lng = _insert_float(new_stop.get("lng"))
        if lat is None or lng is None:
            continue
        key = str(new_stop.get("address") or f"{lat:.6f},{lng:.6f}").strip()
        if key in marker_by_key:
            continue
        marker_by_key[key] = {
            "id": f"insert-new-{len(marker_by_key) + 1}",
            "route_id": str(proposal.get("route_id") or ""),
            "route_index": _insert_int(proposal.get("route_index"), 0),
            "order": 10_000 + len(marker_by_key),
            "display_label": f"N{len(marker_by_key) + 1}",
            "address": str(new_stop.get("address") or "New stop"),
            "requested_address": str(new_stop.get("address") or ""),
            "passenger_count": _insert_int(new_stop.get("passenger_count"), 1),
            "is_depot": False,
            "lat": lat,
            "lng": lng,
            "cumulative_duration_s": 0,
            "cumulative_distance_m": 0,
        }

    if not marker_by_key:
        return map_data

    preview = dict(map_data)
    preview["stops"] = [dict(stop) for stop in list(map_data.get("stops") or [])] + list(marker_by_key.values())
    bounds = dict(preview.get("bounds") or {})
    for marker in marker_by_key.values():
        lat = float(marker["lat"])
        lng = float(marker["lng"])
        bounds["min_lat"] = min(float(bounds.get("min_lat", lat)), lat)
        bounds["max_lat"] = max(float(bounds.get("max_lat", lat)), lat)
        bounds["min_lng"] = min(float(bounds.get("min_lng", lng)), lng)
        bounds["max_lng"] = max(float(bounds.get("max_lng", lng)), lng)
    preview["bounds"] = bounds
    return preview


def _insert_new_stop_key(stop: dict[str, Any]) -> str:
    if stop.get("index") is not None:
        return f"index:{stop.get('index')}"
    lat = _insert_float(stop.get("lat"))
    lng = _insert_float(stop.get("lng"))
    return f"{str(stop.get('address') or '').strip()}:{lat}:{lng}"


def _insert_proposal_identity(proposal: dict[str, Any]) -> tuple[Any, ...]:
    return (
        proposal.get("type"),
        proposal.get("route_id"),
        proposal.get("target_stop_order"),
        proposal.get("insert_after_order"),
        proposal.get("insert_before_order"),
    )


def _insert_proposal_sort_key(proposal: dict[str, Any]) -> tuple[int, float]:
    feasible = bool(proposal.get("feasible"))
    if feasible and proposal.get("type") == "walk_to_stop":
        return (0, float(_insert_float(proposal.get("walking_distance_m")) or 0.0))
    return (1 if feasible else 2, float(_insert_float(proposal.get("score")) or 0.0))


def _insert_selection_matches(
    proposal: dict[str, Any], selection: dict[str, Any]
) -> bool:
    return all(
        str(proposal.get(key) if proposal.get(key) is not None else "")
        == str(selection.get(key) if selection.get(key) is not None else "")
        for key in (
            "type",
            "route_id",
            "target_stop_order",
            "insert_after_order",
            "insert_before_order",
        )
    )


def _insert_options_by_stop(
    proposals: list[dict[str, Any]], new_stops: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        _insert_new_stop_key(stop): [] for stop in new_stops
    }
    seen: dict[str, set[tuple[Any, ...]]] = {key: set() for key in grouped}
    for proposal in sorted(proposals, key=_insert_proposal_sort_key):
        key = _insert_new_stop_key(dict(proposal.get("new_stop") or {}))
        identity = _insert_proposal_identity(proposal)
        if key not in grouped or identity in seen[key]:
            continue
        grouped[key].append(proposal)
        seen[key].add(identity)
    return grouped


def _insert_select_joint_plan(
    proposals: list[dict[str, Any]],
    new_stops: list[dict[str, Any]],
    selections: Any = None,
) -> list[dict[str, Any]]:
    options_by_stop = _insert_options_by_stop(proposals, new_stops)
    requested_by_stop = {
        _insert_new_stop_key(dict(item.get("new_stop") or {"index": item.get("new_stop_index")})): item
        for item in list(selections or [])
        if isinstance(item, dict)
    }
    route_state: dict[str, dict[str, int]] = {}
    selected_by_stop: dict[str, dict[str, Any]] = {}
    ordered_stops = sorted(
        new_stops,
        key=lambda stop: (
            0 if _insert_new_stop_key(stop) in requested_by_stop else 1,
            len([item for item in options_by_stop.get(_insert_new_stop_key(stop), []) if item.get("feasible")]),
            _insert_int(stop.get("index"), 0),
        ),
    )

    for stop in ordered_stops:
        stop_key = _insert_new_stop_key(stop)
        options = options_by_stop.get(stop_key, [])
        requested = requested_by_stop.get(stop_key)
        if requested:
            matches = [item for item in options if _insert_selection_matches(item, requested)]
            options = matches or options

        chosen: dict[str, Any] | None = None
        for raw_option in options:
            option = deepcopy(raw_option)
            route_id = str(option.get("route_id") or "")
            state = route_state.setdefault(route_id, {"riders": 0, "stops": 0})
            riders = max(1, _insert_int(dict(option.get("new_stop") or {}).get("passenger_count"), 1))
            adds_stop = 1 if option.get("type") == "insert_stop" else 0
            capacity_before = _insert_int(option.get("capacity_before"), 0)
            capacity_after = capacity_before + state["riders"] + riders
            capacity_limit = _insert_int(option.get("capacity_limit"), 0)
            base_stop_count = _insert_int(option.get("base_stop_count"), 0)
            stop_count_after = base_stop_count + state["stops"] + adds_stop
            stop_limit = _insert_int(option.get("stop_limit"), 0)
            capacity_ok = not capacity_limit or capacity_after <= capacity_limit
            stop_ok = not stop_limit or stop_count_after <= stop_limit
            warnings = list(option.get("warnings") or [])
            if not capacity_ok and "combined_capacity" not in warnings:
                warnings.append("combined_capacity")
            if not stop_ok and "combined_stop_limit" not in warnings:
                warnings.append("combined_stop_limit")
            option.update(
                {
                    "capacity_after": capacity_after,
                    "stop_count_after": stop_count_after,
                    "warnings": warnings,
                    "feasible": bool(option.get("feasible")) and capacity_ok and stop_ok,
                }
            )
            if requested or option["feasible"]:
                chosen = option
                break

        if chosen is None and options:
            chosen = deepcopy(options[0])
            chosen["feasible"] = False
            chosen["warnings"] = list(chosen.get("warnings") or []) + ["combined_constraints"]
        if chosen is None:
            continue
        route_id = str(chosen.get("route_id") or "")
        state = route_state.setdefault(route_id, {"riders": 0, "stops": 0})
        state["riders"] += max(
            1, _insert_int(dict(chosen.get("new_stop") or {}).get("passenger_count"), 1)
        )
        state["stops"] += 1 if chosen.get("type") == "insert_stop" else 0
        selected_by_stop[stop_key] = chosen

    return [
        selected_by_stop[key]
        for key in (_insert_new_stop_key(stop) for stop in new_stops)
        if key in selected_by_stop
    ]


def _insert_reasonable_alternate(
    primary: dict[str, Any], option: dict[str, Any]
) -> bool:
    if not option.get("feasible"):
        return False
    if option.get("type") != primary.get("type"):
        return True
    if option.get("type") == "walk_to_stop":
        primary_m = _insert_float(primary.get("walking_distance_m")) or 0.0
        option_m = _insert_float(option.get("walking_distance_m")) or 0.0
        return option_m <= primary_m + 250.0
    primary_s = _insert_float(primary.get("delta_duration_s")) or 0.0
    option_s = _insert_float(option.get("delta_duration_s")) or 0.0
    return option_s <= primary_s + 300.0


def _insert_build_recommendations(
    proposals: list[dict[str, Any]],
    new_stops: list[dict[str, Any]],
    selected_proposals: list[dict[str, Any]],
    *,
    alternate_limit: int = 2,
) -> list[dict[str, Any]]:
    by_stop = _insert_options_by_stop(proposals, new_stops)
    selected_by_stop = {
        _insert_new_stop_key(dict(item.get("new_stop") or {})): item
        for item in selected_proposals
    }

    recommendations: list[dict[str, Any]] = []
    for stop in new_stops:
        options = sorted(by_stop.get(_insert_new_stop_key(stop), []), key=_insert_proposal_sort_key)
        primary = options[0] if options else None
        selected = selected_by_stop.get(_insert_new_stop_key(stop)) or primary
        alternates: list[dict[str, Any]] = []
        seen = {_insert_proposal_identity(selected)} if selected else set()
        for option in options:
            if selected and not _insert_reasonable_alternate(selected, option):
                continue
            identity = _insert_proposal_identity(option)
            if identity in seen:
                continue
            alternates.append(option)
            seen.add(identity)
            if len(alternates) >= alternate_limit:
                break
        recommendations.append(
            {
                "new_stop": stop,
                "primary": primary,
                "selected": selected,
                "alternates": alternates,
                "option_count": len(options),
            }
        )
    return recommendations


def _insert_selected_signature(
    selected: list[dict[str, Any]],
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    return tuple(
        sorted(
            (
                _insert_new_stop_key(dict(item.get("new_stop") or {})),
                _insert_proposal_identity(item),
            )
            for item in selected
        )
    )


def _insert_scenario_selections(
    proposals: list[dict[str, Any]],
    new_stops: list[dict[str, Any]],
    primary: list[dict[str, Any]],
    *,
    limit: int = 4,
) -> list[list[dict[str, Any]]]:
    if not primary:
        return []
    limit = max(1, limit)
    primary_by_stop = {
        _insert_new_stop_key(dict(item.get("new_stop") or {})): item
        for item in primary
    }
    seen = {_insert_selected_signature(primary)}
    alternates: list[list[dict[str, Any]]] = []
    for recommendation in _insert_build_recommendations(
        proposals, new_stops, primary
    ):
        stop_key = _insert_new_stop_key(
            dict(recommendation.get("new_stop") or {})
        )
        for option in list(recommendation.get("alternates") or []):
            requested = [
                option if key == stop_key else selected
                for key, selected in primary_by_stop.items()
            ]
            candidate = _insert_select_joint_plan(
                proposals, new_stops, requested
            )
            signature = _insert_selected_signature(candidate)
            if not candidate or signature in seen:
                continue
            seen.add(signature)
            alternates.append(candidate)

    alternates.sort(
        key=lambda items: (
            not all(bool(item.get("feasible")) for item in items),
            sum(float(_insert_float(item.get("score")) or 0.0) for item in items),
            str(_insert_selected_signature(items)),
        )
    )
    return [primary, *alternates[: limit - 1]]


def _insert_coord_from_pair(pair: Any) -> tuple[float, float] | None:
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        return None
    lng = _insert_float(pair[0])
    lat = _insert_float(pair[1])
    if lat is None or lng is None:
        return None
    return lng, lat


def _insert_bounds_for_map(map_data: dict[str, Any]) -> dict[str, float] | None:
    coords: list[tuple[float, float]] = []
    for stop in map_data.get("stops") or []:
        lat = _insert_float(dict(stop).get("lat"))
        lng = _insert_float(dict(stop).get("lng"))
        if lat is not None and lng is not None:
            coords.append((lng, lat))
    for collection_name in ("routes", "route_connectors", "private_links"):
        for item in map_data.get(collection_name) or []:
            for pair in dict(item).get("display_geometry") or dict(item).get("geometry") or []:
                coord = _insert_coord_from_pair(pair)
                if coord:
                    coords.append(coord)
    if not coords:
        return None
    lngs = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    return {
        "min_lng": min(lngs),
        "min_lat": min(lats),
        "max_lng": max(lngs),
        "max_lat": max(lats),
    }


def _insert_filtered_map_data(
    map_data: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> dict[str, Any] | None:
    route_ids = {str(item.get("route_id") or "").strip() for item in proposals if item.get("route_id")}
    if not route_ids:
        return None
    routes = [dict(route) for route in map_data.get("routes") or [] if str(dict(route).get("id") or "") in route_ids]
    stops = [dict(stop) for stop in map_data.get("stops") or [] if str(dict(stop).get("route_id") or "") in route_ids]
    connectors = [
        dict(connector)
        for connector in map_data.get("route_connectors") or []
        if str(dict(connector).get("route_id") or "") in route_ids
    ]
    private_links = [
        dict(link)
        for link in map_data.get("private_links") or []
        if str(dict(link).get("pickup_route_id") or "") in route_ids
    ]
    preview = dict(map_data)
    preview["routes"] = routes
    preview["stops"] = stops
    preview["route_connectors"] = connectors
    preview["private_links"] = private_links
    preview["summary"] = {
        "route_count": len(routes),
        "stop_count": len([stop for stop in stops if not stop.get("is_depot")]),
        "passenger_count": sum(_insert_int(stop.get("passenger_count"), 0) for stop in stops),
        "distance_m": sum(_insert_int(route.get("distance_m"), 0) for route in routes),
        "duration_s": sum(_insert_int(route.get("duration_s"), 0) for route in routes),
    }
    preview["bounds"] = _insert_bounds_for_map(preview)
    return _insert_map_data_with_new_stop_markers(preview, proposals)


def _insert_order_segment_actions(
    before: dict[str, Any],
    actions: list[dict[str, Any]],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    if len(actions) < 2:
        return actions

    def path_distance(items: tuple[dict[str, Any], ...]) -> float:
        points = [before, *(dict(item.get("new_stop") or {}) for item in items), after]
        return sum(_insert_haversine_m(points[index], points[index + 1]) for index in range(len(points) - 1))

    if len(actions) <= 6:
        return list(min(permutations(actions), key=path_distance))

    # ponytail: large same-segment batches use nearest-neighbour order; switch to
    # a bounded search only if real insert batches regularly exceed six stops.
    remaining = list(actions)
    ordered: list[dict[str, Any]] = []
    current = before
    while remaining:
        next_action = min(
            remaining,
            key=lambda item: _insert_haversine_m(current, dict(item.get("new_stop") or {})),
        )
        ordered.append(next_action)
        remaining.remove(next_action)
        current = dict(next_action.get("new_stop") or {})
    return ordered


def _insert_route_sequence(
    route_stops: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(route_stops) < 2:
        return [deepcopy(stop) for stop in route_stops]
    actions_by_segment: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for action in actions:
        if action.get("type") != "insert_stop":
            continue
        key = (
            _insert_int(action.get("insert_after_order"), -1),
            _insert_int(action.get("insert_before_order"), -1),
        )
        actions_by_segment.setdefault(key, []).append(action)

    sequence: list[dict[str, Any]] = [deepcopy(route_stops[0])]
    for index in range(len(route_stops) - 1):
        before = route_stops[index]
        after = route_stops[index + 1]
        segment_actions = _insert_order_segment_actions(
            before,
            actions_by_segment.get(
                (
                    _insert_int(before.get("order"), -1),
                    _insert_int(after.get("order"), -1),
                ),
                [],
            ),
            after,
        )
        for action in segment_actions:
            new_stop = dict(action.get("new_stop") or {})
            stop_index = _insert_int(new_stop.get("index"), len(sequence))
            sequence.append(
                {
                    "id": f"insert-new-{stop_index + 1}",
                    "address": str(new_stop.get("address") or "New stop"),
                    "requested_address": str(new_stop.get("address") or ""),
                    "passenger_count": max(1, _insert_int(new_stop.get("passenger_count"), 1)),
                    "is_depot": False,
                    "lat": _insert_float(new_stop.get("lat")),
                    "lng": _insert_float(new_stop.get("lng")),
                    "display_label": f"N{stop_index + 1}",
                    "node_index": -1,
                    "_is_inserted": True,
                }
            )
        sequence.append(deepcopy(after))
    return sequence


def _insert_route_measurement(
    route_points: list[dict[str, Any]],
    country: str,
    cache: dict[tuple[Any, ...], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signature = (
        str(country or "").strip().upper(),
        tuple(
            (
                round(float(_insert_float(point.get("lat")) or 0.0), 6),
                round(float(_insert_float(point.get("lng")) or 0.0), 6),
            )
            for point in route_points
        ),
    )
    if cache is not None and signature in cache:
        return deepcopy(cache[signature])
    planner = backend_service.load_legacy_planner()
    previous_osrm_base_url = getattr(planner, "OSRM_BASE_URL", "")
    osrm_geometry: list[list[float]] = []
    osrm_duration_s = 0.0
    osrm_distance_m = 0.0
    leg_durations_s: list[float] = []
    leg_distances_m: list[float] = []
    warnings: list[str] = []
    try:
        resolver = getattr(planner, "resolve_osrm_base_url", None)
        if callable(resolver):
            planner.OSRM_BASE_URL = resolver(route_points)
        coordinates: list[str] = []
        for point in route_points:
            lat, lng = planner.point_osrm_lat_lng(point, prefer_plot=True)
            coordinates.append(f"{lng:.7f},{lat:.7f}")
        payload = planner.osrm_request_json(
            "route",
            ";".join(coordinates),
            {"overview": "full", "geometries": "geojson", "steps": "false"},
        )
        route = dict((payload.get("routes") or [{}])[0] or {})
        osrm_duration_s = float(route.get("duration", 0.0) or 0.0)
        osrm_distance_m = float(route.get("distance", 0.0) or 0.0)
        osrm_geometry = [
            [float(pair[0]), float(pair[1])]
            for pair in list(dict(route.get("geometry") or {}).get("coordinates") or [])
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        ]
        legs = [dict(item or {}) for item in list(route.get("legs") or [])]
        leg_durations_s = [float(item.get("duration", 0.0) or 0.0) for item in legs]
        leg_distances_m = [float(item.get("distance", 0.0) or 0.0) for item in legs]
    except Exception as exc:
        warnings.append(f"osrm_route_failed:{exc.__class__.__name__}")
        osrm_geometry = [
            [float(point.get("lng") or 0.0), float(point.get("lat") or 0.0)]
            for point in route_points
        ]
        leg_distances_m = [
            _insert_haversine_m(route_points[index], route_points[index + 1])
            for index in range(max(0, len(route_points) - 1))
        ]
        leg_durations_s = [distance / 8.33 for distance in leg_distances_m]
        osrm_distance_m = sum(leg_distances_m)
        osrm_duration_s = sum(leg_durations_s)
    finally:
        if hasattr(planner, "OSRM_BASE_URL"):
            planner.OSRM_BASE_URL = previous_osrm_base_url

    display_geometry: list[list[float]] | None = None
    display_source = "osrm"
    display_message = ""
    display_duration_s: float | None = None
    display_distance_m: float | None = None
    country_key = str(country or "").strip().upper()
    if country_key in {"CHINA", "CN", "中国", "中华人民共和国"}:
        (
            display_geometry,
            display_source,
            display_message,
            display_duration_s,
            display_distance_m,
        ) = backend_service._amap_display_geometry_for_route(
            route_points, list(range(len(route_points)))
        )
        if not str(display_source).startswith("amap") or display_duration_s is None:
            warnings.append("amap_final_validation_unavailable")

    duration_s = display_duration_s if display_duration_s is not None else osrm_duration_s
    distance_m = display_distance_m if display_distance_m is not None else osrm_distance_m
    duration_scale = duration_s / osrm_duration_s if osrm_duration_s > 0 else 1.0
    distance_scale = distance_m / osrm_distance_m if osrm_distance_m > 0 else 1.0
    result = {
        "geometry": osrm_geometry,
        "display_geometry": display_geometry,
        "display_geometry_source": display_source,
        "display_geometry_message": display_message,
        "duration_s": duration_s,
        "distance_m": distance_m,
        "leg_durations_s": [value * duration_scale for value in leg_durations_s],
        "leg_distances_m": [value * distance_scale for value in leg_distances_m],
        "provider_verified": str(display_source).startswith("amap") and display_duration_s is not None,
        "warnings": warnings,
    }
    if cache is not None:
        cache[signature] = deepcopy(result)
    return result


def _insert_clock_minutes(value: Any) -> int | None:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def _insert_time_window_seconds(config: dict[str, Any]) -> float | None:
    start = _insert_clock_minutes(config.get("time_window_start"))
    end = _insert_clock_minutes(config.get("time_window_end"))
    if start is None or end is None:
        return None
    if end <= start:
        end += 24 * 60
    return float((end - start) * 60)


def _insert_build_selected_plan(
    map_data: dict[str, Any],
    selected: list[dict[str, Any]],
    *,
    country: str,
    constraints: dict[str, Any],
    suggested_config: dict[str, Any],
    measurement_cache: dict[tuple[Any, ...], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    route_ids = {str(item.get("route_id") or "") for item in selected if item.get("route_id")}
    if not route_ids:
        return {"status": "unavailable", "feasible": False, "actions": selected}, None

    route_by_id = {str(route.get("id") or ""): dict(route) for route in map_data.get("routes") or []}
    stops_by_route: dict[str, list[dict[str, Any]]] = {}
    for stop in map_data.get("stops") or []:
        route_id = str(dict(stop).get("route_id") or "")
        if route_id in route_ids:
            stops_by_route.setdefault(route_id, []).append(dict(stop))
    for route_stops in stops_by_route.values():
        route_stops.sort(key=lambda item: _insert_int(item.get("order"), 0))

    merged_config = dict(suggested_config)
    merged_config.update({key: value for key, value in constraints.items() if value not in (None, "")})
    window_s = _insert_time_window_seconds(merged_config)
    dwell_s = max(0.0, (_insert_float(merged_config.get("stop_service_minutes")) or 1.0) * 60.0)
    map_routes: list[dict[str, Any]] = []
    map_stops: list[dict[str, Any]] = []
    private_links: list[dict[str, Any]] = []
    route_results: list[dict[str, Any]] = []

    for route_id in sorted(route_ids):
        route = route_by_id.get(route_id)
        route_stops = stops_by_route.get(route_id, [])
        if not route or len(route_stops) < 2:
            continue
        route_actions = [item for item in selected if str(item.get("route_id") or "") == route_id]
        insert_actions = [item for item in route_actions if item.get("type") == "insert_stop"]
        sequence = _insert_route_sequence(route_stops, insert_actions)
        route_points = [
            _insert_coord_payload(stop, country, str(dict(stop).get("city") or ""))
            for stop in sequence
        ]
        if insert_actions:
            base_points = [
                _insert_coord_payload(stop, country, str(dict(stop).get("city") or ""))
                for stop in route_stops
            ]
            base_measurement = _insert_route_measurement(
                base_points, country, measurement_cache
            )
            selected_measurement = _insert_route_measurement(
                route_points, country, measurement_cache
            )
        else:
            original_geometry = list(route.get("display_geometry") or route.get("geometry") or [])
            base_measurement = {
                "geometry": list(route.get("geometry") or []),
                "display_geometry": list(route.get("display_geometry") or []) or None,
                "display_geometry_source": str(route.get("display_geometry_source") or "existing"),
                "display_geometry_message": str(route.get("display_geometry_message") or ""),
                "duration_s": float(route.get("duration_s", 0.0) or 0.0),
                "distance_m": float(route.get("distance_m", 0.0) or 0.0),
                "leg_durations_s": [],
                "leg_distances_m": [],
                "provider_verified": bool(original_geometry),
                "warnings": [],
            }
            selected_measurement = dict(base_measurement)

        base_service_s = float(route.get("stop_service_time_s", 0.0) or 0.0)
        if base_service_s <= 0:
            base_service_s = max(0, _insert_int(route.get("stop_count"), 0)) * dwell_s
        selected_service_s = base_service_s + len(insert_actions) * dwell_s
        base_total_s = float(base_measurement.get("duration_s", 0.0) or 0.0) + base_service_s
        selected_total_s = float(selected_measurement.get("duration_s", 0.0) or 0.0) + selected_service_s
        time_window_ok = window_s is None or selected_total_s <= window_s
        action_ok = all(bool(item.get("feasible")) for item in route_actions)
        provider_required = bool(insert_actions) and str(country or "").strip().upper() in {
            "CHINA",
            "CN",
            "中国",
            "中华人民共和国",
        }
        provider_ok = not provider_required or bool(selected_measurement.get("provider_verified"))
        route_feasible = action_ok and time_window_ok and provider_ok
        load_after = _insert_int(route.get("load"), 0) + sum(
            max(1, _insert_int(dict(item.get("new_stop") or {}).get("passenger_count"), 1))
            for item in route_actions
        )
        route_results.append(
            {
                "route_id": route_id,
                "feasible": route_feasible,
                "base_duration_s": round(base_total_s),
                "selected_duration_s": round(selected_total_s),
                "delta_duration_s": round(selected_total_s - base_total_s),
                "base_distance_m": round(float(base_measurement.get("distance_m", 0.0) or 0.0)),
                "selected_distance_m": round(float(selected_measurement.get("distance_m", 0.0) or 0.0)),
                "delta_distance_m": round(
                    float(selected_measurement.get("distance_m", 0.0) or 0.0)
                    - float(base_measurement.get("distance_m", 0.0) or 0.0)
                ),
                "capacity_before": _insert_int(route.get("load"), 0),
                "capacity_after": load_after,
                "capacity_limit": _insert_int(route.get("bus_capacity"), 0) or None,
                "base_stop_count": _insert_int(route.get("stop_count"), 0),
                "selected_stop_count": _insert_int(route.get("stop_count"), 0) + len(insert_actions),
                "affected_riders": _insert_int(route.get("load"), 0),
                "time_window_ok": time_window_ok,
                "provider_required": provider_required,
                "provider_verified": bool(selected_measurement.get("provider_verified")),
                "provider_source": selected_measurement.get("display_geometry_source") or "osrm",
                "warnings": list(selected_measurement.get("warnings") or []),
            }
        )

        leg_durations = list(selected_measurement.get("leg_durations_s") or [])
        leg_distances = list(selected_measurement.get("leg_distances_m") or [])
        cumulative_duration_s = 0.0
        cumulative_distance_m = 0.0
        stop_ids: list[str] = []
        for order, stop in enumerate(sequence):
            if order > 0:
                if order - 1 < len(leg_durations):
                    cumulative_duration_s += float(leg_durations[order - 1] or 0.0)
                if order - 1 < len(leg_distances):
                    cumulative_distance_m += float(leg_distances[order - 1] or 0.0)
            stop_id = str(stop.get("id") or f"{route_id}:insert:{order}")
            stop_ids.append(stop_id)
            map_stops.append(
                {
                    "id": stop_id,
                    "route_id": route_id,
                    "route_index": _insert_int(route.get("route_index"), 0),
                    "order": order,
                    "display_label": stop.get("display_label"),
                    "node_index": _insert_int(stop.get("node_index"), -1),
                    "address": str(stop.get("address") or ""),
                    "requested_address": str(stop.get("requested_address") or ""),
                    "passenger_count": _insert_int(stop.get("passenger_count"), 0),
                    "is_depot": bool(stop.get("is_depot")),
                    "lat": _insert_float(stop.get("lat")) or 0.0,
                    "lng": _insert_float(stop.get("lng")) or 0.0,
                    "cumulative_duration_s": cumulative_duration_s,
                    "cumulative_distance_m": cumulative_distance_m,
                }
            )

        map_route = dict(route)
        map_route.update(
            {
                "load": load_after,
                "stop_count": _insert_int(route.get("stop_count"), 0) + len(insert_actions),
                "distance_m": float(selected_measurement.get("distance_m", 0.0) or 0.0),
                "duration_s": float(selected_measurement.get("duration_s", 0.0) or 0.0),
                "raw_duration_s": float(selected_measurement.get("duration_s", 0.0) or 0.0),
                "geometry": list(selected_measurement.get("geometry") or []),
                "display_geometry": selected_measurement.get("display_geometry"),
                "display_geometry_source": selected_measurement.get("display_geometry_source") or "osrm",
                "display_geometry_message": selected_measurement.get("display_geometry_message") or "",
                "stop_ids": stop_ids,
            }
        )
        map_routes.append(map_route)

        if insert_actions:
            old_geometry = list(base_measurement.get("display_geometry") or base_measurement.get("geometry") or [])
            if len(old_geometry) >= 2:
                private_links.append(
                    {
                        "id": f"original-route-{route_id}",
                        "access_type": "original_route",
                        "address": "Original route",
                        "pickup_address": route_id,
                        "pickup_route_id": route_id,
                        "drive_time_s": float(base_measurement.get("duration_s", 0.0) or 0.0),
                        "drive_distance_m": float(base_measurement.get("distance_m", 0.0) or 0.0),
                        "geometry": old_geometry,
                    }
                )

        for action in [item for item in route_actions if item.get("type") == "walk_to_stop"]:
            new_stop = dict(action.get("new_stop") or {})
            target_order = _insert_int(action.get("target_stop_order"), -1)
            target = next(
                (stop for stop in route_stops if _insert_int(stop.get("order"), -2) == target_order),
                None,
            )
            if not target:
                continue
            stop_index = _insert_int(new_stop.get("index"), len(map_stops))
            map_stops.append(
                {
                    "id": f"walk-new-{stop_index + 1}",
                    "route_id": f"walk:{route_id}:{stop_index + 1}",
                    "route_index": _insert_int(route.get("route_index"), 0),
                    "order": 0,
                    "display_label": f"N{stop_index + 1}",
                    "node_index": -1,
                    "address": str(new_stop.get("address") or ""),
                    "requested_address": str(new_stop.get("address") or ""),
                    "passenger_count": max(1, _insert_int(new_stop.get("passenger_count"), 1)),
                    "is_depot": False,
                    "lat": _insert_float(new_stop.get("lat")) or 0.0,
                    "lng": _insert_float(new_stop.get("lng")) or 0.0,
                    "cumulative_duration_s": 0,
                    "cumulative_distance_m": 0,
                }
            )
            private_links.append(
                {
                    "id": f"walk-link-{stop_index + 1}",
                    "access_type": "walk_to_stop",
                    "address": str(new_stop.get("address") or ""),
                    "pickup_address": str(target.get("address") or ""),
                    "pickup_route_id": route_id,
                    "drive_time_s": 0,
                    "drive_distance_m": _insert_float(action.get("walking_distance_m")) or 0.0,
                    "geometry": [
                        [_insert_float(new_stop.get("lng")) or 0.0, _insert_float(new_stop.get("lat")) or 0.0],
                        [_insert_float(target.get("lng")) or 0.0, _insert_float(target.get("lat")) or 0.0],
                    ],
                }
            )

    selected_map = dict(map_data)
    selected_map.update(
        {
            "scenario_key": "route_insert_selected",
            "scenario_name": "Selected Insert Plan",
            "routes": map_routes,
            "stops": map_stops,
            "route_connectors": [],
            "private_links": private_links,
            "summary": {
                "route_count": len(map_routes),
                "stop_count": sum(_insert_int(route.get("stop_count"), 0) for route in map_routes),
                "passenger_count": sum(_insert_int(route.get("load"), 0) for route in map_routes),
                "distance_m": sum(float(route.get("distance_m", 0.0) or 0.0) for route in map_routes),
                "duration_s": max([float(route.get("duration_s", 0.0) or 0.0) for route in map_routes] or [0.0]),
            },
        }
    )
    selected_map["bounds"] = _insert_bounds_for_map(selected_map)
    feasible = len(selected) > 0 and all(bool(item.get("feasible")) for item in selected)
    feasible = feasible and all(bool(item.get("feasible")) for item in route_results)
    plan = {
        "status": "ready" if feasible else "needs_review",
        "feasible": feasible,
        "actions": selected,
        "affected_routes": route_results,
        "affected_route_count": len(route_results),
        "inserted_stop_count": sum(1 for item in selected if item.get("type") == "insert_stop"),
        "walking_stop_count": sum(1 for item in selected if item.get("type") == "walk_to_stop"),
        "total_added_duration_s": round(sum(float(item.get("delta_duration_s", 0.0) or 0.0) for item in route_results)),
        "total_added_distance_m": round(sum(float(item.get("delta_distance_m", 0.0) or 0.0) for item in route_results)),
        "provider_verified_route_count": sum(1 for item in route_results if item.get("provider_verified")),
        "time_window_start": merged_config.get("time_window_start"),
        "time_window_end": merged_config.get("time_window_end"),
    }
    return plan, selected_map


def _build_route_insert_proposals(
    job_record: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    source = dict(payload.get("source") or {})
    scenario_key = str(
        payload.get("scenario_key") or source.get("scenario_key") or "current_plan"
    ).strip()
    map_data = dict(payload.get("_map_data") or {})
    map_error = None
    if not map_data:
        map_data, map_error = backend_service._build_job_map_data(job_record, scenario_key)
    if map_error or not map_data:
        raise BackendHttpError(404, {"error": map_error or "Route map data not found."})

    constraints = dict(payload.get("constraints") or {})
    default_country = str(constraints.get("country") or payload.get("country") or "China").strip()
    default_city = str(constraints.get("city") or payload.get("city") or "Shanghai").strip()
    walking_threshold_m = max(0.0, _insert_float(constraints.get("walking_threshold_m")) or 500.0)
    proposal_limit = max(1, min(100, _insert_int(constraints.get("proposal_limit"), 50)))
    refine_top_n = max(0, min(50, _insert_int(constraints.get("refine_top_n"), 25)))

    raw_stops = payload.get("new_stops") or payload.get("addresses") or []
    requested_stops = _insert_stop_inputs(raw_stops)
    new_stops, geocode_warnings = _insert_geocode_stops(
        requested_stops, default_country, default_city
    )

    stops_by_id = {str(stop.get("id")): dict(stop) for stop in map_data.get("stops") or []}
    stops_by_route: dict[str, list[dict[str, Any]]] = {}
    for stop in stops_by_id.values():
        stops_by_route.setdefault(str(stop.get("route_id") or ""), []).append(stop)
    for route_stops in stops_by_route.values():
        route_stops.sort(key=lambda item: _insert_int(item.get("order"), 0))

    proposals: list[dict[str, Any]] = []
    for new_stop in new_stops:
        riders = _insert_int(new_stop.get("passenger_count"), 1)
        for route in list(map_data.get("routes") or []):
            route_id = str(route.get("id") or "").strip()
            route_stops = stops_by_route.get(route_id, [])
            service_stops = [stop for stop in route_stops if not stop.get("is_depot")]
            capacity = _insert_int(route.get("bus_capacity"), 0)
            base_load = _insert_int(route.get("load"), 0)
            load_after = base_load + riders
            capacity_ok = not capacity or load_after <= capacity
            stop_limit = _insert_stop_limit(route, constraints)
            base_stop_count = _insert_int(route.get("stop_count"), len(service_stops))
            stop_count_after = base_stop_count + 1
            stop_ok = stop_limit is None or stop_count_after <= stop_limit
            base_duration_s = round(_insert_float(route.get("duration_s")) or 0.0)
            base_distance_m = round(_insert_float(route.get("distance_m")) or 0.0)

            nearest = min(
                service_stops,
                key=lambda stop: _insert_haversine_m(new_stop, stop),
                default=None,
            )
            if nearest:
                walk_m = _insert_haversine_m(new_stop, nearest)
                if walk_m <= walking_threshold_m:
                    feasible = capacity_ok
                    proposals.append(
                        {
                            "type": "walk_to_stop",
                            "new_stop": new_stop,
                            "route_id": route_id,
                            "route_index": route.get("route_index"),
                            "target_stop_order": nearest.get("order"),
                            "target_stop_address": nearest.get("address"),
                            "walking_distance_m": round(walk_m),
                            "delta_distance_m": 0,
                            "delta_duration_s": 0,
                            "base_route_duration_s": base_duration_s,
                            "estimated_route_duration_s": base_duration_s,
                            "base_route_distance_m": base_distance_m,
                            "estimated_route_distance_m": base_distance_m,
                            "capacity_before": base_load,
                            "capacity_after": load_after,
                            "capacity_limit": capacity or None,
                            "base_stop_count": base_stop_count,
                            "stop_count_after": base_stop_count,
                            "stop_limit": stop_limit,
                            "feasible": feasible,
                            "warnings": [] if feasible else ["capacity"],
                            "score": walk_m,
                        }
                    )

            if len(route_stops) < 2:
                continue
            seconds_per_meter = (
                (_insert_float(route.get("duration_s")) or 0.0)
                / max(1.0, _insert_float(route.get("distance_m")) or 0.0)
            )
            if seconds_per_meter <= 0:
                seconds_per_meter = 180.0 / 1000.0
            route_candidates: list[dict[str, Any]] = []
            for index in range(len(route_stops) - 1):
                before = route_stops[index]
                after = route_stops[index + 1]
                # ponytail: direct-distance delta is only the first-pass ranker; use OSRM/AMap when final-priced inserts are needed.
                delta_m = max(
                    0.0,
                    _insert_haversine_m(before, new_stop)
                    + _insert_haversine_m(new_stop, after)
                    - _insert_haversine_m(before, after),
                )
                delta_s = delta_m * seconds_per_meter
                feasible = capacity_ok and stop_ok
                warnings = []
                if not capacity_ok:
                    warnings.append("capacity")
                if not stop_ok:
                    warnings.append("stop_limit")
                route_candidates.append(
                    {
                        "type": "insert_stop",
                        "new_stop": new_stop,
                        "route_id": route_id,
                        "route_index": route.get("route_index"),
                        "insert_after_order": before.get("order"),
                        "insert_after_address": before.get("address"),
                        "insert_after_point": _insert_coord_payload(before, default_country, default_city),
                        "insert_before_order": after.get("order"),
                        "insert_before_address": after.get("address"),
                        "insert_before_point": _insert_coord_payload(after, default_country, default_city),
                        "delta_distance_m": round(delta_m),
                        "delta_duration_s": round(delta_s),
                        "base_route_duration_s": base_duration_s,
                        "estimated_route_duration_s": round(
                            base_duration_s + delta_s
                        ),
                        "base_route_distance_m": base_distance_m,
                        "estimated_route_distance_m": round(base_distance_m + delta_m),
                        "capacity_before": base_load,
                        "capacity_after": load_after,
                        "capacity_limit": capacity or None,
                        "base_stop_count": base_stop_count,
                        "stop_count_after": stop_count_after,
                        "stop_limit": stop_limit,
                        "feasible": feasible,
                        "warnings": warnings,
                        "impact_source": "direct_estimate",
                        "refined": False,
                        "score": delta_s + (0 if feasible else 1_000_000),
                    }
                )
            proposals.extend(sorted(route_candidates, key=lambda item: item["score"])[:3])

    proposals.sort(key=lambda item: (not bool(item.get("feasible")), float(item.get("score") or 0)))
    _refine_insert_proposals_with_osrm(
        proposals,
        country=default_country,
        city=default_city,
        limit=refine_top_n,
    )
    proposals.sort(key=_insert_proposal_sort_key)
    selected_proposals = _insert_select_joint_plan(
        proposals, new_stops, payload.get("selections")
    )
    scenario_selections = _insert_scenario_selections(
        proposals, new_stops, selected_proposals
    )
    measurement_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    scenarios: list[dict[str, Any]] = []
    for index, scenario_selected in enumerate(scenario_selections):
        scenario_plan, scenario_map = _insert_build_selected_plan(
            map_data,
            scenario_selected,
            country=default_country,
            constraints=constraints,
            suggested_config=dict(payload.get("_suggested_config") or {}),
            measurement_cache=measurement_cache,
        )
        scenarios.append(
            {
                "id": "recommended" if index == 0 else f"alternative_{index}",
                "is_recommended": index == 0,
                "recommendations": _insert_build_recommendations(
                    proposals, new_stops, scenario_selected
                ),
                "selected_plan": scenario_plan,
                "selected_map_data": scenario_map,
            }
        )
    active_scenario = scenarios[0] if scenarios else {
        "recommendations": [],
        "selected_plan": {
            "status": "unavailable",
            "feasible": False,
            "actions": [],
        },
        "selected_map_data": None,
    }
    recommendations = list(active_scenario.get("recommendations") or [])
    returned_proposals: list[dict[str, Any]] = []
    seen_returned: set[tuple[str, tuple[Any, ...]]] = set()
    for recommendation in recommendations:
        for item in [
            recommendation.get("selected"),
            recommendation.get("primary"),
            *(recommendation.get("alternates") or []),
        ]:
            if not isinstance(item, dict):
                continue
            item_key = (
                _insert_new_stop_key(dict(item.get("new_stop") or {})),
                _insert_proposal_identity(item),
            )
            if item_key in seen_returned:
                continue
            returned_proposals.append(item)
            seen_returned.add(item_key)
    selected_plan = dict(active_scenario.get("selected_plan") or {})
    selected_map_data = active_scenario.get("selected_map_data")
    preview_map_data = selected_map_data or _insert_filtered_map_data(
        map_data, selected_proposals
    )
    return {
        "status": "ok",
        "proposal_status": "ready",
        "proposals": returned_proposals,
        "recommendations": recommendations,
        "selected_plan": selected_plan,
        "selected_map_data": selected_map_data,
        "scenarios": scenarios,
        "summary": {
            "source_job_id": job_record.get("job_id"),
            "source_label": payload.get("_source_label") or "",
            "source_kind": payload.get("_source_kind") or "audit_job",
            "scenario_key": scenario_key,
            "new_stop_count": len(new_stops),
            "geocode_warning_count": len(geocode_warnings),
            "proposal_count": len(returned_proposals),
            "candidate_count": len(proposals),
            "recommendation_count": len(recommendations),
            "proposal_limit": proposal_limit,
            "refined_candidate_count": sum(1 for item in returned_proposals if item.get("refined")),
            "refine_top_n": refine_top_n,
            "selected_action_count": len(selected_proposals),
            "selected_plan_status": selected_plan.get("status"),
            "scenario_count": len(scenarios),
            "mutates_original_plan": False,
        },
        "geocode_warnings": geocode_warnings,
        "map_data": preview_map_data,
    }


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
    if str(payload_dict.get("file_base64") or "").strip():
        preview = backend_service._workbook_preview_response(payload_dict)
        map_data = dict(preview.get("current_plan_map") or {})
        if not map_data:
            raise BackendHttpError(
                400,
                {"error": preview.get("current_plan_map_error") or "Workbook map preview is not available."},
            )
        workbook_payload = dict(payload_dict)
        workbook_payload["_map_data"] = map_data
        workbook_payload["_suggested_config"] = dict(preview.get("suggested_config") or {})
        workbook_payload["_source_label"] = preview.get("source_label") or payload_dict.get("file_name") or ""
        workbook_payload["_source_kind"] = "workbook"
        response = _build_route_insert_proposals(
            {"job_id": "workbook-preview"},
            workbook_payload,
        )
        response["summary"]["requested_by"] = context.email
        response["summary"]["workbook_summary"] = dict(preview.get("summary") or {})
    else:
        source = dict(payload_dict.get("source") or {})
        audit_job_id = str(
            source.get("audit_job_id")
            or payload_dict.get("audit_job_id")
            or ""
        ).strip()
        if not audit_job_id:
            raise BackendHttpError(
                400,
                {"error": "Upload a workbook before requesting insert proposals."},
            )
        job_record = _job_for_context(audit_job_id, context)
        response = _build_route_insert_proposals(job_record, payload_dict)
        response["summary"]["requested_by"] = context.email

    try:
        history = backend_service._handle_route_insert_history_create(
            {
                "title": payload_dict.get("history_title"),
                "scenario": {
                    "file_name": payload_dict.get("file_name")
                    or dict(response.get("summary") or {}).get("source_label"),
                    "new_stops": payload_dict.get("new_stops")
                    or payload_dict.get("addresses"),
                    "constraints": dict(payload_dict.get("constraints") or {}),
                    "source": dict(payload_dict.get("source") or {}),
                },
                "route_insert_result": response,
            },
            context.email,
        )
        response["history_job"] = history.get("job")
    except Exception as exc:
        response["history_error"] = str(exc)
    return _json_response(200, response)


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
