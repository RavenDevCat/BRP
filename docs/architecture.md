# Architecture Overview

This is the maintained high-level architecture note. For daily commands and release steps, use `docs/development-release-workflow.md`.

## Repository Layout

- `apps/client`: Streamlit UI, workbook intake, client-side geocoding, demand preview, job history, map/result rendering.
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

- `https://client.ravenapis.com`
- `https://brp.ravenapis.com`
- `https://brp-api.ravenapis.com`
- `https://osrm-shanghai.ravenapis.com`
- `https://osrm-beijing.ravenapis.com`
- `https://osrm-suzhou.ravenapis.com`
- `https://osrm-xian.ravenapis.com`
- `https://osrm-south-korea.ravenapis.com`
- `https://osrm-korea.ravenapis.com`

The South Korea server is special: operator access should use the existing Tailscale route rather than the public hostname.

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
