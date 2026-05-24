# BRP Platform

Unified project layout for the live BRP stack.

This structure separates the public client, the solver backend, operational scripts, and data references so the project is easier to explain, maintain, and deploy.

## Structure

- `apps/client`
  - Streamlit client used by end users
  - handles Excel upload, geocoding, subway lookup, local preprocessing, and result rendering
- `apps/backend`
  - backend compute service
  - handles OSRM matrix building, OR-Tools solving, final route enrichment, and JSON responses
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
  - `apps/backend`
  - `docs`
  - `ops`
  - root files such as `README.md` and `.gitignore`
- Keep out of Git:
  - Python virtual environments such as `.venv/`
  - runtime caches under `apps/client/cache` and `apps/backend/cache`
  - saved backend jobs under `apps/backend/jobs`
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

1. User uploads Excel in `apps/client`.
2. Client geocodes and aggregates stops locally.
3. Client sends prepared payload to `apps/backend`.
4. Backend selects the correct OSRM endpoint by country/city.
5. Backend computes three scenarios and returns structured results.
6. Client renders HTML maps and summary metrics.

## Runtime scripts

- `ops/scripts/run_client.sh`
- `ops/scripts/run_backend.sh`
- `ops/scripts/run_osrm_stack.sh`
- `ops/scripts/export_osrm_env.sh`

## Required API Keys

Set these in your shell environment before running the client:

- `AMAP_API_KEY`
- `KAKAO_REST_API_KEY`
- `GOOGLE_GEOCODE_API_KEY`

## Operations

- Fresh deployment overview:
  - `docs/deployment-overview.md`
- Runbook:
  - `docs/operations-checklist.md`

## Current country support

- China
  - geocoding: AMap
  - routing: city-specific OSRM datasets
- South Korea
  - geocoding: Kakao
  - routing: South Korea OSRM dataset

## Important note

The live OSRM containers should keep mounting datasets from the local machine data directory rather than this Git repository. That separation keeps the repository small and avoids committing large generated routing artifacts.
