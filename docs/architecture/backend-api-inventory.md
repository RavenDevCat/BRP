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

## Day 1-3 FastAPI Coverage

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

All paths above are also registered with `/api` prefix except the already
prefixed `/api/health` row.

## Remaining Legacy Coverage

Day 4 targets history reads and deletes:

- `GET /jobs`
- `GET /jobs/{job_id}`
- `DELETE /jobs/{job_id}`
- `GET/DELETE /fleet-planner/history/{run_id}`
- `GET/DELETE /distance-checker/history/{run_id}`
- `GET/DELETE /distance-checker/reference-history/{run_id}`
- `GET/DELETE /distance-checker/route-cost-history/{run_id}`

Day 5 targets map and file artifacts:

- `GET /jobs/{job_id}/map-data/{scenario_key}`
- `GET /jobs/{job_id}/artifacts/{artifact_key}`
- `GET /jobs/{job_id}/exports/{export_key}`
- `GET /jobs/{job_id}/traffic-attribution`
- `GET /map-tiles/{z}/{x}/{y}.png`

Day 6 targets side-tool POST workflows:

- `POST /distance-checker/workbook-preview`
- `POST /distance-checker/reference`
- `POST /distance-checker/route-cost`
- `POST /distance-checker/history`
- `POST /fleet-planner/preview`
- `POST /fleet-planner/geocode`
- `POST /fleet-planner/clusters`
- `POST /fleet-planner/route-preview`
- `POST /fleet-planner/global-plan`
- `POST /fleet-planner/history`

Day 7 targets core job workflows:

- `POST /compute`
- `POST /workbooks/preview`
- `POST /workbooks/submit`
- `POST /jobs`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/{job_id}/ai-audit`
