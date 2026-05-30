# Development And Release Workflow

This is the day-to-day workflow for developing BRP from any local machine while using the domestic server for OSRM, staging, production, and Cloudflare Tunnel.

## Current Roles

- Local machine: code editing, local backend/frontend testing, git commits.
- Domestic server: OSRM, staging, production, Cloudflare Tunnel, runtime jobs, caches, and generated outputs.
- South Korea server: special deployment path reached through the existing Tailscale route, not the public hostname.

The local machine should not run OSRM Docker containers. Local development reaches OSRM through SSH port forwarding to the domestic server.

## Local Development

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
  azureuser@143.64.19.35
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

The React web frontend in `apps/web` is an isolated preview on port `5173`.
It proxies `/api` to the backend on `127.0.0.1:8001` and does not replace the
Streamlit client on `8501`.

Production-style React serving is different from Vite dev serving:

- build static assets with `npm run build` in `apps/web`
- serve `apps/web/dist` from a static web server
- configure SPA fallback so `/jobs`, `/fleet`, and `/distance` return `index.html`
- reverse proxy `/api/*` from the same hostname to the backend service

Do not expose `apps/web/dist` without the `/api/*` proxy. The React app uses
`/api` as its default API base URL so it can stay same-origin behind Cloudflare.

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
app:      /opt/brp/staging/app
jobs:     /opt/brp/staging/data/jobs
backend:  127.0.0.1:8001
frontend: 127.0.0.1:8501
public:   https://brp-api.ravenapis.com
public:   https://client.ravenapis.com
```

Before restarting a server backend, confirm the job store is pinned to runtime
storage:

```bash
echo "$BRP_BACKEND_JOBS_DIR"
```

If unset, the run scripts default it to `state/jobs` under the repository root.
Do not rely on `apps/backend/jobs` for real history.

Deploy:

```bash
ssh azureuser@143.64.19.35
cd /opt/brp/staging/app
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

## React Preview Host

Until the final switch, deploy React behind a separate preview hostname, for example:

```text
react-brp.ravenapis.com -> static server for /opt/brp/staging/app/apps/web/dist
react-brp.ravenapis.com/api/* -> 127.0.0.1:8001
```

Suggested staging workflow:

```bash
ssh azureuser@143.64.19.35
cd /opt/brp/staging/app
git pull --ff-only
cd apps/web
npm install
npm run build
```

Static server requirements:

- serve `apps/web/dist/assets/*` as static files
- serve `apps/web/dist/index.html` for unknown non-API paths
- proxy `/api/*` to the staging backend on `127.0.0.1:8001`
- keep Streamlit on `client.ravenapis.com` and `brp.ravenapis.com` until React preview passes QA

For lightweight preview hosts, including Windows servers without Node.js, the
repository includes a Python static/proxy server:

```bash
python ops/scripts/serve_react_static.py \
  --dist-dir apps/web/dist \
  --backend-url http://127.0.0.1:8001 \
  --host 127.0.0.1 \
  --port 4173
```

React preview smoke:

```bash
curl -I https://react-brp.ravenapis.com/
curl -I https://react-brp.ravenapis.com/jobs
curl -s https://react-brp.ravenapis.com/api/health
```

Browser QA before switching `brp.ravenapis.com`:

- Route Audit dashboard, new audit submit, Job History, job detail, maps, and AI Audit
- Distance & Cost workbook preview, reference distance, and route cost
- Fleet Planner demand geocode, clustering, route preview, global plan, workbook download, and submit generated plan as job

If preview passes, switch the public BRP hostname by moving `brp.ravenapis.com`
from Streamlit to the React static service. Keep Streamlit on `client.ravenapis.com`
as a fallback/operator UI during the first React production window.

## Deploy To Production

Production runs on the domestic server:

```text
app:      /opt/brp/prod/app
jobs:     /opt/brp/prod/data/jobs
backend:  127.0.0.1:8000
frontend: 127.0.0.1:8500
```

Only deploy to production after staging passes.

Deploy:

```bash
ssh azureuser@143.64.19.35
cd /opt/brp/prod/app
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
cd /opt/brp/prod/app
git fetch --tags
git checkout vX.Y.Z
sudo systemctl restart brp-prod-backend.service brp-prod-frontend.service
```

Rollback example:

```bash
cd /opt/brp/prod/app
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

Job JSON files may contain absolute paths to generated outputs. If jobs are copied from a local machine to a server, rewrite local repository paths to the server app path before relying on historical map links.

## Guardrails

- Do not run local OSRM Docker for normal development.
- Do not edit directly in `/opt/brp/prod/app`.
- Do not let staging and production share job directories.
- Keep real secrets in `ops/env/local.env` on each machine/server, never in git.
- Keep Cloudflare Tunnel and OSRM long-running on the domestic server.
- Use staging as the gate before production.
