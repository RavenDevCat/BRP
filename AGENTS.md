# Codex Startup Card

This repository is BRP / Bus Route Planner. Read this file first in every new Codex session, especially on a different machine.

## First Reads

1. Read `docs/session-handoff.md` for the rolling project context.
2. Read `docs/development-release-workflow.md` before running local services or deploying.
3. Read `docs/deployment-overview.md` before fresh-server or environment setup.
4. Read `docs/updates.md` before deciding whether a user-facing change needs a release note.

## Server Names

- KR server: Tailscale Windows host `100.87.225.85`, user `Bus.EiM`, active checkout `C:\Users\Bus.EIM\BRP`.
- CN server: `143.64.19.35`, user `azureuser`.
- Do not confuse the KR Tailscale host with the CN server.

## Runtime Data Safety

Never overwrite, reset, or delete runtime data unless the user explicitly asks:

- `state/jobs`
- `apps/client/cache`
- `apps/backend/cache`
- generated output folders
- `ops/env/local.env`
- `apps/client/cache/google_geocode_usage.json`

When deploying, verify that job history, caches, and env-specific behavior survived the deploy.

The Google geocode usage counter is persistent runtime state. The value `134` was only the known KR count when the counter was restored on 2026-05-30. Future valid Google calls should increase the count. Preserve and verify the current value; do not reset it to `134`.

## KR Deploy Pattern

- Build React locally from `apps/web` with `npm run build`.
- KR currently does not have Node/npm in PATH, so copy local `apps/web/dist` to KR after KR pulls the code.
- Restart the KR scheduled tasks:
  - `BRP-Backend-Preview`
  - `BRP-React-Preview`
  - `BRP-React-Public`
- Verify:
  - backend health
  - public React proxy health
  - Tailscale React preview health
  - `/new` and `/jobs`
  - job count
  - client/backend cache file counts
  - Google usage endpoint when KR has `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`

## Product Naming

- Main product: `Bus Route Planner`.
- Side tool: `Distance & Cost`.
- Result tab order: `AI Audit`, `Audit Detail`, `Actions`, `Baselines`, `Maps`, `Diagnostics`.

## Session Habit

After meaningful implementation rounds, update `docs/session-handoff.md`. Also ask whether `docs/updates.md` should receive a user-facing update when the change affects features, providers, routing/geocoding/planner behavior, or whether users should rerun jobs.
