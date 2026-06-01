# BRP Updates

This document tracks major user-facing product and operations updates.

It is not a code changelog. Record changes here when users or operators should know that behavior, available tools, service providers, or recommended rerun guidance changed.

## 2026-06-01

### Environment Workflow Reset

- Redefined the operating model: operator workstations are connection/testing
  workspaces, CN owns staging and domestic production, and KR is a separate
  final production landing target.
- `staging.example.com` is the stage-only test endpoint.
- `$CN_PROD_HOST` and `$KR_PROD_HOST` are final production endpoints
  and should not be repointed or restarted during staging work.
- Future domain replacement should be handled through DNS, Cloudflare Access,
  tunnel ingress, environment variables, and private inventory rather than
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

- The React job history workspace no longer lets the History panel occupy the whole screen on smaller windows.
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

## Update Log Guidance

Add a new dated entry when a change affects user workflow, operational behavior, or interpretation of results, such as:

- a new user-facing tool or workflow
- a service provider change
- routing/geocoding/provider logic changes
- planner assumptions or optimization behavior changes
- report or audit interpretation changes
- migration notes that require users to rerun jobs for updated conclusions

Do not record routine refactors, local-only environment fixes, dependency churn, or small copy/style tweaks unless they change how users should operate or interpret BRP.
