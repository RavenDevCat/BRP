# Backend API Inventory

This inventory tracks the FastAPI backend HTTP surface. The old hand-rolled
`backend_service.py` HTTP server has been removed; `backend_service.py` now
holds shared business logic only. Keep existing paths and JSON payloads stable
while continuing product work.

## Migration Guardrails

- The backend runner scripts are FastAPI-only. `BRP_BACKEND_FRAMEWORK` is
  deprecated and is no longer read by the runners.
- Uvicorn must run as a single process until the file-backed job queue is proven
  multi-worker safe. When the worker count is `1`, the runners intentionally
  omit `--workers 1`; this is especially important for the KR Windows runner.
- The React API base remains `/api`; FastAPI also exposes non-prefixed paths for
  parity with internal scripts.
- Business logic remains in existing backend modules; the HTTP layer is the
  FastAPI shell.

## Day 1-8 FastAPI Coverage

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/health` | unauthenticated health check |
| GET | `/api/health` | frontend/proxy health check |
| GET | `/auth/config` | auth metadata |
| GET | `/auth/login` | auth redirect or pending SSO response |
| GET | `/auth/logout` | auth logout redirect |
| GET | `/me` | current user payload |
| GET | `/google-geocode-usage` | Google geocode usage status |
| GET | `/deployment-features` | deployment feature flags |
| GET | `/osrm-manager/status` | admin-only OSRM manager readout |
| GET | `/traffic-rollout/status` | admin-only traffic rollout readout |
| GET | `/workbooks/template` | planning workbook download |
| GET | `/fleet-planner/demand-template` | fleet demand workbook download |
| GET | `/fleet-planner/vehicle-catalog` | fleet vehicle catalog |
| GET | `/jobs` | job history list |
| GET | `/jobs/{job_id}` | job detail |
| DELETE | `/jobs/{job_id}` | job cancel/delete |
| GET | `/jobs/{job_id}/map-data/{scenario_key}` | interactive map data |
| GET | `/jobs/{job_id}/artifacts/{artifact_key}` | legacy HTML map artifact |
| GET | `/jobs/{job_id}/exports/{export_key}` | workbook export |
| GET | `/jobs/{job_id}/traffic-attribution` | traffic attribution report |
| GET | `/map-tiles/{z}/{x}/{y}.png` | cached/proxied OSM tile |
| GET | `/fleet-planner/history` | fleet planner history list |
| GET | `/fleet-planner/history/{run_id}` | fleet planner history detail |
| DELETE | `/fleet-planner/history/{run_id}` | fleet planner history delete |
| GET | `/distance-checker/history` | distance history list |
| GET | `/distance-checker/history/{run_id}` | distance history detail |
| DELETE | `/distance-checker/history/{run_id}` | distance history delete |
| GET | `/distance-checker/reference-history` | reference distance history list |
| GET | `/distance-checker/reference-history/{run_id}` | reference distance history detail |
| DELETE | `/distance-checker/reference-history/{run_id}` | reference distance history delete |
| GET | `/distance-checker/route-cost-history` | route-cost history list |
| GET | `/distance-checker/route-cost-history/{run_id}` | route-cost history detail |
| DELETE | `/distance-checker/route-cost-history/{run_id}` | route-cost history delete |
| POST | `/distance-checker/workbook-preview` | distance workbook preview |
| POST | `/distance-checker/reference` | reference distance check |
| POST | `/distance-checker/route-cost` | route cost calculation |
| POST | `/distance-checker/history` | save distance history |
| POST | `/distance-checker/reference-history` | save reference distance history |
| POST | `/distance-checker/route-cost-history` | save route-cost history |
| POST | `/fleet-planner/preview` | fleet planner workbook preview |
| POST | `/fleet-planner/geocode` | fleet demand geocode |
| POST | `/fleet-planner/clusters` | fleet cluster build |
| POST | `/fleet-planner/route-preview` | fleet route preview |
| POST | `/fleet-planner/global-plan` | fleet optimized plan |
| POST | `/fleet-planner/history` | save fleet planner history |
| POST | `/compute` | synchronous prepared-payload planner run |
| POST | `/workbooks/preview` | uploaded planning workbook preview |
| POST | `/workbooks/submit` | uploaded planning workbook submit and queued job spawn |
| POST | `/jobs` | prepared-payload queued job create and worker spawn |
| POST | `/jobs/{job_id}/cancel` | cancel queued/running job with existing access checks |
| POST | `/jobs/{job_id}/ai-audit` | generate/cache AI audit report; KR jobs generate English and Korean reports |

All paths above are also registered with `/api` prefix except the already
prefixed `/api/health` row.

## FastAPI Runtime

The backend runner scripts now start FastAPI directly:

- Linux/CN: `ops/scripts/run_backend.sh` starts `uvicorn api_app:app`.
- Windows/KR: `ops/scripts/run_backend.ps1` is launched by the `BRP Backend`
  Scheduled Task. It starts a detached single-process uvicorn instance, writes
  stdout/stderr under `logs/`, verifies the process did not exit immediately,
  and then lets the task return to `Ready`.
- Keep `BRP_BACKEND_UVICORN_WORKERS=1` unless the file-backed job queue is
  explicitly hardened and retested for multiple workers.
- `python apps/backend/backend_service.py` no longer starts an HTTP server and
  exits with an operator-facing message.

Rollback is code-based, not env-switch based: deploy a previously tested commit
or revert the FastAPI-only cleanup commit, then restart the same service/task.
Do not attempt to restore legacy mode by setting `BRP_BACKEND_FRAMEWORK`.

## Ops Status Scope

`GET /traffic-rollout/status` is read-only and scopes rollout status to the
current environment:

- CN staging shows all tracked markets: CN, KR, and BK.
- CN production shows CN and BK, excluding KR.
- KR production shows KR only.
- `BRP_TRAFFIC_STATUS_MARKETS` can override the market scope for diagnostics.
- The rollout gate, API-budget preflight, and market overview are all derived
  from the same scoped market definitions. The status endpoint must not call
  traffic providers or start OSRM.

## Required Smoke Checks

Before and after production deployment, verify at least:

- `GET /api/health`
- `GET /api/jobs`
- `GET /api/traffic-rollout/status` as an admin, confirming the environment
  scope above
- create/cancel/delete of a queued job
- frontend version marker matches the deployed git head
- KR `BRP Backend` Scheduled Task can be `Ready` after launch, but there must be
  exactly one uvicorn process on port `8001`, no `backend_service.py` process,
  and `GET /api/health` must return `ok`

No current React `/api` route is intentionally legacy-only. Remaining backend
work is operational hardening and multi-worker safety, not missing HTTP route
coverage.
