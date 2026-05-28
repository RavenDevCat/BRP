# Production Flow

This document records the lightweight development, staging, and production workflow for BRP.

## Goal

Keep fast iteration possible while preventing production from being affected by experiments.

The core rule is:

- development can be messy
- staging should look like production
- production should run a known, recoverable version

## Recommended Environment Split

Minimum practical setup:

- `dev`: local Windows / Mac / current main development machine
- `staging`: deployed server environment for validating `main`
- `prod`: customer-facing server environment

Early-stage lightweight setup can run `staging` and `prod` on the same Ubuntu server, as long as ports, env files, and data directories are separated.

## Same-Server Staging Layout

Staging may share the same physical server as production.

Recommended port split:

```text
prod frontend     127.0.0.1:8501
prod backend      127.0.0.1:8000
prod OSRM         127.0.0.1:5006

staging frontend  127.0.0.1:8502
staging backend   127.0.0.1:8001
staging OSRM      127.0.0.1:5006
```

OSRM can be shared when it is a read-only routing service. Frontend and backend should not share ports.

Recommended domain split:

```text
brp-kr.example.com              -> prod frontend
brp-api-kr.example.com          -> prod backend

staging-brp-kr.example.com      -> staging frontend
staging-brp-api-kr.example.com  -> staging backend
```

Recommended directory split:

```text
/opt/brp/prod/app
/opt/brp/prod/data/jobs
/opt/brp/prod/data/cache

/opt/brp/staging/app
/opt/brp/staging/data/jobs
/opt/brp/staging/data/cache
```

Do not let staging and production share job history, uploaded workbooks, AI reports, or mutable caches.

## Git Flow

Use a lightweight flow:

```text
feature branch -> main -> staging -> version tag -> production
```

Suggested rules:

- `main` should be deployable to staging.
- Feature work should happen on branches when the change is risky or multi-step.
- Staging pulls `main`.
- Production deploys a tag, not a moving branch.

Example staging deploy:

```bash
cd /opt/brp/staging/app
git pull origin main
sudo systemctl restart brp-staging-backend brp-staging-frontend
```

Example production release:

```bash
git tag v0.4.2
git push origin v0.4.2
```

On production:

```bash
cd /opt/brp/prod/app
git fetch --tags
git checkout v0.4.2
sudo systemctl restart brp-prod-backend brp-prod-frontend
```

Example rollback:

```bash
cd /opt/brp/prod/app
git checkout v0.4.1
sudo systemctl restart brp-prod-backend brp-prod-frontend
```

## Environment Config

The code should be the same across environments; configuration should differ.

Use separate env files per environment:

```text
ops/env/dev.env
ops/env/staging.env
ops/env/prod.env
```

Each server should keep its real secrets locally and out of git.

Important environment-specific values:

```text
BRP_ENV=dev|staging|prod
PUBLIC_FRONTEND_URL
BACKEND_PUBLIC_URL
OSRM_BASE_URL
JOB_STORAGE_DIR
GEOCODE_CACHE_PATH
GOOGLE_MAPS_API_KEY
KAKAO_REST_API_KEY
DEEPSEEK_API_KEY
```

## Ubuntu Without GUI

BRP does not require a GUI on Ubuntu.

Preferred server setup:

- Ubuntu Server 22.04 LTS or 24.04 LTS
- SSH
- Docker for OSRM
- Miniconda or venv for Python services
- `systemd` for process supervision
- reverse proxy or tunnel for public access

Codex can still be used without GUI:

- SSH into the server and run Codex CLI in the terminal, or
- use VS Code Remote SSH from a local Mac / Windows machine.

For production-like servers, no GUI is preferred because it saves memory and reduces attack surface.

If a GUI is needed for occasional remote desktop work, install a lightweight desktop such as XFCE:

```bash
sudo apt update
sudo apt install -y xfce4 xfce4-goodies xrdp
sudo systemctl enable --now xrdp
```

## Public Access Options

Cloudflare Tunnel is convenient but should not be treated as mandatory.

Supported deployment options:

- Cloudflare Tunnel
- Nginx reverse proxy
- Caddy reverse proxy
- company gateway / load balancer
- Kubernetes ingress in a larger corporate environment

The app only needs two externally routable services:

```text
frontend hostname -> Streamlit frontend
backend hostname  -> FastAPI backend
```

Authentication can be provided by Cloudflare Access today, but future company deployment may replace it with:

- company SSO / OIDC
- reverse-proxy auth
- internal VPN-only access
- app-native auth layer

## Server Sizing

Lightweight test / staging:

```text
2 vCPU
4 GB RAM
40-80 GB SSD
Ubuntu 22.04 / 24.04
```

Small production for Korea-only routing:

```text
4 vCPU
8 GB RAM
100+ GB SSD
Ubuntu 22.04 / 24.04
```

Larger map coverage, especially China-wide OSRM, may require substantially more memory.

## Deployment Guardrails

- Do not develop directly in production.
- Do not let staging write into production job/cache directories.
- Do not deploy production directly from an untagged moving branch.
- Keep OSRM shared only if it is read-only and compatible with both environments.
- Keep secrets in local env files or a secret manager, never in git.
- Run a staging smoke test before tagging production.

Recommended staging smoke test:

- frontend loads
- backend `/health` returns OK
- geocode works with a small known address set
- OSRM table request works
- one demo workbook can submit a job
- job history and result reload work
- AI report generation works only when explicitly triggered

