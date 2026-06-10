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
- Do not use or recreate a separate Windows or OneDrive BRP project checkout for
  development.
- CN staging is the active development and test checkout.
- CN production and KR production are release targets only.
- When taking over work, inspect CN staging first: checkout revision, tracked
  status, relevant service health, and recent runtime behavior.
- Work directly in CN staging, validate there, commit and push the intended Git
  revision, then promote only when the user explicitly asks.
- Real server addresses, usernames, private hostnames, and concrete access
  paths belong only in the ignored private inventory or approved private backup.

## Current Release State

- Interactive Route Audit map polish is included through `bd6379e` (`Surface route status in map list`); later docs-only commits may advance `main` beyond that product revision.
- CN staging frontend `apps/web/dist` was rebuilt from `bd6379e` and staging
  responded `200 OK` at the protected React origin during verification.
- Tonight's work has not been promoted to CN production or KR production unless
  the user explicitly asked elsewhere after this handoff.
- Commit authorship on the CN/KR-maintained history was corrected on 2026-06-09:
  the prior Ubuntu-authored commits from `8eb1b52` onward were rewritten to
  `Raven <catsradios@gmail.com>`, with backup tag
  `backup/main-before-author-rewrite-20260609`.
- CN staging and KR repo configs now set future commits to
  `Raven <catsradios@gmail.com>`.
- CN staging now includes the OSRM elevated-road snap fallback in
  `apps/backend/BusingProblem.py`. The backend retries very short but obviously
  overlong OSRM legs with original geocode coordinates and records
  `coordinate_source=raw_geocode_fallback` when that route is selected.
- Staging job `ed918d069752` was patched in place for review: `21-fromschool`
  leg 4 to 5 (`长宁路999号` to `长宁路63号`) changed from the 北横通道 detour
  to a local-road route. A backup was kept next to the job JSON in shared
  runtime storage.
- Live traffic sampling supports `baseline_json` sources. CN staging Shanghai
  AM/PM timer env now points at shared runtime baseline JSON instead of Route
  Audit job seeds, so missing historical job files should no longer break the
  timer. The baseline loader geocodes through the existing cache and computes
  OSRM durations before AMap sampling.

## Completed 2026-06-09 Evening

Route Audit Maps tab polish:

- Added React MapLibre interactive route map MVP while retaining legacy HTML
  map fallback.
- Added structured map-data endpoint usage in React Maps tab.
- Added scenario summary tiles and removed the old duplicated scenario button row.
- Fixed map artifact refresh issue caused by an undefined private-access mode
  variable in legacy map summary rerendering.
- Improved selected-route viewport focus so selecting a route scrolls/fits the
  map into the current browser viewport.
- Added route list search, quick filters, and filter hit counts.
- Added natural numeric route label ordering so route labels sort `1,2,3...10`
  instead of `1,10,11...2`.
- Added stop hover/click priority, stop details, selected stop popups, and stop
  sequence drill-in.
- Made selected-route stops larger and visually distinct; softened non-selected
  context stops into grey points.
- Added route context toggle to switch between contextual routes and selected
  route isolation.
- Added selected-route direction arrows.
- Added route hover summaries, bottom selected-route summary card, route status
  badges, and route list accent markers for long/high-load/capacity routes.

Operational/documentation work:

- Rewrote Ubuntu-authored commits to Raven authorship and aligned CN staging and
  KR source checkouts to the new `main` history.
- Set CN and KR local Git author configs to Raven.
- Recorded the remaining map-product backlog in public updates and private
  handoff material.

## Open Product Backlog

Recommended next Route Audit map polish:

- Improve route display naming so user-facing labels read like `Route 1`, with
  raw route IDs preserved in secondary text.
- Revisit selected route card placement or collapse behavior so it does not hide
  map content or stops.
- Show demand batch metadata in stop sequence when oversized same-address demand
  was split into solver batches.
- Tune map base style/contrast so dense city labels compete less with routes.
- Add wider transparent route hit areas so hover/click does not require precise
  line targeting.
- Add scenario delta metrics such as route count saved, distance delta, and
  longest-route delta against current plan.
- Revisit route context toggle default behavior and narrow viewport layout.
- Decide what the upper-right map popup/control should show after the map polish pass; current behavior needs product review.
- Decide how users should download/export the polished interactive map style, since the legacy HTML download does not capture the new React map presentation.

Other known product/routing follow-ups:

- Existing completed jobs are immutable snapshots. When geocoding or baseline
  modeling changes, affected jobs need to be rerun for corrected maps/results.
- The OSRM snap fallback currently affects final route geometry/time enrichment.
  If solver ordering still appears distorted by elevated-road snaps, apply the
  same fallback policy to matrix-building or candidate-edge costing.
- Domain replacement and SSO remain blocked pending company cyber/security code
  review; continue product polish until that is cleared.

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
- update `AGENTS.md` for new-session startup warnings and current focus
- update `README.md` for product-level surface changes
- update `docs/architecture.md` for stable design or module boundary changes
- update `docs/development-release-workflow.md` for run/deploy/check changes
- update `docs/deployment-overview.md` for fresh-environment requirements
- update `docs/updates.md` for user/operator-facing behavior or rerun guidance
- update ignored private inventory and the OneDrive private backup when server
  access, service maintenance, or production restart facts change
