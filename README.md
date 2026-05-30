# BRP Platform

Unified project layout for the live BRP stack.

This structure separates the public client, the solver backend, operational scripts, and data references so the project is easier to explain, maintain, and deploy.

## Structure

- `apps/client`
  - legacy Streamlit/operator client
  - still owns shared Python workbook, geocoding, cache, and output helpers used by server-side flows
- `apps/web`
  - React frontend for Route Audit and Side Tools
  - KR public frontend now serves this app; domestic public hostnames remain Streamlit until their own cutover
- `apps/backend`
  - backend compute service
  - handles `/api/*`, workbook preview/submit helpers, OSRM matrix building, OR-Tools solving, final route enrichment, AI Audit, and JSON responses
- `docs`
  - architecture and operational notes
- `ops`
  - Cloudflare and local run scripts
- `tmp`
  - local scratch area for one-off validation outputs

## Git Architecture

This repository is intended to store the codebase and documentation only.

- Commit:
  - `apps/client`
  - `apps/web`
  - `apps/backend`
  - `docs`
  - `ops`
  - root files such as `README.md` and `.gitignore`
- Keep out of Git:
  - Python virtual environments such as `.venv/`
  - runtime caches under `apps/client/cache` and `apps/backend/cache`
  - saved backend jobs under `state/jobs` or the server's `BRP_BACKEND_JOBS_DIR`
  - provider coordination files under `state/api_rate_limits`
  - rendered HTML outputs under `apps/client/outputs` and `apps/backend/outputs`
  - local secrets such as `.env` files and Streamlit secrets
  - OSRM datasets and preprocessed `.osrm*` files

## Local Data Layout

Heavy OSRM data should live outside the repository.

- Recommended OSRM data root: `/opt/brp/osrm-data`
- Expected subfolders:
  - `shanghai`
  - `beijing`
  - `suzhou`
  - `xian`
  - `south-korea`

The code repository stays lightweight, while `ops/scripts/run_osrm_stack.sh` mounts the local OSRM data directory into Docker containers at runtime.

## Current live flow

1. Users enter through React where a server has been cut over, or through the
   legacy Streamlit client on deployments that have not been cut over yet.
2. Route Audit uploads a workbook and calls backend `/api/*` routes for preview,
   validation, job creation, history, details, and AI Audit.
3. Server-side Python helpers parse workbooks, reuse geocode/cache data, prepare
   stops and fleet inputs, and keep provider keys out of the browser.
4. The backend persists the job, starts planner work, selects the configured OSRM
   endpoint by country/city, solves scenarios, and writes generated outputs.
5. The frontend renders job history, AI Audit, Audit Detail, Actions, Baselines,
   Maps, Diagnostics, and Side Tools such as Distance & Cost and Fleet Planner.

KR has already switched `brp-kr.ravenapis.com` to the React frontend. Domestic
`client.ravenapis.com` and `brp.ravenapis.com` still serve Streamlit until a
separate domestic React cutover is performed.

## Runtime scripts

- `ops/scripts/run_client.sh`
- `ops/scripts/run_web.sh`
- `ops/scripts/run_backend.sh`
- `ops/scripts/run_osrm_stack.sh`
- `ops/scripts/export_osrm_env.sh`

## Required API Keys

Set these in `ops/env/local.env` or the shell environment before running local
services:

- `AMAP_API_KEY`
- `KAKAO_REST_API_KEY`
- `GOOGLE_GEOCODE_API_KEY`
- `DEEPSEEK_API_KEY` for AI Audit

## Operations

- Architecture overview:
  - `docs/architecture.md`
- Local development and release workflow:
  - `docs/development-release-workflow.md`
- Fresh deployment overview:
  - `docs/deployment-overview.md`
- Major user-facing updates:
  - `docs/updates.md`
- Codex handoff:
  - `docs/session-handoff.md`

## Current country support

- China
  - geocoding: AMap
  - routing: city-specific OSRM datasets
- South Korea
  - geocoding: Kakao
  - routing: South Korea OSRM dataset

## Important note

The live OSRM containers should keep mounting datasets from the local machine data directory rather than this Git repository. That separation keeps the repository small and avoids committing large generated routing artifacts.
