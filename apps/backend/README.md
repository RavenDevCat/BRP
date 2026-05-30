# BRP Backend

Python backend service for BRP: Bus Route Planner.

The backend owns the job API, workbook preview/submit routes used by the React
frontend, planner execution, OSRM matrix calls, generated output handling, and
AI Audit integration.

## Responsibilities

- expose `/api/*` routes consumed by `apps/web`
- validate and submit Route Audit workbooks
- persist job records under `BRP_BACKEND_JOBS_DIR` or `state/jobs`
- run planner workers and enrich route results
- select OSRM endpoints from environment configuration
- serve generated map/report artifacts
- generate or return cached AI Audit reports
- expose Distance & Cost and Fleet Planner API routes

Shared workbook/geocode/cache helpers still live in `apps/client` and are used
server-side so provider keys are not exposed to the browser.

## Setup

From the repository root:

```bash
pip install -r apps/backend/requirements.txt
```

Set local environment values in `ops/env/local.env` or the shell. At minimum,
configure the provider keys and OSRM endpoints required by the flow being tested.

Common variables:

```text
BRP_BACKEND_HOST=127.0.0.1
BRP_BACKEND_PORT=8001
BRP_BACKEND_JOBS_DIR=
BRP_API_RATE_LIMIT_DIR=
OSRM_USE_BUILTIN_DEFAULTS=true
```

## Run

From the repository root:

```bash
./ops/scripts/run_backend.sh
```

Health check:

```bash
curl -s http://127.0.0.1:8001/health
```

## Runtime Data

Do not commit backend runtime data:

- job history
- generated outputs
- backend cache
- provider rate-limit state
- server-local env files

See the root `README.md` and `docs/deployment-overview.md` for the full runtime
data policy.

## Workbook Inputs

Route Audit expects a current-plan workbook with:

- `current_plan_assignments`
  - `route_id`
  - `stop_sequence`
  - `bus_type`
  - `country`
  - `city`
  - `address`
  - `passenger_count`
- `current_plan_fleet`
  - `bus_type`
  - `seat_count`
  - `vehicle_count`

The first row of each route is treated as the shared depot/start point.
