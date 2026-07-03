# BRP: Bus Route Planner

BRP is a route audit and planning platform for school bus operations. It ingests
current-plan workbooks, geocodes stops, compares existing routes with optimized
baselines, produces map/report artifacts, and exposes auxiliary tools for
distance, cost, and fleet planning.

The production architecture is a Python backend with a React frontend.
`apps/client/` provides shared Python helpers used by server-side workbook,
geocode, and demand routing flows.

## What It Does

- Route Audit: upload a current bus plan workbook, validate it, run planning
  baselines, and review AI Audit, Audit Detail, Actions, Baselines, Maps, and
  Diagnostics. The Maps tab includes a React MapLibre interactive route map.
- Distance & Cost: check reference-stop distance and estimate route-level cost
  from uploaded workbook data.
- Fleet Planner: preview demand, geocode/cluster stops, generate route plans,
  inspect them in the shared interactive route map, download generated
  workbooks/maps, and submit generated plans as audit jobs.
- Provider coordination: share Kakao, Google, AMap, and DeepSeek rate-limit
  state across worker processes.
- Runtime continuity: preserve job history, caches, generated outputs, usage
  counters, and server-local env files outside normal Git sync. Route Audit job
  records are SQLite-authoritative through `BRP_RUNTIME_DB_PATH`; `state/jobs`
  is a legacy archive/migration path, not the active job lookup source.

## Repository Layout

```text
apps/backend/   Python backend service, planner execution, API routes, AI Audit
apps/client/    Shared Python helpers for workbook, geocode, cache, and demand routing
apps/web/       React frontend for Route Audit and Side Tools
docs/           Architecture, deployment, workflow, updates, handoff notes
ops/            Run scripts, env examples, Cloudflare examples
state/          Ignored runtime state placeholder
tmp/            Ignored scratch area
```

## Prerequisites

- Python 3.11-compatible runtime
- Node.js and npm for React development/builds
- Docker if running local OSRM containers
- Access to OSRM endpoints for the regions being tested
- Provider keys only for flows that call those providers:
  - `AMAP_API_KEY`
  - `KAKAO_REST_API_KEY`
  - `GOOGLE_GEOCODE_API_KEY`
  - `GOOGLE_ROUTES_API_KEY` only for non-KR experimental route-provider checks
  - `DEEPSEEK_API_KEY`

For local or server-specific values, copy `ops/env/example.env` to
`ops/env/local.env` and edit it locally. Do not commit real env files.

## Local Setup

Install Python dependencies:

```bash
pip install -r apps/backend/requirements.txt
pip install -r apps/client/requirements.txt
```

Install React dependencies:

```bash
cd apps/web
npm install
```

Start the backend from the repository root:

```bash
./ops/scripts/run_backend.sh
```

Start the React dev server:

```bash
./ops/scripts/run_web.sh
```

Optional legacy/operator client:

```bash
./ops/scripts/run_client.sh
```

Default local ports:

```text
Backend API:       http://127.0.0.1:8001
React dev server:  http://127.0.0.1:5173
```

The React dev server proxies `/api` to the backend. Production-style React
serving is static files from `apps/web/dist` plus SPA fallback and a same-origin
`/api/*` proxy.

## Useful Commands

Backend health:

```bash
curl -s http://127.0.0.1:8001/health
```

React build:

```bash
cd apps/web
npm run build
```

React type/lint check:

```bash
cd apps/web
npm run lint
```

Python syntax check for key modules:

```bash
python3 -m py_compile apps/backend/backend_service.py apps/backend/ai_audit.py apps/client/client_runtime.py
```

## Runtime Data And Secrets

This repository should contain code, documentation, scripts, and examples only.
Do not commit:

- `ops/env/local.env` or any real `.env` file
- provider keys, passwords, tunnel tokens, or service credentials
- runtime job SQLite stores such as `BRP_RUNTIME_DB_PATH`
- legacy job JSON archives such as `state/jobs` or `BRP_BACKEND_JOBS_DIR`
- `state/side_tools` or a server's `BRP_SIDE_TOOLS_DIR`
- runtime SQLite stores such as `BRP_RUNTIME_DB_PATH`, `BRP_QUOTA_DB_PATH`,
  or `BRP_OSRM_MANAGER_DB_PATH`
- `apps/client/cache` and `apps/backend/cache`
- generated outputs under `apps/client/outputs` or `apps/backend/outputs`
- OSRM datasets and generated `.osrm*` files

Important runtime files to preserve during server moves:

- job history in `BRP_RUNTIME_DB_PATH`
- client/backend caches
- generated map/report outputs
- server-local env files
- quota/runtime SQLite stores for provider rate limits and Google/Kakao usage
- legacy Google/Kakao usage JSON files if present, so first startup can migrate
  them into SQLite

## OSRM Data

Heavy OSRM data should live outside the Git checkout, for example:

```text
/srv/brp/osrm-data
```

Expected region folders include:

```text
shanghai/
beijing/
suzhou/
xian/
south-korea/
bangkok/
```

Only deploy the regions that a server actually serves. See
`docs/deployment-overview.md` for the multi-region and South Korea-only startup
patterns.

## Traffic Timing

BRP uses region-specific traffic timing sources:

- CN traffic sampling uses AMap driving routes against saved current-plan jobs
  or baseline JSON. The existing AM/PM timers are intended for workday peak
  windows.
- KR Route Audit final validation uses Kakao Navi future directions per job.
  The old weekly KR coefficient sampler/timer is retired for normal production
  operation. Google Routes is not used for KR driving checks because Seoul
  driving probes returned empty routes in production diagnostics.
- Bangkok currently uses a conservative static traffic multiplier until a richer
  Bangkok traffic-profile sampling strategy is added.

Useful checked-in wrappers:

```bash
# CN/general sampler
ops/scripts/run_live_traffic_sampler.sh am_peak
```

Historical KR profile wrappers remain for manual diagnostics only. They should
not be scheduled for normal production operation.

```powershell
.\ops\scripts\run_live_traffic_kr_profile.ps1 -Period all -DryRun
```

## Documentation

- `docs/architecture.md`: stable system design and module boundaries
- `docs/development-release-workflow.md`: local development, release, and deploy
  workflow
- `docs/deployment-overview.md`: fresh environment checklist
- `docs/updates.md`: major user/operator-facing updates
- `docs/session-handoff.md`: public pointer explaining that current handoff
  content is not stored in the repository

Committed docs use aliases, example domains, and generic paths. Keep real server
addresses, usernames, hostnames, environment-specific paths, and deployment
inventory outside this repository.

## Development Notes

- Prefer React in `apps/web` for product UI work.
- Keep `apps/client/` changes focused on shared Python helper behavior.
- Do not bypass cross-process provider rate limiting when adding external API
  calls.
- Do not write the Google usage JSON directly; use the reservation helpers in
  `apps/client/client_runtime.py`.
- Keep server-specific behavior in env files and runtime data directories, not
  hard-coded in application code.

## Current Product Notes

As of 2026-06-11, Route Audit Maps has completed the main migration from legacy
backend-rendered HTML previews to the React MapLibre interactive experience. The
map can switch scenarios through summary tiles, inspect routes and stops, focus
selected routes, show route direction arrows, filter and search route lists,
open in an in-page fullscreen viewer, and export a standalone interactive HTML
map. Continue UI/product work in CN staging first; promote production only when
explicitly approved.
