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
  Diagnostics. The Maps tab includes a React MapLibre interactive route map
  with legacy HTML map artifacts retained as fallback.
- Distance & Cost: check reference-stop distance and estimate route-level cost
  from uploaded workbook data.
- Fleet Planner: preview demand, geocode/cluster stops, generate route plans,
  download generated workbooks, and submit generated plans as audit jobs.
- Provider coordination: share Kakao, Google, AMap, and DeepSeek rate-limit
  state across worker processes.
- Runtime continuity: preserve job history, caches, generated outputs, usage
  counters, and server-local env files outside normal Git sync.

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
- `state/jobs` or a server's `BRP_BACKEND_JOBS_DIR`
- `state/side_tools` or a server's `BRP_SIDE_TOOLS_DIR`
- `state/api_rate_limits` or a server's `BRP_API_RATE_LIMIT_DIR`
- `apps/client/cache` and `apps/backend/cache`
- generated outputs under `apps/client/outputs` or `apps/backend/outputs`
- OSRM datasets and generated `.osrm*` files

Important runtime files to preserve during server moves:

- job history
- client/backend caches
- generated map/report outputs
- server-local env files
- Google geocode usage counter
- provider rate-limit state

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
```

Only deploy the regions that a server actually serves. See
`docs/deployment-overview.md` for the multi-region and South Korea-only startup
patterns.

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
