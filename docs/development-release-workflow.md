# Development And Release Workflow

This is the day-to-day workflow for developing BRP across local checkouts, the
domestic CN server, and the South Korea/KR production server. Local checkouts
are code-record and test workspaces only. CN owns the development, staging,
domestic production, OSRM, and Cloudflare Tunnel chain. KR is a separate final
production landing target.

## Current Roles

- Local checkouts: code-record and test workspaces. Use them to validate the
  staging host recorded in the private inventory in a browser and keep Git
  visibility.
- CN server: owns the full dev -> stage -> domestic prod chain plus OSRM and
  Cloudflare Tunnel. Development and test changes happen only in CN staging.
  CN production is a separate checkout and service pair; it should change only
  during an explicit production promotion.
- South Korea/KR server: final production landing target for KR. It should pull
  only an intended release revision after CN staging validation and any required
  CN production promotion.

Default posture: work directly in the CN staging checkout, validate through
`$CN_STAGING_HOST`, commit and push the intended Git revision, then promote only
when the user explicitly asks for a production update. Keep runtime data and
server-local env files out of Git.
When the operator says `开发` or `拉齐` without naming a different target, read
that as CN staging work.

## Environment Boundary Contract

Current hostnames and ports:

```text
$CN_STAGING_HOST -> CN staging frontend 127.0.0.1:8501
CN staging backend    -> 127.0.0.1:8001

$CN_PROD_HOST     -> CN production frontend 127.0.0.1:8500
CN production backend -> 127.0.0.1:8000

$KR_PROD_HOST     -> KR production frontend 127.0.0.1:8501 on KR
KR production backend -> 127.0.0.1:8001 on KR
```

Staging is the only place for active development and test traffic. Do not point
`$CN_PROD_HOST` or `$KR_PROD_HOST` at staging ports as part of preview work. CN
production and KR production should be pull-and-restart
targets only after the staged version is accepted.

The public domain is replaceable. Treat public hostnames as DNS,
Cloudflare Tunnel, Cloudflare Access, and environment configuration, not product
logic. When the company domain is ready, update domain references in Cloudflare
DNS, Access applications, tunnel ingress, env files, smoke-test variables, and
private inventory together.

Authentication is environment-configurable. Keep production environments on
their current provider until an explicit migration is approved. CN staging may
use `BRP_AUTH_PROVIDER=microsoft_sso_pending` while Microsoft SSO details are
being prepared; this only changes the application-reported auth mode and shell
auth links, not the job ownership model. Admin rights remain controlled by the
server-local `BRP_ADMIN_EMAILS` value unless a future release deliberately moves
admin authorization to group claims.

Local checkouts should not run OSRM Docker containers. Lightweight local checks
can reach OSRM through the diagnostic loopback mapping recorded in private
inventory.
The React Google geocode usage counter is shown only when `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`, which should be set on the South Korea deployment only.
That counter is persistent runtime state. Preserve the current `apps/client/cache/google_geocode_usage.json` file during deploys; do not reset it to an old verified value.
External API QPS is also persistent runtime coordination state. Kakao, Google,
AMap, and DeepSeek calls use cross-process limiter files under
`state/api_rate_limits` by default, or `BRP_API_RATE_LIMIT_DIR` when set.

CN staging and CN production intentionally share runtime history and caches
through server-local env paths. Keep these variables pointed at the same shared
runtime roots on both CN service pairs:

```text
BRP_BACKEND_JOBS_DIR
BRP_SIDE_TOOLS_DIR
BRP_CLIENT_CACHE_DIR
BRP_BACKEND_CACHE_DIR
BRP_API_RATE_LIMIT_DIR
```

Use separate checkouts, ports, service names, and `ops/env/local.env` files for
staging and production; only the runtime data roots above should be shared.

Planner job concurrency is separately gated by `BRP_MAX_CONCURRENT_JOBS`. On
CN, keep staging and production pointed at the same `BRP_JOB_CONCURRENCY_DIR`
so the limit is host-wide instead of per service. If a backend dies after
claiming a queue slot but before starting the worker, the slot is reclaimed
after `BRP_JOB_SLOT_ATTACH_STALE_SECONDS`.

## Connection Workspaces

Local checkouts should not be treated as the main runtime source. Use them to
keep a code record, inspect Git, and test the staging site in a browser. For
normal BRP feature work, work in the CN staging checkout:

```text
staging app: /opt/brp/staging/app
prod app:    /opt/brp/prod/app  # release promotion only
```

Local service startup remains useful only as a fallback or diagnostic path.
Do not start local services for ordinary development alignment; validate through
the CN staging host unless the user explicitly asks for a local diagnostic run.
When a local diagnostic run is needed, OSRM endpoints should be provided as
local loopback ports:

```text
127.0.0.1:5002 -> Shanghai OSRM
127.0.0.1:5003 -> Beijing OSRM
127.0.0.1:5004 -> Suzhou OSRM
127.0.0.1:5005 -> Xian OSRM
127.0.0.1:5006 -> South Korea OSRM
```

Start local services:

```bash
./ops/scripts/run_backend.sh
./ops/scripts/run_client.sh
./ops/scripts/run_web.sh
```

In PowerShell, start the diagnostic OSRM loopback mapping recorded in the
private inventory first, then use the checked-in service helpers:

```powershell
.\ops\scripts\run_backend.ps1
.\ops\scripts\run_client.ps1
.\ops\scripts\run_web.ps1
```

The PowerShell helpers load `ops/env/local.env` if it exists. For local checks,
keep `BACKEND_PYTHON` and `CLIENT_PYTHON` pointed at the local Python
environment, and keep `BRP_DEV_USER_EMAIL` set to a development email that
should own or administer local jobs.

Windows shell habit:

- Use Git Bash at `C:\Program Files\Git\bin\bash.exe` for complex local `ssh`,
  `scp`, and Git command composition, especially when sending shell snippets to
  Linux hosts.
- Use PowerShell for Windows-specific local helper commands, such as npm/Python
  invocations that rely on Windows paths.
- For remote Windows service work, avoid deeply nested one-line quoting. Prefer
  encoded PowerShell or an uploaded script, then run that script remotely.

If historical jobs were generated in another environment, do not rely on their persisted absolute
map-output paths. The client rerenders historical maps into the current checkout under
`apps/client/outputs/<job_id>/`.

Local checks:

```bash
curl -s http://127.0.0.1:8001/health
curl -I http://127.0.0.1:8501
curl -I http://127.0.0.1:5173
curl -s 'http://127.0.0.1:5002/nearest/v1/driving/121.4737,31.2304?number=1'
```

PowerShell equivalents:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-WebRequest http://127.0.0.1:8501 -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:5173 -UseBasicParsing
Invoke-RestMethod 'http://127.0.0.1:5002/nearest/v1/driving/121.4737,31.2304?number=1'
```

Provider safety checks:

- Do not bypass `CrossProcessRateLimiter` for Kakao, Google, AMap, or DeepSeek calls.
- Do not write `apps/client/cache/google_geocode_usage.json` directly; use the atomic reservation helpers.
- Keep `state/api_rate_limits` as runtime coordination state, or set `BRP_API_RATE_LIMIT_DIR` to an equivalent server-local runtime path.
- Provider QPS limiting should gate only outbound requests, not whole jobs.
- Keep `BRP_MAX_CONCURRENT_JOBS` and `BRP_JOB_CONCURRENCY_DIR` configured on
  shared servers so planner workers queue instead of exhausting memory.
- OSRM capacity should be handled separately from external provider QPS.
- Do not publish OSRM through Cloudflare Tunnel or DNS. Application services
  should call local `127.0.0.1` OSRM endpoints directly.

The React web frontend in `apps/web` runs locally through Vite on port `5173`.
It proxies `/api` to the backend on `127.0.0.1:8001`. Production-style serving
uses Nginx as the static/proxy host instead of the Vite dev server. KR has
already cut over to React; CN staging and CN production use separate
frontend/backend origins.

Production-style React serving is different from Vite dev serving:

- build static assets with `npm run build` in `apps/web`
- serve `apps/web/dist` from Nginx
- configure SPA fallback so `/jobs`, `/fleet`, and `/distance` return `index.html`
- reverse proxy `/api/*` from the same hostname to the backend service

Do not expose `apps/web/dist` without the `/api/*` proxy. The React app uses
`/api` as its default API base URL so it can stay same-origin behind Cloudflare.
For public hosts, keep the hostname inside a Cloudflare Access application. The
managed Nginx site returns 401 when the Cloudflare Access user header is missing
as a server-side guardrail against DNS or Access app drift.

Install or refresh a Linux Nginx React site from the app checkout:

```bash
sudo SITE_NAME=brp-staging \
  APP_ROOT=/opt/brp/staging/app \
  FRONTEND_PORT=8501 \
  BACKEND_URL=http://127.0.0.1:8001 \
  SERVER_NAMES="staging.example.com" \
  ops/scripts/install_nginx_react_site.sh
```

Before publishing:

```bash
git status
git add ...
git commit -m "..."
git push origin main
```

## Deploy To Staging

Staging runs on the domestic server:

```text
app:      $BRP_STAGING_APP_ROOT
jobs:     $BRP_STAGING_JOBS_ROOT
backend:  127.0.0.1:8001
frontend: 127.0.0.1:8501
public:   https://$CN_STAGING_HOST
```

Staging deploys must not change `$CN_PROD_HOST`, `brp-prod-*` services, or
CN production ports.

Before restarting a server backend, confirm the job store is pinned to runtime
storage:

```bash
echo "$BRP_BACKEND_JOBS_DIR"
```

If unset, the run scripts default it to `state/jobs` under the repository root.
Do not rely on `apps/backend/jobs` for real history.

Deploy:

```bash
ssh "$CN_SSH_USER@$CN_SSH_HOST"
cd "$BRP_STAGING_APP_ROOT"
git pull --ff-only
sudo systemctl restart brp-staging-backend.service
sudo SITE_NAME=brp-staging \
  APP_ROOT="$BRP_STAGING_APP_ROOT" \
  FRONTEND_PORT=8501 \
  BACKEND_URL=http://127.0.0.1:8001 \
  SERVER_NAMES="staging.example.com" \
  ops/scripts/install_nginx_react_site.sh
sudo systemctl reload nginx
curl -s http://127.0.0.1:8001/health
curl -I http://127.0.0.1:8501
```

If OSRM data or OSRM startup settings changed:

```bash
sudo systemctl restart brp-osrm.service
sudo docker ps
```

If Cloudflare Tunnel config changed:

```bash
sudo systemctl restart cloudflared.service
sudo systemctl status cloudflared.service --no-pager -l
```

Run a staging smoke test before production:

- frontend loads
- backend `/health` returns OK
- OSRM endpoint returns `code: Ok`
- one demo workbook can submit and finish a job
- job history reloads
- map output opens
- AI report works when explicitly triggered

## React Static Host And Cutover

For any deployment that has not cut over yet, deploy React behind a separate
preview hostname first, for example:

```text
$DOMESTIC_REACT_PREVIEW_HOST -> static server for $BRP_STAGING_APP_ROOT/apps/web/dist
$DOMESTIC_REACT_PREVIEW_HOST/api/* -> 127.0.0.1:8001
```

Suggested staging workflow:

```bash
ssh "$CN_SSH_USER@$CN_SSH_HOST"
cd "$BRP_STAGING_APP_ROOT"
git pull --ff-only
cd apps/web
npm install
npm run build
```

Static server requirements:

- serve `apps/web/dist/assets/*` as static files
- serve `apps/web/dist/index.html` for unknown non-API paths
- proxy `/api/*` to the staging backend on `127.0.0.1:8001`
- if `BRP_BACKEND_SERVICE_TOKEN` is set for the backend, inject that token
  server-side in the static/proxy process; never expose it to the browser

Linux preview and production hosts should use the managed Nginx installer:

```bash
sudo SITE_NAME=brp-staging \
  APP_ROOT="$BRP_STAGING_APP_ROOT" \
  FRONTEND_PORT=8501 \
  BACKEND_URL=http://127.0.0.1:8001 \
  SERVER_NAMES="staging.example.com" \
  ops/scripts/install_nginx_react_site.sh
```

React static/proxy smoke:

```bash
curl -I "https://${DOMESTIC_REACT_PREVIEW_HOST}/"
curl -I "https://${DOMESTIC_REACT_PREVIEW_HOST}/jobs"
curl -s "https://${DOMESTIC_REACT_PREVIEW_HOST}/api/health"
```

Browser QA before production promotion:

- Route Audit dashboard, new audit submit, Job History, job detail, maps, and AI Audit
- Distance & Cost workbook preview, reference distance, and route cost
- Fleet Planner demand geocode, clustering, route preview, global plan, workbook download, and submit generated plan as job

If preview passes, schedule a separate production promotion. Do not switch or
repoint `$CN_PROD_HOST` as part of staging setup. Production hostnames move only
during an explicit release window.

KR is already in the post-cutover shape. Its public React service uses local
Nginx on the KR server's `8501` origin, and its preview uses local `4173`.
Because KR does not currently have Node/npm in PATH, build React locally and
copy `apps/web/dist` to KR when frontend assets change.

## Deploy To KR

KR is a separate production deployment that follows the intended Git revision
after CN production unless the user explicitly excludes KR. Preserve its runtime
data: `state/jobs`, `state/side_tools`, `apps/client/cache`,
`apps/backend/cache`, generated outputs, `ops/env/local.env`,
`apps/client/cache/google_geocode_usage.json`, and `state/api_rate_limits`.

High-level KR deploy flow:

1. Validate the intended revision on CN staging.
2. Commit and push the intended revision.
3. Build React from a local/connection workspace with `npm run build` in
   `apps/web` when frontend assets changed and KR cannot build it itself.
4. Pull the intended revision in the KR active checkout recorded in the private
   inventory.
5. Copy the new `apps/web/dist` to KR when frontend assets changed.
6. Restart `BRP-Backend-Preview` and `BRP-Nginx-Public`. Restart
   `BRP-React-Preview` only when the private preview needs the new build.
   `BRP-React-Public` is intentionally disabled after the public Nginx cutover.
7. Verify backend health, public React proxy health, private React preview health, `/new`, `/jobs`, job count, cache counts, and Google usage continuity.

## Deploy To Production

Production runs on the domestic server:

```text
app:      $BRP_PROD_APP_ROOT
jobs:     $BRP_PROD_JOBS_ROOT
backend:  127.0.0.1:8000
frontend: 127.0.0.1:8500
public:   https://$CN_PROD_HOST
```

Only deploy to production after staging passes.
Do not use production as a development or preview environment.

Deploy:

```bash
ssh "$CN_SSH_USER@$CN_SSH_HOST"
cd "$BRP_PROD_APP_ROOT"
git pull --ff-only
sudo systemctl restart brp-prod-backend.service
sudo SITE_NAME=brp-prod \
  APP_ROOT="$BRP_PROD_APP_ROOT" \
  FRONTEND_PORT=8500 \
  BACKEND_URL=http://127.0.0.1:8000 \
  SERVER_NAMES="brp.example.com" \
  ops/scripts/install_nginx_react_site.sh
sudo systemctl reload nginx
curl -s http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8500
```

For higher-safety releases, tag the tested commit before production:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

Then on production:

```bash
cd "$BRP_PROD_APP_ROOT"
git fetch --tags
git checkout vX.Y.Z
sudo systemctl restart brp-prod-backend.service
sudo SITE_NAME=brp-prod \
  APP_ROOT="$BRP_PROD_APP_ROOT" \
  FRONTEND_PORT=8500 \
  BACKEND_URL=http://127.0.0.1:8000 \
  SERVER_NAMES="brp.example.com" \
  ops/scripts/install_nginx_react_site.sh
sudo systemctl reload nginx
```

Rollback example:

```bash
cd "$BRP_PROD_APP_ROOT"
git fetch --tags
git checkout vX.Y.PREVIOUS
sudo systemctl restart brp-prod-backend.service
sudo SITE_NAME=brp-prod \
  APP_ROOT="$BRP_PROD_APP_ROOT" \
  FRONTEND_PORT=8500 \
  BACKEND_URL=http://127.0.0.1:8000 \
  SERVER_NAMES="brp.example.com" \
  ops/scripts/install_nginx_react_site.sh
sudo systemctl reload nginx
```

## Server Status Checks

Check all core services:

```bash
systemctl is-active \
  brp-osrm.service \
  brp-staging-backend.service \
  brp-prod-backend.service \
  nginx.service \
  cloudflared.service
```

Check enabled-on-boot state:

```bash
systemctl is-enabled \
  brp-osrm.service \
  brp-staging-backend.service \
  brp-prod-backend.service \
  nginx.service \
  cloudflared.service
```

Check listening ports:

```bash
ss -ltnp | grep -E ':5002|:5003|:5004|:5005|:5006|:8000|:8001|:8500|:8501|:20241'
```

Check OSRM containers:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

## Runtime Data Sync Notes

Runtime data is intentionally outside git. When moving to a fresh server or replacing a server, remember these paths:

```text
$BRP_OSRM_DATA_ROOT
$BRP_STAGING_APP_ROOT/state/api_rate_limits
$BRP_STAGING_JOBS_ROOT
$BRP_STAGING_APP_ROOT/apps/backend/cache
$BRP_STAGING_APP_ROOT/apps/client/cache
$BRP_STAGING_APP_ROOT/apps/client/cache/google_geocode_usage.json
$BRP_STAGING_APP_ROOT/apps/backend/outputs
$BRP_STAGING_APP_ROOT/apps/client/demodata
$BRP_PROD_APP_ROOT/state/api_rate_limits
$BRP_PROD_JOBS_ROOT
$BRP_PROD_APP_ROOT/apps/backend/cache
$BRP_PROD_APP_ROOT/apps/client/cache
$BRP_PROD_APP_ROOT/apps/client/cache/google_geocode_usage.json
$BRP_PROD_APP_ROOT/apps/backend/outputs
$BRP_PROD_APP_ROOT/apps/client/demodata
```

Job JSON files may contain absolute paths to generated outputs. If jobs are copied from one environment to another, rewrite local repository paths to the target app path before relying on historical map links.

## Guardrails

- Do not run local OSRM Docker for normal development.
- Do not edit directly in the production app checkout.
- On CN, staging and production should share runtime history/cache directories
  through env paths; elsewhere, do not share runtime directories unless that is
  an explicit deployment decision.
- Keep real secrets in `ops/env/local.env` for each environment, never in git.
- Preserve `state/jobs` or `BRP_BACKEND_JOBS_DIR`, `state/side_tools` or
  `BRP_SIDE_TOOLS_DIR`, `state/api_rate_limits` or
  `BRP_API_RATE_LIMIT_DIR`, `BRP_CLIENT_CACHE_DIR`, `BRP_BACKEND_CACHE_DIR`,
  legacy cache folders, and generated outputs across pulls and directory moves.
- Keep Cloudflare Tunnel and OSRM long-running on the server that owns each
  deployment, but do not expose OSRM through public hostnames.
- Use staging as the gate before production.
