# Architecture Overview

This is the maintained high-level architecture note. For daily commands and release steps, use `docs/development-release-workflow.md`.

## Repository Layout

- `apps/client`: Streamlit UI, workbook intake, client-side geocoding, demand preview, job history, map/result rendering.
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
brp-staging-frontend.service  # 127.0.0.1:8501
brp-prod-backend.service      # 127.0.0.1:8000
brp-prod-frontend.service     # 127.0.0.1:8500
cloudflared.service           # public access layer
```

Local development does not run OSRM Docker. Local `127.0.0.1:5002-5006` is an SSH port-forward into the domestic server.

## Client

Location: `apps/client`

Responsibilities:

- accept workbook uploads
- build and download workbook templates
- validate current-plan and demand workbooks
- geocode addresses through configured providers
- reuse client cache files under `apps/client/cache`
- submit prepared jobs to the backend
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
- not currently assigned a public static hostname

Do not assume `brp.ravenapis.com` serves React until Cloudflare routing and static hosting are explicitly changed.

## Backend

Location: `apps/backend`

Responsibilities:

- receive prepared jobs from the client
- persist job history under `BRP_BACKEND_JOBS_DIR`
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
paced through the external provider limiter. Future OSRM stability work should
use job concurrency limits, queue policy, or OSRM service capacity controls.

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

- `https://client.ravenapis.com` -> Streamlit
- `https://brp.ravenapis.com` -> Streamlit
- `https://brp-api.ravenapis.com` -> backend API
- `https://osrm-shanghai.ravenapis.com`
- `https://osrm-beijing.ravenapis.com`
- `https://osrm-suzhou.ravenapis.com`
- `https://osrm-xian.ravenapis.com`
- `https://osrm-south-korea.ravenapis.com`
- `https://osrm-korea.ravenapis.com`

The South Korea server is special: operator access should use the existing Tailscale route. `https://brp-kr.ravenapis.com` is Cloudflare Access-protected and currently serves the React frontend from the KR machine's local `8501` origin.

## Runtime Data

Do not commit runtime data or secrets.

Runtime data to preserve during server moves:

```text
/opt/brp/osrm-data
/opt/brp/staging/data/jobs
/opt/brp/staging/app/apps/backend/cache
/opt/brp/staging/app/apps/client/cache
/opt/brp/staging/app/apps/backend/outputs
/opt/brp/staging/app/apps/client/demodata
/opt/brp/prod/data/jobs
/opt/brp/prod/app/apps/backend/cache
/opt/brp/prod/app/apps/client/cache
/opt/brp/prod/app/apps/backend/outputs
/opt/brp/prod/app/apps/client/demodata
```

## Separation Of Concerns

- Code is shared through Git.
- Secrets live in server-local `ops/env/local.env`.
- OSRM data lives outside Git.
- Staging and production have separate checkouts, env files, job stores, caches, and output folders.
- Cloudflare Tunnel and OSRM stay on the domestic server for long-running service stability.
