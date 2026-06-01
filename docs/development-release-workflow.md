# Development And Release Workflow

This is the day-to-day workflow for developing BRP across the Windows/Mac
operation machines, the domestic CN server, and the South Korea/KR production
server. Local Windows and Mac checkouts are connection and code-record
workspaces only. CN owns the development, staging, domestic production, OSRM,
and Cloudflare Tunnel chain. KR is a separate final production landing target.

## Current Roles

- Windows operation machine and Mac operation machine: operator access-first remote
  connection and development workstations. Use them to open
  `staging.example.com` in a browser for testing, and to connect by
  Codex/VS Code Remote into the CN staging checkout for code changes.
- CN server: owns the full dev -> stage -> domestic prod chain plus OSRM and
  Cloudflare Tunnel. Development and test changes happen only in CN staging.
  CN production is a separate checkout and service pair; it should change only
  during an explicit production promotion.
- South Korea/KR server: final production landing target for KR. It should pull
  only an intended release revision after CN staging validation and any required
  CN production promotion.

Default posture: connect over operator access where available, work directly in the CN
staging checkout, validate through `staging.example.com`, commit and push the
intended Git revision, then promote only when the user explicitly asks for a
production update. Keep runtime data and server-local env files out of Git.

## Environment Boundary Contract

Current hostnames and ports:

```text
staging.example.com -> CN staging frontend 127.0.0.1:8501
CN staging backend    -> 127.0.0.1:8001

$CN_PROD_HOST     -> CN production frontend 127.0.0.1:8500
CN production backend -> 127.0.0.1:8000

$KR_PROD_HOST  -> KR production frontend 127.0.0.1:8501 on KR
KR production backend -> 127.0.0.1:8001 on KR
```

Staging is the only place for active development and test traffic. Do not point
`$CN_PROD_HOST` or `$KR_PROD_HOST` at staging ports as part of
preview work. CN production and KR production should be pull-and-restart
targets only after the staged version is accepted.

The `example.com` domain is replaceable. Treat public hostnames as DNS,
Cloudflare Tunnel, Cloudflare Access, and environment configuration, not product
logic. When the company domain is ready, update domain references in Cloudflare
DNS, Access applications, tunnel ingress, env files, smoke-test variables, and
private inventory together.

The local machine should not run OSRM Docker containers. Local development reaches OSRM through SSH port forwarding to the domestic server.
The React Google geocode usage counter is shown only when `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`, which should be set on the South Korea deployment only.
That counter is persistent runtime state. Preserve the current `apps/client/cache/google_geocode_usage.json` file during deploys; do not reset it to an old verified value.
External API QPS is also persistent runtime coordination state. Kakao, Google,
AMap, and DeepSeek calls use cross-process limiter files under
`state/api_rate_limits` by default, or `BRP_API_RATE_LIMIT_DIR` when set.

Planner job concurrency is separately gated by `BRP_MAX_CONCURRENT_JOBS`. On
CN, keep staging and production pointed at the same `BRP_JOB_CONCURRENCY_DIR`
so the limit is host-wide instead of per service. If a backend dies after
claiming a queue slot but before starting the worker, the slot is reclaimed
after `BRP_JOB_SLOT_ATTACH_STALE_SECONDS`.

## Local And Mac Connection Workspaces

Local Windows and Mac checkouts should not be treated as the main runtime source.
Use them to keep a code record, connect through SSH/Remote SSH, inspect Git, run
Codex/VS Code Remote against CN, and test the staging site in a browser. For
normal BRP feature work, connect to CN over operator access when possible and work in:

```text
staging app: /opt/brp/staging/app
prod app:    /opt/brp/prod/app  # release promotion only
```

Local service startup remains useful only as a fallback or diagnostic path.

From the repository root on the local machine, confirm the OSRM tunnel is running:

```bash
ps aux | grep '[s]sh -fN.*5002'
```

If it is not running, start it:

```bash
ssh -fN -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:5002:127.0.0.1:5002 \
  -L 127.0.0.1:5003:127.0.0.1:5003 \
  -L 127.0.0.1:5004:127.0.0.1:5004 \
  -L 127.0.0.1:5005:127.0.0.1:5005 \
  -L 127.0.0.1:5006:127.0.0.1:5006 \
  "$CN_SSH_USER@$CN_SSH_HOST"
```

Local OSRM endpoints stay as localhost values because the tunnel forwards them to the domestic server:

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

On Windows PowerShell, use the checked-in PowerShell helpers:

```powershell
.\ops\scripts\start_osrm_tunnel.ps1
.\ops\scripts\run_backend.ps1
.\ops\scripts\run_client.ps1
.\ops\scripts\run_web.ps1
```

The PowerShell helpers load `ops/env/local.env` if it exists. For local Windows development, keep
`BACKEND_PYTHON` and `CLIENT_PYTHON` pointed at the local Conda environment, and keep
`BRP_DEV_USER_EMAIL` set to a development email that should own or administer local jobs.

If historical jobs were generated on another machine, do not rely on their persisted absolute
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

The React web frontend in `apps/web` runs locally through Vite on port `5173`.
It proxies `/api` to the backend on `127.0.0.1:8001`. Production-style serving
uses a static/proxy host instead of the Vite dev server. KR has already cut over
to React; CN staging and CN production use separate frontend/backend origins.

Production-style React serving is different from Vite dev serving:

- build static assets with `npm run build` in `apps/web`
- serve `apps/web/dist` from a static web server
- configure SPA fallback so `/jobs`, `/fleet`, and `/distance` return `index.html`
- reverse proxy `/api/*` from the same hostname to the backend service

Do not expose `apps/web/dist` without the `/api/*` proxy. The React app uses
`/api` as its default API base URL so it can stay same-origin behind Cloudflare.
For public hosts, keep the hostname inside a Cloudflare Access application and
set `BRP_REQUIRE_CLOUDFLARE_ACCESS=true` on the static/proxy service as a
server-side guardrail against DNS or Access app drift.

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
public:   https://staging.example.com
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
sudo systemctl restart brp-staging-backend.service brp-staging-frontend.service
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

For lightweight preview hosts, including Windows servers without Node.js, the
repository includes a Python static/proxy server:

```bash
python ops/scripts/serve_react_static.py \
  --dist-dir apps/web/dist \
  --backend-url http://127.0.0.1:8001 \
  --host 127.0.0.1 \
  --port 4173
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
repoint `$CN_PROD_HOST` as part of staging setup. Production hostnames move
only during an explicit release window.

KR is already in the post-cutover shape. Its public React service uses the KR
machine's local `8501` origin, and its private preview uses local `4173`.
Because KR does not currently have Node/npm in PATH, build React locally and
copy `apps/web/dist` to KR when frontend assets change.

## Deploy To KR

KR is a separate production deployment that follows the intended Git revision
after CN validation. Preserve its runtime data:
`state/jobs`, `apps/client/cache`, `apps/backend/cache`, generated outputs,
`ops/env/local.env`, `apps/client/cache/google_geocode_usage.json`, and
`state/api_rate_limits`.

High-level KR deploy flow:

1. Validate the intended revision on CN staging.
2. Commit and push the intended revision.
3. Build React from a local/connection workspace with `npm run build` in
   `apps/web` when frontend assets changed and KR cannot build it itself.
4. Pull the intended revision in the KR active checkout recorded in the private
   inventory.
5. Copy the new `apps/web/dist` to KR when frontend assets changed.
6. Restart scheduled tasks `BRP-Backend-Preview`, `BRP-React-Preview`, and `BRP-React-Public`.
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
sudo systemctl restart brp-prod-backend.service brp-prod-frontend.service
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
sudo systemctl restart brp-prod-backend.service brp-prod-frontend.service
```

Rollback example:

```bash
cd "$BRP_PROD_APP_ROOT"
git fetch --tags
git checkout vX.Y.PREVIOUS
sudo systemctl restart brp-prod-backend.service brp-prod-frontend.service
```

## Server Status Checks

Check all core services:

```bash
systemctl is-active \
  brp-osrm.service \
  brp-staging-backend.service \
  brp-staging-frontend.service \
  brp-prod-backend.service \
  brp-prod-frontend.service \
  cloudflared.service
```

Check enabled-on-boot state:

```bash
systemctl is-enabled \
  brp-osrm.service \
  brp-staging-backend.service \
  brp-staging-frontend.service \
  brp-prod-backend.service \
  brp-prod-frontend.service \
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

Job JSON files may contain absolute paths to generated outputs. If jobs are copied from a local machine to a server, rewrite local repository paths to the server app path before relying on historical map links.

## Guardrails

- Do not run local OSRM Docker for normal development.
- Do not edit directly in the production app checkout.
- Do not let staging and production share job directories.
- Keep real secrets in `ops/env/local.env` on each machine/server, never in git.
- Preserve `state/jobs`, `state/api_rate_limits`, caches, and generated outputs across pulls and directory moves.
- Keep Cloudflare Tunnel and OSRM long-running on the domestic server.
- Use staging as the gate before production.
