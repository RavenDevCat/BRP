# Backend API Inventory

This inventory tracks the legacy `backend_service.py` route surface while the
backend HTTP layer is migrated to FastAPI. Keep existing paths and JSON payloads
stable during the migration.

## Migration Guardrails

- Default runtime remains `BRP_BACKEND_FRAMEWORK=legacy`.
- FastAPI runtime is opt-in with `BRP_BACKEND_FRAMEWORK=fastapi`.
- Uvicorn must run with one worker until the file-backed job queue is proven
  multi-worker safe.
- The React API base remains `/api`; FastAPI also exposes non-prefixed paths for
  parity with the legacy backend and internal scripts.
- Business logic remains in existing backend modules during the thin-shell
  phase.

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

## Day 8 Runtime Switch

The backend runner scripts support the FastAPI HTTP layer without changing the
React API base or the legacy business modules:

- Linux: set `BRP_BACKEND_FRAMEWORK=fastapi` in `ops/env/local.env` and restart
  the backend service.
- Windows/KR: set `BRP_BACKEND_FRAMEWORK=fastapi` in `ops\env\local.env` and
  restart the `BRP Backend` Scheduled Task.
- Keep `BRP_BACKEND_UVICORN_WORKERS=1` until the file-backed job queue is proven
  safe for multiple workers.
- Rollback is removing or setting `BRP_BACKEND_FRAMEWORK=legacy`, then restarting
  the same service/task.

Production deployment can carry the FastAPI code while live traffic remains on
legacy. Before switching a live environment, run a temporary-port FastAPI smoke
using production paths or isolated temp paths and verify at least:

- `GET /api/health`
- `GET /api/jobs`
- `POST /api/compute` with a stub or non-destructive prepared payload when
  available
- `POST /api/jobs/{job_id}/ai-audit` against an existing completed job only when
  AI-provider use is acceptable
- new-job workbook preview/submit flow in staging before live production switch

## Remaining Legacy Coverage

No current React `/api` route is intentionally legacy-only after Day 7. The
remaining work is operational hardening around live FastAPI rollout, observability,
and multi-worker safety, not missing HTTP route coverage.
