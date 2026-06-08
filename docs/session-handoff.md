# Session Handoff

This is the short recovery note for a new Codex session. Keep stable design in
`docs/architecture.md`, operating steps in `docs/development-release-workflow.md`,
fresh-server setup in `docs/deployment-overview.md`, and major user/operator
updates in `docs/updates.md`.

## Current Product Shape

- Product name: `BRP: Bus Route Planner`.
- Main workflow: `Route Audit`.
- Side tools: `Distance & Cost`, `Fleet Planner`.
- React in `apps/web` is the primary frontend.
- `apps/client` remains as the legacy/operator UI plus shared Python helpers
  for workbook intake, geocoding, caches, and map/result rendering.
- `apps/backend` is the job API and planner execution layer.

Current React routes:

- `/`: dashboard
- `/new`: new Route Audit job
- `/jobs`: audit history workspace
- `/jobs/$jobId`: job detail
- `/distance`: Distance & Cost
- `/fleet`: Fleet Planner

Job result tab order:

1. `AI Audit`
2. `Audit Detail`
3. `Actions`
4. `Baselines`
5. `Maps`
6. `Diagnostics`

## Current Operating Model

- The Windows local machine is only a Codex/control console.
- Do not use or recreate a separate Windows project checkout for BRP
  development.
- CN staging is the active development and test checkout.
- CN production and KR production are release targets only.
- When taking over work, inspect CN staging first: checkout revision, tracked
  status, relevant service health, and recent runtime behavior.
- Work directly in CN staging, validate there, commit and push the intended Git
  revision, then promote only when the user explicitly asks.
- For external `ssh`, `scp`, and Git command composition from Windows, prefer
  Git Bash over PowerShell.
- Real server addresses, usernames, private hostnames, and concrete access
  paths belong only in the ignored private inventory or approved private backup.

## Current Release State

- GitHub `main` and CN staging source are at `43c40de`
  (`Show git version in app sidebar`).
- CN staging frontend `apps/web/dist` has been rebuilt from `43c40de`; the
  sidebar shows the short Git version.
- CN production has the China geocoding hardening release `2c3f841`
  (`Harden China geocoding city constraints`) and backend health was verified
  after restart.
- CN production has not yet received the sidebar version display release
  `43c40de`.
- KR production was not synced to the China geocoding hardening or sidebar
  version display at end of day. Sync KR only after an explicit release decision.

## Completed Today

- Repaired stale React frontend assets that made CN staging, CN production, and
  KR production appear rolled back even though source checkouts were newer.
- Established CN staging as the canonical React build host; CN has Node/npm
  available for release builds.
- Improved Side Tools history discoverability and deployed it through the
  accepted release path.
- Hardened China geocoding for the OSRM-covered China cities:
  Shanghai, Beijing, Suzhou, and Xi'an.
- AMap geocode requests now use city adcodes for those cities, and results are
  checked by adcode, city alias, and bounding box before they can be accepted or
  reused from cache.
- Cleaned polluted CN shared client geocode cache entries. Affected old job
  result files do not change automatically; users should rerun affected jobs for
  corrected coordinates.
- Added a small sidebar version marker in CN staging, sourced from the build
  checkout's short Git hash.

## Open Product Risks / Next Work

- Route Audit `subway baseline` and `nearby baseline` can currently merge
  multiple riders at the same station/cluster into one capacity demand. If that
  merged demand exceeds a vehicle capacity, or exceeds a vehicle's remaining
  capacity when the route reaches that node, the solver can become infeasible.
  Proposed fix: keep the station/cluster aggregated for display, but split the
  solver input into same-coordinate virtual pickup batches no larger than the
  smallest enabled vehicle capacity. Preserve `cluster_id`, `batch_index`, and
  `batch_count`, then merge consecutive same-station batches again for route
  presentation.
- A `To School` job was observed with a `长寿路` stop recognized as Suzhou.
  Do not rush a blind fix. Next session should identify the exact job, source
  row, city/country fields, and whether the result came from old polluted cache
  or a fresh provider call. If it still reproduces after the China geocoding
  hardening, add a Shanghai-specific candidate selection or district/context
  enrichment rule for ambiguous road names.
- The old generated job outputs remain immutable snapshots. When geocoding or
  baseline modeling changes, affected jobs need to be rerun to get corrected
  maps and route results.
- Before deploying the sidebar version display to production, promote
  `43c40de`, rebuild/copy frontend assets through the current release flow, and
  verify the visible version in each touched environment.

## Runtime Data Rules

Never reset or delete runtime data unless the user explicitly asks.

Preserve:

- job store: `state/jobs` or `BRP_BACKEND_JOBS_DIR`
- side-tool history: `state/side_tools` or `BRP_SIDE_TOOLS_DIR`
- client cache: `apps/client/cache` or `BRP_CLIENT_CACHE_DIR`
- backend cache: `apps/backend/cache` or `BRP_BACKEND_CACHE_DIR`
- generated outputs
- server-local env: `ops/env/local.env`
- Google usage: `apps/client/cache/google_geocode_usage.json`
- provider rate-limit state: `state/api_rate_limits` or `BRP_API_RATE_LIMIT_DIR`
- planner concurrency state: `BRP_JOB_CONCURRENCY_DIR`

Provider rules:

- Kakao, Google, AMap, and DeepSeek use cross-process rate limiters.
- Google usage is reserved atomically before actual Google requests.
- OSRM is private BRP-managed routing infrastructure and is not exposed through
  public DNS or Cloudflare Tunnel.

## Deployment Habit

For ordinary code changes:

1. Inspect CN staging checkout and staging runtime first.
2. Work in CN staging.
3. Validate against CN staging services and the protected staging hostname.
4. Commit and push the intended Git revision.
5. Promote CN production only after explicit approval.
6. Sync KR production only after an explicit release decision, using the same
   intended Git revision.
7. For frontend changes, build React from CN staging, verify the generated
   `apps/web/dist` bundle and release marker, then reuse that artifact for
   production targets.
8. Verify frontend, backend, same-origin `/api/health`, job/history visibility,
   runtime counts, and provider usage continuity for every environment touched.

Docs-only changes do not need service restart.

## Documentation Rule

After meaningful implementation:

- update this handoff only for current recovery facts
- update `docs/architecture.md` for stable design or module boundary changes
- update `docs/development-release-workflow.md` for run/deploy/check changes
- update `docs/deployment-overview.md` for fresh-environment requirements
- update `docs/updates.md` for user/operator-facing behavior or rerun guidance
