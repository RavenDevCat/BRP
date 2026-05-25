# Deployment Overview

This document summarizes what a fresh BRP server environment needs before the live stack can run.

Use this as the new-environment checklist. Use `docs/operations-checklist.md` as the day-to-day runbook after the server is already prepared.

## Deployment Principle

Keep code, configuration, and runtime data separate.

- Code is shared from this Git repository.
- Each server owns its own environment variables, OSRM data, cache, jobs, outputs, and tunnel config.
- Future secondary servers, including the South Korea deployment, should follow the main code line with local configuration rather than server-specific code changes.

## Required Services

The live stack needs these services on each server:

1. OSRM Docker containers
2. Backend Python service
3. Client Streamlit service
4. Public access layer, currently Cloudflare Tunnel

Optional access helpers:

- operator access or another private network layer for operator access
- systemd, tmux, launchd, or another process supervisor for long-running services

## Required System Dependencies

Install these before running the application:

- Git
- Python 3.11-compatible runtime or Conda environment
- Docker
- cloudflared, if using Cloudflare Tunnel
- curl or an equivalent HTTP checker

The backend and client Python environments are currently started through:

- `ops/scripts/run_backend.sh`
- `ops/scripts/run_client.sh`

Both scripts allow the Python executable to be overridden by environment variable:

- `BACKEND_PYTHON`
- `CLIENT_PYTHON`

Server-local environment values can be kept in a private file copied from:

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
/opt/brp/app                 # Git checkout
/opt/brp/osrm-data           # Preprocessed OSRM datasets
/opt/brp/runtime             # Optional per-server runtime state
```

The OSRM data directory must contain these subfolders when all current regions are enabled:

```text
shanghai/
beijing/
suzhou/
xian/
south-korea/
```

Each folder must contain the matching preprocessed `.osrm*` files expected by `ops/scripts/run_osrm_stack.sh`.

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

Set these before starting the client:

```bash
export AMAP_API_KEY="..."
export KAKAO_REST_API_KEY="..."
export GOOGLE_GEOCODE_API_KEY="..."
```

### OSRM data and bind settings

```bash
export OSRM_LOCAL_DATA_DIR="/opt/brp/osrm-data"
export OSRM_BIND_HOST="127.0.0.1"
```

Use `OSRM_BIND_HOST=0.0.0.0` only when OSRM ports must be reachable from outside localhost, for example through a private network layer.

### Backend settings

```bash
export BRP_BACKEND_HOST="127.0.0.1"
export BRP_BACKEND_PORT="8001"
```

Then load city routing endpoints:

```bash
source ops/scripts/export_osrm_env.sh
```

For a South Korea-only deployment, load the smaller endpoint set instead:

```bash
source ops/scripts/export_osrm_south_korea_env.sh
```

If a server uses different OSRM hostnames or ports, override the exported `OSRM_BASE_URL_*` values in that server's environment.

### Client settings

```bash
export BRP_BACKEND_BASE_URL="http://127.0.0.1:8001"
export BRP_BACKEND_TIMEOUT_SECONDS="1800"
export STREAMLIT_SERVER_ADDRESS="127.0.0.1"
export STREAMLIT_SERVER_PORT="8501"
```

If the client reaches the backend through a tunnel or reverse proxy, set `BRP_BACKEND_BASE_URL` to that internal or public backend URL.

## Default Ports

| Service | Port | Notes |
| --- | ---: | --- |
| Client Streamlit | `8501` | End-user UI |
| Backend API | `8001` | Job API and health endpoint |
| OSRM Shanghai | `5002` | Docker container |
| OSRM Beijing | `5003` | Docker container |
| OSRM Suzhou | `5004` | Docker container |
| OSRM Xian | `5005` | Docker container |
| OSRM South Korea | `5006` | Docker container |

## Startup Order

Start services in this order:

1. OSRM containers
2. Backend service
3. Client Streamlit service
4. Cloudflare Tunnel or the chosen public access layer

See `docs/operations-checklist.md` for concrete commands and health checks.

## Public Access

The current public access layer is Cloudflare Tunnel.

Reference config:

- `ops/cloudflared/config.example.yml`
- `ops/cloudflared/kr-config.example.yml` for the South Korea server

Current public hostnames:

- `client.example.com`
- `brp.example.com`
- `osrm-shanghai.example.com`
- `osrm-beijing.example.com`
- `osrm-suzhou.example.com`
- `osrm-xian.example.com`
- `osrm-south-korea.example.com`

South Korea server hostnames:

- `brp-kr.example.com`
- `brp-api-kr.example.com`

For production-like environments, avoid exposing the backend publicly without an access control layer.

## Fresh Environment Validation

After deployment, verify:

```bash
docker ps
curl -s http://127.0.0.1:8001/health
curl -I http://127.0.0.1:8501
```

If using Cloudflare Tunnel, also verify the public URLs:

```bash
curl -i https://brp.example.com/health
curl -I https://client.example.com
```

Then run one demo workbook through the client and confirm:

- workbook loads successfully
- job submits to the backend
- backend job finishes
- result summary renders
- route map downloads or previews are available

## Multi-Server Note

Future deployments may run the same application on multiple servers while keeping databases and runtime data separate.

For that model:

- keep the domestic server/version as the main update line
- keep South Korea as an easy-to-sync follower environment
- deploy the same Git revision where possible
- store server differences in environment variables and data directories
- keep each server's database, jobs, cache, outputs, and OSRM data independent
- avoid hard-coded server-specific behavior in application code
