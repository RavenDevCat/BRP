# BRP Updates

This document tracks major user-facing product and operations updates.

It is not a code changelog. Record changes here when users or operators should know that behavior, available tools, service providers, or recommended rerun guidance changed.

## 2026-06-20

### Korea Route-Level Traffic Attribution Verified

- South Korea Route Audit jobs can now use the same route-network attribution
  model as the China deployment: historical Kakao Navi samples are matched to
  each route by route fingerprint and Seoul-metro geography instead of applying
  one flat market coefficient.
- Seoul, Incheon, Gyeonggi, and nearby Seoul-metro cities are grouped into the
  shared `Seoul Metro` attribution bucket for traffic-factor matching.
- KR production now defaults new jobs to attributed traffic coefficients.
- A full Monday-Friday Kakao profile refresh produced 345 geo-ready route
  samples, and a representative KR Route Audit verification job confirmed
  route-level factors in Current Plan, Free Optimization Baseline, and
  15-minute constrained scenarios.
- Existing completed jobs keep their stored results; rerun a job to regenerate
  route timings with the new KR attribution behavior.

### KR Windows Backend Compatibility Hardened

- The OSRM manager module is now import-safe on Windows as well as Linux.
- This prevents KR backend restarts from failing on POSIX-only file-lock imports
  while preserving Linux file locking for on-demand OSRM environments.
- KR production should continue to start the backend through the `BRP Backend`
  Scheduled Task.

## 2026-06-14

### Fleet Planner Map Workspace Completed

- Fleet Planner history results now use the shared React MapLibre route map
  instead of the old embedded HTML map panel.
- The Fleet Planner results workspace is consolidated into `Plan`, `Map`, and
  `Review` tabs so generated plans, route maps, and supporting input checks are
  easier to find.
- Fleet Planner maps now include the same map actions users expect elsewhere:
  Open fullscreen map, Download interactive map, and Download workbook.
- The fullscreen Fleet Planner map opens in-page with a close control instead
  of navigating users to a separate raw HTML page.
- Fleet Planner route maps now refit to the available route geometry and stops
  when the map loads, reducing cases where users had to click a route before
  seeing the route area.
- Legacy Fleet Planner history records that have route data but no structured
  map data are hydrated by the backend so compatible old runs can still render
  in the interactive map. Very old records without enough route detail may need
  to be rebuilt.

### Bangkok Market Routing Foundation

- Added Bangkok/BK routing support as the current Thailand-market focus.
- Bangkok workbooks use Google geocoding by default and route through the
  Bangkok OSRM endpoint when available.
- Bangkok route timing currently uses a conservative all-day static traffic
  multiplier until richer Bangkok traffic-profile sampling is implemented.
- The OSRM scripts and environment examples now include Bangkok/BK names while
  keeping Thailand aliases for backward compatibility with older workbooks or
  local env files.

### Bangkok Google Geocode Relay

- Added a narrow Google geocode relay adapter for Bangkok/BK only, intended for
  environments that cannot call Google directly while Thailand-market testing is
  still hosted from existing servers.
- The relay requires a bearer token, keeps its own usage counter, enforces
  daily/monthly caps, and rejects non-Bangkok requests by default.
- Route Audit and shared client geocoding only use the relay when
  `BRP_BK_GEOCODE_MODE=google_relay` is explicitly enabled.

### Production Sync

- CN production and KR production were promoted to the same runtime release as
  staging after validation.
- Production frontend assets were built once on staging and then synced to
  production targets; production servers did not run ad-hoc frontend builds.
- Existing completed jobs remain immutable snapshots. Rerun jobs when users
  need route plans rebuilt under the new Fleet Planner/Bangkok behavior.

## 2026-06-13

### Time Impact Decision Review Completed

- The Time Impact tab now starts with an operations-facing decision card for each optimized scenario: Acceptable, Review needed, High risk, or Incomplete.
- The decision card explains why the scenario needs attention using acceptance rate, over-threshold riders/stops, high-risk stops, route changes, and unmatched timing coverage.
- Operators can jump directly from the decision card to over-threshold stops, high-risk stops, or the route that contains a top impacted stop.
- This is a review workflow only. Time-impact warnings and recommendations do not alter solver capacity, route assignment, geocoding, or cache behavior.

### Input Address Review Warnings

- Route Audit now surfaces accepted input addresses that may still need human review.
- The Summary tab shows a prominent warning when accepted addresses appear outside the expected service area, unusually far from the school, or suspicious in the route sequence.
- The Review tab lists the affected address, workbook row references where available, accepted geocode details, route context, and the suggested workbook check.
- Route-context review can now flag large adjacent-stop detours, stops far from the adjacent-stop corridor, isolated stops, and stops that appear backwards relative to neighboring stops.
- These warnings are informational quality checks: they do not block solving, delete cache entries, auto-correct coordinates, or treat normal cross-district travel inside a large city as an error.
- Existing completed jobs are immutable snapshots. Rerun an audit to generate the new address-review warnings under the updated backend logic.

### KR Weekday Traffic Profile Refresh

- Live traffic sampling now supports Kakao Navi future directions for KR in
  addition to the existing CN AMap route API, with generic
  `total_api_duration_s` sample summaries so the planner can consume either
  provider.
- South Korea/KR traffic profiles are designed as Kakao predicted
  Monday-Friday profiles from stable baseline JSON exports, not daily realtime
  live timers. Google Routes was removed from the KR plan after production
  probes returned empty Seoul driving routes.
- Route-level traffic attribution now treats Seoul, Incheon, Gyeonggi, and
  nearby Seoul-metro cities as one reusable Seoul Metro profile bucket, so KR
  Kakao samples can support cross-boundary school routes instead of requiring an
  exact workbook city match.
- Kakao Navi calls are guarded by independent monthly, daily, and per-refresh
  caps plus a persistent usage counter separate from the Google geocode counter.
- Added Linux/staging and Windows/KR wrappers for refreshing KR weekday profiles
  with To School anchored to 08:00 arrival and From School anchored to 15:40
  departure.
- The KR wrappers now support an `all` period that refreshes AM peak, PM peak,
  and the 11:00 off-peak profile in one Monday-Friday weekly batch.
- KR production is intended to run this full batch every Sunday at 08:00 server
  local time. The expected current scale is about 405 Kakao Navi future-route
  calls per weekly refresh, so the Kakao-specific daily and per-refresh safety
  caps default to 500 while the monthly safety cap remains 4000.
- Windows/KR backend defaults now point live traffic sample and baseline
  directories to the repository-local `state` runtime folders, so Kakao samples
  generated by the weekly task are visible to route-audit planning even when
  `BRP_LIVE_TRAFFIC_SAMPLE_DIR` is not explicitly set.
- KR weekday samples are matched by weekday during the workweek; weekend opens
  keep the latest weekday profile available for review instead of filtering all
  profiles out.

## 2026-06-12

### Route Audit Result Workspace Consolidated

- Route Audit job results are now organized around four user-facing tabs:
  `Summary`, `Plans`, `Impact`, and `Review`.
- Legacy comparison scenarios that users did not need to interpret directly
  have been removed from the visible workflow. New audits focus on Current
  Plan, Free Optimization Baseline, and the `15-Minute Constrained` plan.
- The Impact summary has been condensed into fewer conclusion-oriented metrics
  so operations teams can see acceptance, over-threshold riders, typical
  adverse impact, and worst/high-risk stops without scanning repeated numbers.
- Route map actions now keep the primary downloads together: Open map, Download
  map, and Download workbook are available from the map action group.

### Time Impact Review Started

- Route Audit results now include a Time Impact tab for compatible completed
  jobs with optimized route scenarios.
- The first review surface compares current-plan stop timing against optimized
  scenario timing and summarizes worse riders, high-risk stops, weighted adverse
  minutes, route-changed riders, P90 adverse impact, and maximum adverse impact.
- Users can switch between optimized scenarios, filter stop impacts, focus one
  route from the route-impact table, search stops/routes, and export an Excel
  workbook for operations review.
- The Time Impact review now treats an adverse pickup/dropoff shift of 15
  minutes or less as acceptable and reports over-threshold stops/riders,
  acceptance rate, and maximum over-threshold minutes.
- Route Audit now also produces a `15-Minute Constrained` optimized scenario
  after Free Optimization Baseline. It reruns optimization with a hard
  15-minute adverse time-impact limit for matched stops, so users can compare
  the most aggressive free optimization against a more family-acceptable
  constrained plan.
- If the 15-minute constrained solve is infeasible, the audit should still
  complete and mark only that scenario as skipped while preserving the free
  optimization result for comparison.

## 2026-06-11

### Legacy Streamlit Client Removed

- The Streamlit client UI (`apps/client/app.py`, `distance_checker_page.py`,
  `fleet_planner_page.py`) has been removed. The React frontend in `apps/web`
  is now the sole user-facing UI.
- `apps/client/` continues to serve as the shared Python helper library for
  workbook processing, geocoding, caching, demand clustering/routing, and fleet
  planning — all modules used by the backend API routes are intact.
- The `run_client.sh` and `run_client.ps1` scripts now emit a deprecation
  message and exit; they are kept as stubs to fail early if any automation still
  references them.

### Interactive Route Map Rollout Completed

- Route Audit Maps now presents the React MapLibre interactive route map as the
  user-facing map experience; the legacy HTML preview/toggle is no longer shown
  in the product UI.
- The map Open action launches an in-page fullscreen viewer with a glass-style
  route list, route expand/collapse, route filters/search, route focus, stop
  inspection, and route/stop hover details.
- The map Download action now exports a standalone interactive HTML map named
  from the job history label and scenario/map name. The exported file embeds
  route and stop data, but still needs network access for MapLibre CDN assets
  and OpenStreetMap tiles.
- Map action buttons are solid white controls over the map for better legibility.
- No rerun is required for compatible completed jobs because the interactive map
  reads existing structured job results. Rerun only when users want results
  rebuilt under newer planner or geocoding behavior.

### Korean Language Support

- The frontend now supports Korean (한국어) as an alternative UI language.
  English remains the default.
- A language toggle (`EN` / `한`) appears in the sidebar on staging and KR
  production servers. CN production hides the toggle by default.
- Server behavior is controlled by `BRP_DISABLE_LANGUAGE_SWITCH`:
  unset or `false` shows the toggle; set to `true` hides it.
- Translated pages include the navigation shell, dashboard, job history,
  job detail, route audit results, and the interactive route map.
  Untranslated strings fall back to English.
- All translation keys are maintained in `apps/web/src/lib/i18n/en.ts` and
  `ko.ts`. Add new keys to both files when introducing user-facing text.

## 2026-06-09

### Side Tool History Rails Unified

- Fleet Planner History and Distance & Cost History now use the same compact
  desktop rail interaction as Route Audit History.
- Users can open the rail to inspect saved runs, and it collapses after selecting
  a run or clicking back into the workspace so the tool surface keeps focus.

### Interactive Route Map Polished

- The Route Audit Maps tab now uses the React MapLibre interactive map as the
  primary route inspection surface for compatible completed jobs, while keeping
  legacy HTML maps available through the existing fallback mode.
- Scenario summary tiles replace the old repeated scenario button row and show
  route count, stop count, riders, total distance, and longest route at a glance.
- Route inspection now includes route search and filters, natural numeric route
  label ordering, selected-route focus, route context toggle, selected-route
  direction arrows, route hover summaries, bottom route summary cards, status
  badges for long/high-load/capacity routes, and softer context stops.
- The Maps Open control now launches an in-page fullscreen interactive map viewer with a close control and download action instead of opening a raw HTML artifact in a new browser tab; the legacy HTML preview is no longer user-facing.
- Stop inspection now prioritizes stop hover/click over route lines, keeps
  selected-route stops visually prominent, and shows stop address, riders, and
  cumulative timing/distance details.
- No rerun is required for compatible completed jobs because the interactive map
  reads existing structured job results. Rerun only when users want results
  rebuilt under newer planner or geocoding behavior.

### Interactive Route Map MVP Started

- Route Audit map work is moving from backend-rendered Folium iframe artifacts
  toward a React-native interactive map.
- The first pass keeps the legacy HTML maps as a fallback while adding a
  structured map-data API and an Interactive / Legacy toggle in the Maps tab.
- MVP scope: Route Audit job results only, scenario switching for existing map
  outputs, route list, route focus, stop markers, stop detail popups, and
  fit-to-route controls.
- Backend source of truth remains existing completed job `structured_results`;
  no rerun is required to view an interactive map for compatible completed jobs.

### Oversized Pickup Batching Balanced

- Route Audit now splits oversized pickup demand using the smallest capacity-feasible
  batch count plus a target-load check instead of filling one nearly full batch
  and leaving a tiny remainder.
- The default target is 70% of the largest comfort capacity, with a small cap on
  extra batches to avoid over-fragmenting a single pickup point.
- This applies to original stops and aggregated subway/nearby pickup points
  before solver execution. Display still preserves the original pickup identity
  through demand batch metadata.
- Existing completed jobs are immutable snapshots. Rerun affected jobs when users
  want route results rebuilt under the balanced batching rule.

## 2026-06-08

### China Geocoding Hardened For OSRM Cities

- China geocoding now normalizes the OSRM-covered China cities before calling
  AMap: Shanghai, Beijing, Suzhou, and Xi'an.
- AMap city constraints use city adcodes instead of relying on English city
  names, reducing nationwide false matches for ambiguous road names.
- Accepted AMap results are checked against city adcode prefixes, city aliases,
  and broad city bounding boxes before being cached or used.
- Polluted CN shared geocode cache entries from earlier false matches were
  removed after backup.
- Existing completed jobs are immutable snapshots. Rerun affected jobs to
  refresh maps and route results under the corrected geocoding rules.

### Sidebar Version Marker

- The React sidebar can show the current frontend build version as a short Git
  hash.
- This helps operators confirm which code revision a user is seeing when
  staging, production, or KR appear out of sync.
- The marker is build-time metadata; frontend assets must be rebuilt from the
  intended Git revision for the displayed version to be meaningful.

### Side Tool History Discoverability

- Fleet Planner and Distance & Cost history rails now make the collapsed
  history affordance clearer when users enter those pages.
- Existing side-tool history files remain compatible.

### Stale React Asset Repair

- A deployment mismatch was corrected where server checkouts were current but
  public React `dist` assets were still built from older frontend code.
- CN staging is now the canonical React build host for release artifacts.
- Future frontend releases should verify marker strings or the visible version
  in the built `apps/web/dist` before copying assets to production targets.

## 2026-06-04

### Distance & Cost Histories

- Distance & Cost now keeps separate React history panels for Reference Distance and Route Cost runs.
- Reference-distance checks and route-cost calculations auto-save to their matching history after a successful run.
- Saved Reference Distance and Route Cost runs can be reopened or deleted from the side-tool workspace, and include submitter metadata.
- Switching between Reference Distance and Route Cost after opening saved history now clears incompatible result state, preventing cross-tab history records from crashing the workspace.
- Existing Route Audit and Fleet Planner jobs do not need to be rerun.

### Fleet Planner Generated Workbook Sheet Names

- Fleet Planner generated plan downloads now use `current_plan_assignments` and `current_plan_fleet` sheet names.
- Downloaded optimized-plan workbooks can be uploaded directly into New Audit without manually renaming sheets.

### Shared Runtime History And Cache Paths

- Added configurable `BRP_CLIENT_CACHE_DIR` and `BRP_BACKEND_CACHE_DIR` runtime paths.
- CN staging and CN production can share Route Audit history, side-tool history, Distance & Cost history, provider caches, and backend route caches while keeping separate code checkouts and services.
- Added an ops merge helper for moving existing runtime data into shared directories without deleting the original folders.

### Fleet Planner Vehicle Profile Preview

- Added a staging React vehicle profile editor to Fleet Planner run settings.
- Users can adjust vehicle names, listed seats, category, diesel/electric type, available count, and enabled state for a single Fleet Planner run.
- Custom vehicle profiles are sent to fleet preview, demand grouping diagnostics, grouped route preview, and global OR-Tools planning.
- Fleet Planner History saves the vehicle profile snapshot used for each run. Existing history remains usable; rerun only when users want results rebuilt under a changed vehicle profile.

### Fleet Planner Route Time Target

- Added an explicit `Route Time Target` control to the React Fleet Planner run settings.
- The target is passed into fleet preview assumptions, demand grouping diagnostics, grouped route preview, and global OR-Tools route planning.
- Fleet Planner History saves and restores the route-time target for each saved run.
- Existing Fleet Planner history remains usable; rerun saved scenarios only when users want plans rebuilt under a different time target.

## 2026-06-02

### AI Audit Report Readability

- Tightened the AI audit prompt so generated reports read more like concise
  management briefings and less like raw model templates.
- The React report viewer now strips common Markdown noise such as horizontal
  rules and orphan quote markers.
- Existing jobs do not need to be rerun, but regenerating an AI audit can use
  the improved report style.

### Staging SSO Preparation

- Added environment-driven auth provider reporting through `/api/auth/config`.
- The React shell now reads auth button targets and provider display text from
  the backend instead of assuming one fixed logout path.
- Admin authorization remains local through `BRP_ADMIN_EMAILS`.
- Existing jobs do not need to be rerun.

## 2026-06-01

### CN Frontend Standardized On Nginx

- Replaced the CN React frontend origin process with Nginx for both staging and
  production.
- Nginx now serves `apps/web/dist`, handles direct navigation fallback, proxies
  `/api/*` to the correct backend, injects the backend service token
  server-side, and returns 401 when the Cloudflare Access user header is absent.
- Existing jobs do not need to be rerun.

### KR Public Frontend Standardized On Nginx

- Replaced the KR public React origin process with local Nginx on port `8501`.
- The old public Python static/proxy task is disabled; KR public traffic now
  reaches React and same-origin `/api/*` through Nginx.
- Existing jobs do not need to be rerun.

### Public OSRM Hostnames Retired

- Removed public OSRM tunnel routes from the CN access configuration.
- CN and KR application services already use local OSRM loopback endpoints, so
  routing requests no longer need public OSRM hostnames.
- Existing jobs do not need to be rerun.

### CN Production React Promotion

- Promoted CN production to the current GitHub `main` revision and switched the
  production frontend origin to the React static/proxy service.
- `$CN_PROD_HOST` now serves the React frontend from the production origin
  while keeping production jobs, caches, outputs, and environment files separate
  from staging.
- The origin access guard is enabled on production; requests that bypass the
  access layer return 401.
- Existing jobs do not need to be rerun.

### Environment Workflow Reset

- Redefined the operating model: local checkouts are code-record and testing
  workspaces, CN owns staging and domestic production, and KR is a separate
  final production landing target.
- `$CN_STAGING_HOST` is the stage-only test endpoint.
- `$CN_PROD_HOST` and `$KR_PROD_HOST` are final production endpoints
  and should not be repointed or restarted during staging work.
- Future domain replacement should be handled through DNS, Cloudflare Access,
  tunnel ingress, environment variables, and operator-maintained deployment records rather than
  hard-coded application behavior.

### React Static Proxy Access Guard

- Added an optional `BRP_REQUIRE_CLOUDFLARE_ACCESS` guard for public React
  static/proxy hosts.
- When enabled, requests that reach the origin without the Cloudflare Access
  user header return 401 instead of serving the app or API.
- Existing jobs do not need to be rerun.

### Planner Job Concurrency Guard

- Added an optional backend queue guard for planner jobs through
  `BRP_MAX_CONCURRENT_JOBS`.
- On shared hosts, staging and production can point at the same
  `BRP_JOB_CONCURRENCY_DIR` so the configured limit is host-wide.
- This protects OSRM and server memory by keeping extra submitted jobs queued
  instead of starting every heavy planner worker immediately.
- Existing completed jobs do not need to be rerun.

## 2026-05-30

### Unprotected Domestic Client Host Disabled

- Disabled the direct domestic legacy client hostname so the Streamlit client is no longer exposed without access control.
- The protected domestic app hostname remains available behind Cloudflare Access.
- This is an access-control change only; existing jobs and results do not need to be rerun.

### Product Name Standardized

- Standardized the product name to `BRP: Bus Route Planner`.
- The main workflow is named `Route Audit`.
- Auxiliary workflows are organized under `Side Tools`.

### React Preview Adds Distance & Cost

- Added the first React preview slice for Distance & Cost at `/distance`.
- The first slice supports reference-stop distance checks from uploaded Excel workbooks.
- Users can preview workbook sheets and columns, choose address/city/country fields, select road or straight-line distance mode, and review result tables.
- Current-plan route cost is now available in the React preview. Users can map route/order/bus columns, choose market cost defaults, and calculate per-route distance and one-way diesel cost.

### React Preview Adds Fleet Planner First Slice

- Added the first React preview slice for Fleet Planner at `/fleet`.
- Users can download the demand template, upload a demand workbook or enter rider groups manually, choose market and planning mode, and preview recommended vehicles plus estimated fleet mix.
- Demand workbook geocode preview is now available in the React preview, including summary metrics, result table, and map rendering.
- Demand clustering preview is now available in the React preview, including direction-sector selection, cluster metrics, cluster/stop tables, and map rendering.
- OSRM route preview is now available in the React preview, including to-school/from-school direction, route metrics, route/stop tables, and map rendering.
- Generated plan workbook download is now available for route preview and global plan results.
- Global OR-Tools planning is now available in the React preview, including generated route metrics, candidate vehicle count, stop detail, map rendering, and workbook download.
- Global plan job submission has been added to the React preview; it creates a normal backend planner job from the generated global plan.

### React Cutover Preparation

- Documented the production serving plan for the React frontend.
- React should first be deployed behind a preview hostname before replacing the current Streamlit public hostname.
- The production-style React host must serve static build assets, support direct navigation to app routes, and proxy `/api/*` to the backend from the same hostname.
- This is an operator/deployment update only; existing user jobs do not need to be rerun.

### KR React Preview Deployment

- Deployed the React preview stack to the KR server for QA.
- KR React preview serves the static React build and proxies `/api/*` to the KR backend from the same host.
- This is a preview/operator deployment only; existing user jobs do not need to be rerun.

### Backend Job History Persistence Hardened

- Backend job history now defaults to repository-level runtime storage under `state/jobs` instead of the code package directory.
- Startup scripts set the job store path explicitly so pull/restart workflows do not accidentally create an empty history store.
- If `index.json` is missing or empty, the backend rebuilds history from existing job JSON records.

### KR Public Frontend Switched To React

- The KR public frontend origin now serves the React frontend instead of Streamlit.
- KR historical jobs, cache, and generated outputs were migrated from the older KR checkout into the active checkout.
- This is a deployment/runtime switch only; existing user jobs do not need to be rerun.

### KR Runtime Migration Verified

- Completed the KR runtime migration beyond job history: AI/geocode keys, Distance & Cost cache, backend cache, generated outputs, and demo workbooks are now present in the active KR checkout.
- Verified the KR React frontend, backend proxy, historical job detail, map artifact refresh, template downloads, and AI Audit service call.
- Existing user jobs do not need to be rerun for this migration; AI Audit reports can be generated from the migrated history.

### AI Audit Model Restored

- AI Audit defaults and KR configuration were restored to `deepseek-v4-flash` instead of `deepseek-chat`.
- AI Audit output token budget is now configurable through `BRP_AI_AUDIT_MAX_TOKENS`.
- Existing route audit jobs do not need to be rerun; regenerate AI Audit reports when users want conclusions produced by the restored model.

### Local Development Runtime Rebuilt

- Rebuilt a local development runtime around a dedicated Conda environment.
- Created the `brp` conda environment for backend and Streamlit client development.
- Restored local historical job browsing after moving the project into a stable
  development checkout.

### React Job History Responsive Fix

- The React job history workspace no longer lets the History panel occupy the whole screen on smaller viewports.
- Narrower layouts collapse History by default when viewing a selected job, reducing user confusion.

### Route Audit Assumptions Clarified

- Route Audit optional assumption panels now show that fleet, vehicle mix, route policy, and aggregation settings are optional.
- Opening an optional panel without changing fields does not override workbook defaults.
- When users edit optional assumptions, the panel is marked `Custom` and can be reset to the workbook/default baseline.
- `Run audit` now validates the uploaded workbook automatically before submission, while `Validate workbook` remains available as an optional preview check.
- Existing jobs do not need to be rerun; this is an intake workflow and presentation update.

### KR Google Usage Counter Restored

- The React shell can show the KR Google geocode usage counter when `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`.
- The counter remains hidden on non-KR deployments by default.
- The counter is persistent runtime state; future Google geocoding calls should increase it, and deployments should preserve the current value rather than resetting it to a past baseline.
- Google geocode usage updates now reserve quota through a cross-process lock, so concurrent users or multiple Python service processes do not lose increments or overshoot the monthly cap because of stale read/write races.

### External API Throttling Hardened

- Kakao, Google, AMap, and DeepSeek calls now share cross-process rate limiter state, so concurrent jobs do not multiply each provider's QPS by the number of Python worker processes.
- Jobs still run in parallel; only the external provider request gate is globally paced.
- Existing jobs do not need to be rerun.

### OSRM Elevated-Road Snap Fallback Added

- Route Audit now detects obviously inflated short legs where the normal plot/WGS coordinate snaps to a high-speed or elevated road layer and creates a large detour.
- For those legs only, the backend retries OSRM routing with the original geocode coordinate and keeps the shorter route when it materially improves distance or duration.
- The chosen leg records `coordinate_source=raw_geocode_fallback` plus the original plot-route distance/duration for auditability.
- CN staging job `ed918d069752` was patched so `21-fromschool` leg 4 to 5 (`长宁路999号` to `长宁路63号`) no longer loops through 北横通道; future jobs pick up the fix automatically after backend restart.

### Live Traffic Timers Moved To Baseline JSON

- Live traffic sampling now supports a stable `baseline_json` source in addition to historical Route Audit jobs and Fleet Planner runs.
- Shanghai AM/PM and the Suzhou sample timer inputs can point at shared runtime baseline JSON files, so timers no longer depend on temporary job JSON records or Fleet Planner run records that may be cleaned up.
- The baseline loader geocodes the template stops through the existing cache and computes OSRM route durations before sampling AMap, preserving the same traffic-factor output shape.
- Existing samples remain valid; future timer samples should use the configured baseline source.

### Comfortable Load Target No Longer Reduces Vehicle Capacity

- Comfortable priority still reports the configured comfort target, currently 85% of physical seats, as a planning indicator.
- The route solver, base-plan capacity checks, fleet trimming, and oversized-stop splitting now use physical vehicle capacity as the hard limit.
- A route can exceed the comfort target without being marked overloaded as long as it remains within physical seats; maps now label this as seats plus comfort target instead of treating the target as capacity.
- Existing jobs do not change automatically; rerun audits to get the corrected capacity behavior.

## Update Log Guidance

Add a new dated entry when a change affects user workflow, operational behavior, or interpretation of results, such as:

- a new user-facing tool or workflow
- a service provider change
- routing/geocoding/provider logic changes
- planner assumptions or optimization behavior changes
- report or audit interpretation changes
- migration notes that require users to rerun jobs for updated conclusions

Do not record routine refactors, local-only environment fixes, dependency churn, or small copy/style tweaks unless they change how users should operate or interpret BRP.
