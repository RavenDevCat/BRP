# Deployment Overview

This document summarizes what a fresh BRP server environment needs before the live stack can run.

Use this as the new-environment checklist. Use `docs/development-release-workflow.md` as the day-to-day runbook after the server is already prepared.

## Deployment Principle

Keep code, configuration, and runtime data separate.

- Code is shared from this Git repository.
- Each server owns its own environment variables, OSRM data, cache, jobs, outputs, and tunnel config.
- Future secondary servers, including the South Korea deployment, should follow the main code line with local configuration rather than server-specific code changes.

## Required Services

The live stack needs these services on each server:

1. OSRM Docker containers
2. Backend Python service
3. Frontend service:
   - Nginx for React deployments
   - Streamlit service where that deployment is still on the legacy UI
4. Public access layer, currently Cloudflare Tunnel

Optional access helpers:

- an approved operator access layer
- systemd, tmux, launchd, or another process supervisor for long-running services

## Required System Dependencies

Install these before running the application:

- Git
- Python 3.11-compatible runtime or Conda environment
- Docker
- Node.js/npm only if the server builds React assets locally
- cloudflared, if using Cloudflare Tunnel
- curl or an equivalent HTTP checker

The backend and client Python environments are currently started through:

- `ops/scripts/run_backend.sh`
- `ops/scripts/run_client.sh`

Both scripts allow the Python executable to be overridden by environment variable:

- `BACKEND_PYTHON`
- `CLIENT_PYTHON`

Server-local environment values can be kept in an untracked local file copied from:

- `ops/env/example.env`

Recommended local filename:

- `ops/env/local.env`

Do not commit `ops/env/local.env`.

## Python Dependencies

Install dependencies for both application parts:

```bash
pip install -r apps/backend/requirements.txt
pip install -r apps/client/requirements.txt
```

Use the same virtual environment for both parts unless the deployment intentionally separates them.

## Runtime Data Layout

Do not store large routing data, runtime jobs, generated outputs, cache files, or secrets in Git.

Recommended server layout:

```text
/srv/brp/app                 # Git checkout
/srv/brp/osrm-data           # Preprocessed OSRM datasets
/srv/brp/runtime             # Optional per-server runtime state
```

The OSRM data directory only needs the regions assigned to that server. A full local/dev stack may contain all current regions:

```text
shanghai/
beijing/
suzhou/
xian/
south-korea/
```

Each folder must contain the matching preprocessed `.osrm*` files expected by `ops/scripts/run_osrm_stack.sh`.

Multi-server production should not require every server to carry every OSRM dataset. Use `OSRM_ENABLED_REGIONS` to declare which containers this server should start:

```bash
export OSRM_ENABLED_REGIONS="south-korea"
```

Use a comma-separated list for multi-region servers:

```bash
export OSRM_ENABLED_REGIONS="shanghai,beijing"
```

The special value `auto` starts only regions whose dataset files exist under `OSRM_LOCAL_DATA_DIR`. If a region is explicitly listed and its dataset is missing, startup fails.

For a lightweight South Korea-only deployment, only this folder is required:

```text
south-korea/
```

Start that smaller runtime with:

```bash
ops/scripts/run_osrm_south_korea.sh
```

That script expects `south-korea-latest.osrm` plus the matching generated `.osrm*` sidecar files in the South Korea dataset folder.

## Environment Variables

### API keys

Set these before starting the backend/frontend services:

```bash
export AMAP_API_KEY="..."
export KAKAO_REST_API_KEY="..."
export GOOGLE_GEOCODE_API_KEY="..."
export KAKAO_REST_API_KEY="..."
export DEEPSEEK_API_KEY="..."
```

The Google geocode usage counter is KR-only by policy. Leave it hidden on
domestic, staging, production, and local development servers unless that server
is the South Korea deployment:

The counter file is persistent runtime state. Deployments should preserve the
current `apps/client/cache/google_geocode_usage.json` value and verify
continuity; they should not reset the count to a historical baseline.

```bash
export BRP_SHOW_GOOGLE_GEOCODE_USAGE=false
```

For the South Korea deployment only:

```bash
export BRP_SHOW_GOOGLE_GEOCODE_USAGE=true
```

External API throttling is shared across backend workers and client-side helper
processes through a cross-process limiter. By default it stores state under
`state/api_rate_limits` in the checkout. Set this only if a deployment needs a
separate runtime data mount:

```bash
export BRP_API_RATE_LIMIT_DIR="/srv/brp/runtime/api_rate_limits"
```

AI Audit calls use the same limiter family:

```bash
export BRP_DEEPSEEK_MAX_QPS="1.0"
```

### KR Kakao Navi traffic profile

The South Korea deployment can refresh weekday traffic profiles with Kakao Navi
future directions. This is separate from the Google geocode usage counter and
should use its own persistent usage file. Do not use Google Routes for KR/Seoul
driving profiles; production probes returned HTTP 200 with empty route results.
Route-level traffic attribution normalizes Seoul, Incheon, Gyeonggi, and nearby
Seoul-metro cities into a shared `Seoul Metro` matching bucket while keeping the
stored sample market as `KR`.

Recommended defaults:

```bash
export BRP_LIVE_TRAFFIC_KR_SOURCE="baseline_json"
export BRP_LIVE_TRAFFIC_KR_PROVIDER="kakao_navi"
export BRP_LIVE_TRAFFIC_KR_MARKET="KR"
export BRP_LIVE_TRAFFIC_KR_CITY="Seoul"
export BRP_LIVE_TRAFFIC_KR_TIMEZONE="Asia/Seoul"
export BRP_LIVE_TRAFFIC_SAMPLE_DIR="/srv/brp/runtime/traffic_samples"
export BRP_LIVE_TRAFFIC_KR_BASELINE_DIR="/srv/brp/runtime/traffic_baselines"
export BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH="..."
export BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH="..."
export BRP_LIVE_TRAFFIC_KR_AM_TARGET_ARRIVAL_LOCAL_TIME="08:00"
export BRP_LIVE_TRAFFIC_KR_PM_DEPARTURE_LOCAL_TIME="15:40"
export BRP_LIVE_TRAFFIC_KR_OFF_PEAK_DEPARTURE_LOCAL_TIME="11:00"
export BRP_KAKAO_NAVI_USAGE_PATH="/srv/brp/runtime/kakao_navi_usage.json"
export BRP_KAKAO_NAVI_MONTHLY_SAFETY_CAP="4000"
export BRP_KAKAO_NAVI_DAILY_CAP="500"
export BRP_KAKAO_NAVI_MAX_CALLS_PER_REFRESH="500"
export BRP_KAKAO_NAVI_MAX_WAYPOINTS="5"
```

Linux/staging validation:

```bash
ops/scripts/run_live_traffic_kr_weekday_profile.sh all --dry-run
```

KR production Windows wrapper:

```powershell
.\ops\scripts\run_live_traffic_kr_profile.ps1 -Period all -DryRun
.\ops\scripts\install_live_traffic_kr_timer.ps1
```

Do not schedule the KR profile refresh until the source-specific inputs are
configured and a dry-run confirms the expected call count. `baseline_json`
requires To School and From School baseline JSON paths; `route_audit_job`
requires the corresponding To School, From School, and off-peak job ids.

### OSRM data and bind settings

```bash
export OSRM_LOCAL_DATA_DIR="/srv/brp/osrm-data"
export OSRM_BIND_HOST="127.0.0.1"
```

Keep OSRM bound to `127.0.0.1` for production-style deployments. Use
`OSRM_BIND_HOST=0.0.0.0` only for a deliberate diagnostic environment with a
separate access-control plan.

### Backend settings

```bash
export BRP_BACKEND_HOST="127.0.0.1"
export BRP_BACKEND_PORT="8001"
export BRP_BACKEND_JOBS_DIR="/srv/brp/runtime/jobs"
export BRP_SIDE_TOOLS_DIR="/srv/brp/runtime/side_tools"
export BRP_CLIENT_CACHE_DIR="/srv/brp/runtime/client_cache"
export BRP_BACKEND_CACHE_DIR="/srv/brp/runtime/backend_cache"
```

`BRP_BACKEND_JOBS_DIR`, `BRP_SIDE_TOOLS_DIR`, `BRP_CLIENT_CACHE_DIR`, and
`BRP_BACKEND_CACHE_DIR` should always point at server-local runtime storage, not
at a Git-managed code directory. If omitted, the backend defaults to
`state/jobs`, `state/side_tools`, `apps/client/cache`, and
`apps/backend/cache` under the repository root. The backend also rebuilds the
job `index.json` from existing job JSON files when the index is missing or
empty.

Planner worker concurrency is optional. Leave it unlimited on isolated local
environments, but set it on memory-constrained or shared staging/production hosts:

```bash
export BRP_MAX_CONCURRENT_JOBS="1"
export BRP_JOB_CONCURRENCY_DIR="/srv/brp/job-concurrency"
export BRP_JOB_QUEUE_POLL_SECONDS="5"
export BRP_JOB_SLOT_ATTACH_STALE_SECONDS="300"
```

When staging and production share one host, point both services at the same
`BRP_JOB_CONCURRENCY_DIR` so the limit is host-wide.

Then load city routing endpoints:

```bash
source ops/scripts/export_osrm_env.sh
```

### Authentication settings

BRP separates authentication from application authorization:

- authentication answers which email is using the app
- authorization decides whether that email is an admin or can access a job

The default production-compatible provider reads the authenticated email from
the request headers already supplied by the access layer:

```bash
export BRP_AUTH_PROVIDER="cloudflare_header"
export BRP_AUTH_DISPLAY_NAME="Cloudflare Access"
export BRP_DEV_USER_EMAIL="local@brp.dev"
export BRP_ADMIN_EMAILS="admin@example.com"
```

For staging preparation before company SSO metadata is available, use:

```bash
export BRP_AUTH_PROVIDER="microsoft_sso_pending"
export BRP_AUTH_DISPLAY_NAME="Microsoft SSO"
```

Do not enable a Microsoft/SAML/OIDC provider in production until the identity
provider metadata, callback URLs, user email claim, and logout behavior have
been tested in staging. Admin rights can stay local through `BRP_ADMIN_EMAILS`
even after SSO is enabled.

For a South Korea-only deployment, load the smaller endpoint set instead:

```bash
source ops/scripts/export_osrm_south_korea_env.sh
```

If a server uses different OSRM ports, override the exported `OSRM_BASE_URL_*`
values in that server's environment. These values should normally remain local
loopback URLs, not public hostnames.

For staging/production, keep only the `OSRM_BASE_URL_*` entries that this server
can actually serve locally. Set:

```bash
export OSRM_USE_BUILTIN_DEFAULTS=false
```

This prevents application code from falling back to local development ports for regions that are not configured on that server. Requests for unsupported regions will fail clearly with a missing-OSRM-endpoint error instead of silently calling the wrong local container.

### Frontend and client settings

```bash
export BRP_BACKEND_BASE_URL="http://127.0.0.1:8001"
export BRP_BACKEND_TIMEOUT_SECONDS="1800"
export STREAMLIT_SERVER_ADDRESS="127.0.0.1"
export STREAMLIT_SERVER_PORT="8501"
```

If the client reaches the backend through a tunnel or reverse proxy, set `BRP_BACKEND_BASE_URL` to that internal or public backend URL.

React defaults to same-origin `/api`. In normal Linux production-style serving,
keep that default and configure Nginx to proxy `/api/*` to the backend.
Use `VITE_API_BASE_URL` only for special builds that intentionally target a
different API origin.

### UI language

The React frontend supports English (default) and Korean.
A language toggle appears in the sidebar when the server has
`BRP_DISABLE_LANGUAGE_SWITCH` unset or `false` (staging, KR).
Set it to `true` only on servers where the toggle should stay hidden:

```bash
export BRP_DISABLE_LANGUAGE_SWITCH=true
```

For public React static/proxy hosts behind Cloudflare Access, the managed Nginx
site should reject requests that reach the origin without the
`Cf-Access-Authenticated-User-Email` header. Install the site with:

```bash
sudo SITE_NAME=brp-staging \
  APP_ROOT=/opt/brp/staging/app \
  FRONTEND_PORT=8501 \
  BACKEND_URL=http://127.0.0.1:8001 \
  SERVER_NAMES="staging.example.com" \
  ops/scripts/install_nginx_react_site.sh
```

This guardrail is not a replacement for adding the hostname to the Cloudflare
Access application.

## Default Ports

| Service | Port | Notes |
| --- | ---: | --- |
| CN staging frontend | `8501` | Stage-only React frontend served by Nginx |
| CN staging backend | `8001` | Stage-only job API and health endpoint |
| CN production frontend | `8500` | Domestic production endpoint origin |
| CN production backend | `8000` | Domestic production job API and health endpoint |
| Client Streamlit | chosen legacy port | Legacy/operator UI where still used |
| React Vite dev | `5173` | Local development |
| React static/proxy | `4173`, `8500`, `8501`, or chosen static port | Serve `apps/web/dist` with SPA fallback and `/api/*` proxy; production-style deployments should use Nginx |
| OSRM Shanghai | `5002` | Docker container |
| OSRM Beijing | `5003` | Docker container |
| OSRM Suzhou | `5004` | Docker container |
| OSRM Xian | `5005` | Docker container |
| OSRM South Korea | `5006` | Docker container |

## Startup Order

Start services in this order:

1. OSRM containers
2. Backend service
3. Frontend service, usually Nginx for React or a legacy frontend where still used
4. Cloudflare Tunnel or the chosen public access layer

See `docs/development-release-workflow.md` for concrete commands and health checks.

## Public Access

The current public access layer is Cloudflare Tunnel.

Reference config:

- `ops/cloudflared/config.example.yml`
- `ops/cloudflared/kr-config.example.yml` for the South Korea server

Current public hostnames:

- `staging.example.com` -> CN staging frontend
- `brp.example.com` -> CN production frontend
- `brp-kr.example.com` -> KR production frontend

React frontend hostnames should proxy same-origin `/api` to the backend; do not
create a separate public API hostname unless a legacy integration explicitly
needs it. Do not publish OSRM hostnames through Cloudflare Tunnel or DNS.
Application services should call local OSRM endpoints directly; local
diagnostics should use the operator-provided diagnostic loopback mapping when
needed.

South Korea server hostnames:

- `kr-brp.example.com` -> React frontend behind access control, served by local Nginx on the KR host
- `kr-react-brp.example.com` -> optional React preview hostname

For staging work, React should use the staging hostname, serve `apps/web/dist`
as static assets, and route API calls to the staging backend. Production
hostnames should be moved or restarted only during an explicit release
promotion. KR has already switched its public frontend origin to React on local
port `8501`.

Minimum React static routing rules:

- `/assets/*` -> static files under `apps/web/dist/assets`
- `/api/*` -> backend API
- all other paths -> `apps/web/dist/index.html`

This fallback is required for direct navigation to React routes such as `/jobs`,
`/distance`, and `/fleet`.

For production-like environments, avoid exposing the backend publicly without an access control layer.

## Production Maintenance Notes

- CN staging is the default development, validation, and React build host.
- CN production and KR production are release targets only; change them only
  after explicit approval.
- KR should follow the intended Git revision and reuse CN-staging-built frontend
  assets when frontend files changed. Keep KR service restart details in the
  operator-maintained runbook so future operators do not rediscover the Windows
  scheduled-task setup each session.
- If a checkout cannot fast-forward after an approved public-history rewrite,
  preserve untracked runtime backups, confirm there are no tracked local changes
  to keep, and realign the checkout to the intended `origin/main` revision.

## Fresh Environment Validation

After deployment, verify:

```bash
docker ps
curl -s http://127.0.0.1:8001/health
curl -I http://127.0.0.1:8501
```

If using Cloudflare Tunnel, also verify the public frontend URLs:

```bash
curl -I "https://${PUBLIC_FRONTEND_HOST}"
curl -i "https://${PUBLIC_FRONTEND_HOST}/api/health"
```

Then run one demo workbook through the client and confirm:

- workbook loads successfully
- job submits to the backend
- backend job finishes
- result summary renders
- route map downloads or previews are available

For React deployments, also check direct navigation to:

- `/new`
- `/jobs`
- `/distance`
- `/fleet`
- sidebar version marker, when frontend assets changed

## Multi-Server Note

Future deployments may run the same application on multiple servers while keeping databases and runtime data separate.

For that model:

- keep the domestic server/version as the main update line
- keep South Korea as an easy-to-sync follower environment
- deploy the same Git revision where possible
- store server differences in environment variables and data directories
- keep each server's database, jobs, cache, outputs, and OSRM data independent
- avoid hard-coded server-specific behavior in application code
