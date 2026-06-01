# Architecture Overview

This is the maintained high-level architecture note. For daily commands and release steps, use `docs/development-release-workflow.md`.

## Repository Layout

- `apps/client`: legacy Streamlit/operator UI plus shared Python helpers for workbook intake, geocoding, caches, demand preview, and map/result rendering.
- `apps/web`: React frontend for Route Audit plus side tools such as Distance & Cost and Fleet Planner.
- `apps/backend`: HTTP job service, planner execution, route solving, AI audit integration, generated output handling.
- `ops`: environment examples, Cloudflare examples, and local/server run scripts.
- `docs`: maintained architecture, deployment, workflow, and Codex handoff notes.

## Runtime Shape

Current domestic server layout:

```text
/opt/brp/staging/app          # staging git checkout
/opt/brp/staging/data/jobs    # staging job history
/opt/brp/prod/app             # production git checkout
/opt/brp/prod/data/jobs       # production job history
/opt/brp/osrm-data            # shared read-only OSRM datasets
```

Current domestic server services:

```text
brp-osrm.service              # shared OSRM Docker stack
brp-staging-backend.service   # 127.0.0.1:8001
brp-prod-backend.service      # 127.0.0.1:8000
nginx.service                 # React static host and /api proxy on 127.0.0.1:8501 and 127.0.0.1:8500
cloudflared.service           # public access layer
```

Local checks do not run OSRM Docker. Local `127.0.0.1:5002-5006` can be
provided through the approved access path recorded in the private inventory.

## Environment Roles

- Local checkouts: code-record and testing clients only. They are not runtime
  authorities.
- CN staging: active development and test environment. Public hostname:
  `staging.example.com`; frontend `127.0.0.1:8501`; backend
  `127.0.0.1:8001`.
- CN production: domestic final production environment. Public hostname:
  `$CN_PROD_HOST`; frontend `127.0.0.1:8500`; backend `127.0.0.1:8000`.
- KR production: South Korea final production environment. Public hostname:
  `$KR_PROD_HOST`; frontend `127.0.0.1:8501` on KR; backend
  `127.0.0.1:8001` on KR.

CN production and KR production are pull-and-restart release targets. Staging
changes must not repoint production hostnames or restart production services
unless the user explicitly asks for a production promotion.

## Streamlit Client And Python Helpers

Location: `apps/client`

Responsibilities:

- provide the legacy Streamlit/operator UI where a deployment has not cut over to React
- accept workbook uploads
- build and download workbook templates
- validate current-plan and demand workbooks
- geocode addresses through configured providers
- reuse client cache files under `apps/client/cache`
- provide Python helper modules used by backend `/api/*` routes so provider keys stay server-side
- submit prepared jobs to the backend in the legacy UI
- display job history, audit summaries, maps, and downloads

## React Frontend

Location: `apps/web`

Current status:

- local dev port: `127.0.0.1:5173`
- uses the backend through `/api/*`
- covers Route Audit dashboard, new audit submission, job history, job detail views, AI audit, baselines, maps, actions, and diagnostics
- includes Side Tools:
  - Distance & Cost
  - Fleet Planner
- production-style serving uses Nginx with static files from `apps/web/dist`, SPA fallback, and a same-origin `/api/*` proxy to the backend
- KR public frontend serves React from the KR server's local `8501` origin
- KR private preview can serve the same static/proxy stack from local `4173`
- CN staging uses Nginx for active React testing
- CN production and KR production are separate public production endpoints

## Backend

Location: `apps/backend`

Responsibilities:

- receive prepared jobs from the client
- expose the additive `/api/*` routes used by React
- persist job history under `BRP_BACKEND_JOBS_DIR`
- record job ownership through `owner_email`
- queue planner workers through `BRP_MAX_CONCURRENT_JOBS` when a host-wide
  capacity limit is configured
- select OSRM endpoint by country/city from explicit environment variables
- build OSRM time and distance matrices
- solve and enrich routes
- write generated map outputs under `apps/backend/outputs`
- return structured JSON for the client
- generate or return cached AI audit reports when requested

## External API Coordination

BRP jobs may run in parallel. Submitting multiple jobs starts separate backend
worker processes, so external API protection must be process-wide rather than
only in-memory.

### Provider QPS

Kakao, Google, AMap, and DeepSeek outbound calls use a cross-process provider
rate limiter:

- client implementation: `apps/client/api_rate_limit.py`
- backend implementation: `apps/backend/api_rate_limit.py`
- default state directory: `state/api_rate_limits`
- override: `BRP_API_RATE_LIMIT_DIR`

The limiter protects only the short provider request gate. It does not serialize
whole jobs. Multiple jobs can still run concurrently, but requests for the same
provider key are paced through shared state.

Current provider gates:

- `amap-geocode`
- `amap-places`
- `amap-routing`
- `amap-matrix`
- `kakao-geocode`
- `kakao-places`
- `google-geocode`
- `deepseek-chat-completions`

DeepSeek has a separate default setting:

```text
BRP_DEEPSEEK_MAX_QPS=1.0
```

### Google Usage

Google geocode usage is tracked separately from QPS because it protects monthly
quota and billing rather than instantaneous request rate.

- usage file: `apps/client/cache/google_geocode_usage.json`
- month key timezone: `America/Los_Angeles`
- monthly display limit: `10,000`
- visibility flag: `BRP_SHOW_GOOGLE_GEOCODE_USAGE`

Before sending a Google geocode request, BRP atomically reserves one usage slot
with a cross-process lock. This prevents concurrent workers from reading the same
old value, losing increments, or overshooting the monthly cap near the limit.

Cache hits do not increment usage. Every actual Google request that is sent may
reserve usage, including requests that later return no results or provider
errors. This is intentionally conservative for budget protection.

## Managed Routing

OSRM is BRP-managed routing infrastructure, not an external billed API. It is not
paced through the external provider limiter. OSRM memory protection is handled
separately through job concurrency limits, queue policy, or OSRM service capacity
controls.

## OSRM

Current live OSRM coverage:

- Shanghai: `127.0.0.1:5002`
- Beijing: `127.0.0.1:5003`
- Suzhou: `127.0.0.1:5004`
- Xi'an: `127.0.0.1:5005`
- South Korea: `127.0.0.1:5006`

Production and staging share OSRM because it is read-only routing infrastructure. Mutable runtime data such as jobs, outputs, and caches remains separate between staging and production.

## Public Access

The domestic server runs Cloudflare Tunnel through `cloudflared.service`.

Current public routes include:

- `staging.example.com` -> CN staging frontend
- `brp.example.com` -> CN production frontend
- `brp-kr.example.com` -> KR production frontend
- `brp-api.example.com` -> CN backend API when intentionally exposed behind access control
- `osrm-shanghai.example.com`
- `osrm-beijing.example.com`
- `osrm-suzhou.example.com`
- `osrm-xian.example.com`
- `osrm-south-korea.example.com`

The South Korea server is special: use the approved access route recorded in
private inventory. The KR app hostname is access-protected and currently serves
the React frontend from the KR server's local `8501` origin.

The public domain is replaceable. Domain-specific values should stay in
Cloudflare DNS, Cloudflare Access applications, tunnel ingress, environment
files, and private inventory rather than in application logic.

## Runtime Data

Do not commit runtime data or secrets.

Runtime data to preserve during server moves:

```text
/opt/brp/staging/app/state/api_rate_limits
/opt/brp/job-concurrency
/opt/brp/osrm-data
/opt/brp/staging/data/jobs
/opt/brp/staging/app/apps/backend/cache
/opt/brp/staging/app/apps/client/cache
/opt/brp/staging/app/apps/client/cache/google_geocode_usage.json
/opt/brp/staging/app/apps/backend/outputs
/opt/brp/staging/app/apps/client/demodata
/opt/brp/prod/app/state/api_rate_limits
/opt/brp/prod/data/jobs
/opt/brp/prod/app/apps/backend/cache
/opt/brp/prod/app/apps/client/cache
/opt/brp/prod/app/apps/client/cache/google_geocode_usage.json
/opt/brp/prod/app/apps/backend/outputs
/opt/brp/prod/app/apps/client/demodata
```

Lightweight deployments may keep job history under repository-level
`state/jobs` and provider coordination under `state/api_rate_limits`. Preserve
both alongside caches, generated outputs, and server-local `ops/env/local.env`.

## Separation Of Concerns

- Code is shared through Git.
- Secrets live in server-local `ops/env/local.env`.
- OSRM data lives outside Git.
- Staging and production have separate checkouts, env files, job stores, caches, and output folders.
- Staging and production have separate public hostnames and local frontend/backend ports.
- Cloudflare Tunnel and OSRM stay with the server that owns that deployment's runtime data.
