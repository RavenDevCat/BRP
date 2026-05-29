# Session Handoff

This file is the rolling handoff note for future coding sessions.

Update it after each meaningful implementation round so a new session can quickly recover:

- product direction
- current architecture
- implemented behavior
- active constraints
- known risks
- immediate next step

## Product Direction

The product is no longer just a route generator.

Current intended positioning:

- import the supplier's or operator's current school-bus scheme
- audit whether the current plan is operationally reasonable
- benchmark it against increasingly fair system baselines
- produce explainable route-to-route optimization suggestions
- support supplier pricing / route-count / fleet-count negotiations with evidence

Reference overview:

- `docs/architecture.md` for the maintained system architecture.
- `docs/development-release-workflow.md` for local development, staging, production, and current server operations.
- `docs/deployment-overview.md` for fresh-environment setup.

Current note:

- Old planning docs were removed in favor of the maintained docs above. Keep future process and architecture updates in those files plus this handoff.
- `apps/client/app.py` now presents `Current Plan Audit` in a more report-style layout:
  - "Audit Story" summary block
  - improvement-path / action-oriented narrative ahead of technical evidence
  - detailed route diagnostics and move tables pushed into expanders
  - Excel-based audit download replaced by a generated PDF audit report download
- job submission now supports a user-editable `Job Name` field:
  - defaults to the workbook stem
  - is stored in job metadata
  - Job History and job detail captions prefer `job_name` over raw source filename

## Current Architecture

### Frontend

- `apps/client/app.py`
  - Streamlit UI
  - workbook intake
  - baseline assumption controls
  - report-style results presentation
- `apps/client/client_core.py`
  - client-side workbook parsing
  - payload preparation
  - backend submission
  - map rerender from structured backend results
- `apps/client/client_runtime.py`
  - geocoding helpers
  - cache usage
  - HTML map rendering
  - now geocodes Korean addresses from original user input without auto-normalization or alias recovery

### Backend

- `apps/backend/backend_service.py`
  - thin HTTP wrapper around planner execution
- `apps/backend/planner_core.py`
  - main orchestration layer
  - current-plan normalization
  - audit metrics
  - baseline generation
  - route-reallocation analysis
- `apps/backend/BusingProblem.py`
  - legacy routing engine
  - OSRM matrix and geometry work
  - OR-Tools route solving

### Documentation

- `docs/architecture.md`
- `docs/development-release-workflow.md`
- `docs/deployment-overview.md`
- `docs/session-handoff.md`

## Input Contract

The current-plan workflow now expects only two workbook sheets:

### `current_plan_assignments`

Required columns:

- `route_id`
- `stop_sequence`
- `bus_type`
- `country`
- `city`
- `address`
- `passenger_count`

Rules:

- the first row of each route is the depot / school / destination
- the first row of each route should have `passenger_count = 0`
- all routes should share the same depot address

### `current_plan_fleet`

Required columns:

- `bus_type`
- `seat_count`
- `vehicle_count`

Purpose:

- current-plan audit uses these as the factual supplier vehicle assumptions
- baseline assumptions in the sidebar do not overwrite these imported facts

## What Is Implemented

### Current Plan Audit

Implemented:

- route count
- canonical stop count
- average route distance
- average route duration
- average load factor
- low-load route count
- overlong route count
- route-level diagnostics
- current-plan map rendering is now available alongside baseline maps

Important:

- current-plan route metrics are now displayed as average-per-route values
- not as raw route-total sums
- default target route duration is now `60` minutes
- when a current plan workbook is loaded, baseline fleet settings in the client auto-fill from `current_plan_fleet`

### Baselines

Implemented layers:

1. `Current Plan`
   - replay the imported scheme on the real road network

2. `Like-for-Like Baseline`
   - preserve route count
   - preserve route membership
   - preserve bus mix
   - optimize stop order inside each route

3. `Constrained Improvement Baseline`
   - apply a small set of high-confidence route-to-route transfers
   - move selection is now guided by route-level action signals first, not only raw move score
   - if a route-level signal is strong enough, the baseline can now apply a small compatible move package from the same weak route
   - re-evaluate the adjusted network
   - do not perform a full redesign

4. `Free Optimization Baseline`
   - allow a freer system-generated benchmark
   - now accepts a dedicated free-baseline vehicle ratio input
   - ratio is converted into Large / Mid / Small fleet counts over the total baseline fleet budget
   - result is now returned independently as `free_optimization_baseline`, not only through current-vs-baseline comparison fields
   - used as an upper-bound reference

### Route Reallocation Engine

Implemented:

- weak-route identification
- candidate receiving-route selection
- single-stop / small contiguous cluster transfer testing
- local move scoring
- route-level action labels:
  - `Local improvement`
  - `Consolidation path`
  - `Strong removal path`
  - `Route removable now`

Most recent improvement:

- route-level action signals are no longer based only on one best move
- they now consider whether multiple compatible moves point in the same direction
- this makes removal / consolidation claims more conservative and more credible
- constrained baseline move selection now uses those route-level signals first
- this keeps the "mild improvement" baseline more aligned with the audit judgment layer
- constrained baseline now supports small compatible move packages for strong route-level signals
- package selection is still intentionally bounded to preserve runtime and explainability
- frontend now explicitly shows the grouped constrained transfer packages that were actually applied
- this makes the constrained baseline easier to explain as a concrete action bundle instead of a black-box result
- constrained package selection now explicitly compares competing receiving-route package options for the same weak route
- this reduces the chance that a route gets locked into the first acceptable receiving route instead of the most valuable package-level outcome

### Traffic Assumptions

Implemented:

- profile-based time adjustment
- duration changes only
- distance does not change

Supported profiles:

- `Off-Peak`
- `AM Peak`
- `PM Peak`

Current default logic:

- global defaults exist for all cities
- city-aware defaults are now applied when the workbook clearly belongs to a supported city

Current city-aware support:

- `Shanghai`
- `Beijing`
- `Suzhou`
- `Xian`
- `Seoul`

Current traffic calibration note:

- China defaults were intentionally raised to better match supplier-reported PM route durations while still preserving a modest "ideal operations" discount.
- Shanghai now uses the strongest China PM profile (`1.75x`) so a route that previously landed near `53 min` under PM assumptions should move closer to the `70+ min` range rather than the supplier's `85 min`.

Current route-duration note:

- A bug existed where `Target Route Duration` was only used for audit thresholds and map annotation, not in the OR-Tools baseline solver itself.
- This has now been corrected in `apps/backend/BusingProblem.py` by adding a `Time` dimension to the solver:
  - a soft upper bound is applied at the configured target duration
  - a relaxed hard cap is applied at roughly `125%` of target (with at least `+10 min`)
- This should reduce the prior tendency to over-pack routes purely around seat utilization.

### Address Cleanup Policy

Current behavior:

- the app no longer auto-normalizes or auto-corrects Korean addresses before Kakao geocoding
- Kakao lookup now uses the original user-entered address text plus simple city / country prefixes only
- unresolved addresses are intentionally surfaced back to the user in Diagnostics instead of being silently corrected
- warning output now includes country, city, original address, failure note, and a manual cleanup suggestion

Support tooling:

- `ops/scripts/validate_korean_sheet2_addresses.py`
  - still available as an offline validation utility
  - writes result and failure CSV/JSON artifacts under `tmp/`
  - should be treated as manual analysis support, not automatic runtime correction

### Frontend Presentation

Implemented report structure:

- `Executive Summary`
- `Current Plan Audit`
- `Baseline Scenarios`
- `Maps`
- `Diagnostics`

Top-level result presentation remains tab-based. The temporary bookmark-navigation experiment was reverted.

Current Plan Audit now includes:

- `Route Reallocation Opportunities`
- `Weak Route Review`
- `Route-Level Action Signals`
- `Priority Route-to-Route Actions`
- `Detailed Route-to-Route Move Review`

Constrained Improvement presentation now also includes post-package receiving-route state:

- projected receiving-route stop count
- projected receiving-route passenger count
- projected receiving-route duration / distance
- projected receiving-route load factor
- package summaries now explicitly describe what the receiving route would look like after the package is applied
- a dedicated `Post-Package Route Outcomes` table now compares both sending and receiving routes before vs after each selected package
- package outcomes are now classified into:
  - `Safe merge candidate`
  - `Monitor receiving route`
  - `Receiving route stressed`
  using the configured target route duration plus post-package receiving-route load
- executive findings and constrained-improvement presentation now summarize how many selected packages fall into each merge-readiness bucket

## Important Constraints

### Runtime

Target user expectation:

- roughly `1-2 minutes` is acceptable for realistic audit runs

## Immediate Next Step

The next mainline improvement should focus on making constrained packages smarter, not just more visible.

Priority:

1. improve package scoring when multiple receiving routes compete for the same weak route
2. make route-removal / consolidation confidence more sensitive to package-level outcomes
3. continue tightening business-language explanations in the frontend report

Parallel side track already in progress:

1. keep iterating Korean address normalization until the supplier workbook addresses geocode reliably
2. if Kakao still fails after multiple rule rounds, fall back to external lookup from the workbook's English labels to recover standard Korean addresses

Current Korean-address status:

- first-round cleaning improved early sample hit rate from `1/10` to `3/10`
- second-round rules improved the same early sample to `4/10`
- external lookup plus Kakao-confirmed alias recovery has already rescued several previously failing supplier stops:
  - `유엔빌리지 62 한남리버힐` -> `서울 용산구 유엔빌리지길 62`
  - `까페빈어스-우면지구근린공원하차` -> `서울 서초구 바우뫼로 147`
  - `사평역 1번-반포 Xi Gate 1` -> `서울특별시 서초구 신반포로 270`
  - `금호 서울숲푸르지오 101동앞` -> `서울 성동구 금호로 15`
  - `용산 센트럴해링턴-시티파크103동하차` -> `서울특별시 용산구 서빙고로 17`
  - `리첸시아 뚜레쥬르-초록마을 하차` -> `서울특별시 용산구 백범로 341`
  - `논현 아펠바움2차 1차사거리/임피리얼팰리스호텔하차` -> `서울 강남구 학동로42길 43`
  - `역삼센트럴205동앞승차-동영문화센터` -> `서울 강남구 언주로 337`
  - `서초 푸르지오G-22-장꼬방옆 밀라텔` -> `서울특별시 서초구 서운로 201`
  - `신금호두산위브정문승차-대현교회하차` -> `서울특별시 성동구 행당로 27`
  - `학동로23길 웨덱스/맞은편` -> `서울 강남구 학동로23길 18`
  - `아임애견카페(동광로46길)` -> `서울 서초구 동광로46길`
- those recovered mappings are now stored in `apps/client/cache/korea_address_aliases.json` using `{ \"query\": ... }` entries
- the full supplier workbook (`/Users/developer/Downloads/bus_routes_with_korean_addresses_v2.xlsx`, `Sheet2`, `Bus Stop`) is being revalidated after alias updates
- next likely improvement area is composite POI descriptions:
  - `A - B`
  - `승차/하차`
  - `맞은편/건너편`
  - `아파트 + 동`
  - `역 + 출구 + nearby landmark`
- a plausibility filter was added for Seoul-mode Kakao matches to reject obvious long-distance false positives
- current query expansion now generates stronger Korean POI variants before falling back to raw text

Implication:

- avoid global combinatorial redesign in the audit workflow
- keep route-reallocation search limited and explainable
- prefer local moves and constrained baselines over brute-force network rebuilds

### Data / Modeling Constraints

- current route-removal logic is still heuristic, not exact optimization
- large-vehicle road-access constraints are not implemented yet
- real-time traffic is not implemented; only profile-based time multipliers exist
- Korean pure English postal-style addresses may still be unreliable

### User Experience Constraints

- sidebar baseline settings should remain optional and low-noise
- imported workbook facts should remain separate from baseline assumptions
- free-baseline vehicle-ratio controls should stay scoped only to the free baseline
- suggestions must read like operational advice, not only algorithm output

## Known Risks / Gaps

### Route-Reallocation Logic

Still needs work:

- stronger route-removal confidence logic
- better consolidation-path discrimination
- better move grouping for multi-stop patterns
- better route-level package selection when multiple compatible moves should be applied together across more than one receiving route
- stronger package-level integration so post-package route status feeds higher-level findings more directly

### Performance

Still needs monitoring:

- free optimization can still be the heavy part of a run
- map / geometry rendering can still add latency
- future reallocation upgrades must not explode runtime

### Security

Not implemented yet:

- authenticated user directory
- Cloudflare Access integration
- backend hardening beyond the current setup

## Suggested Immediate Next Step

Best next step:

- continue improving constrained package selection and package-level route-status interpretation

Why:

- the reallocation engine is now producing more stable route-level judgments
- constrained baseline now uses those judgments, can apply small packages, and now exposes package-level post-action summaries
- executive findings and constrained findings now partially reflect package-level post-action summaries
- constrained comparison recommendations are now also package-aware
- the next gap is to validate the wording and behavior on a few realistic audit workbooks, and refine if the narrative feels repetitive or too heuristic
- this is the cleanest path to more trustworthy "mild adjustment" recommendations

Concretely:

1. improve package scoring when there are competing receiving routes
2. validate package-aware constrained recommendations on a few real audit workbooks and tune wording if needed
3. then validate the free-baseline vehicle-ratio workflow on a few real audit workbooks

## Validation Notes

Validation approach to prefer:

- always use the conda environment, not system Python
- preferred Python:
  - `/Users/developer/opt/anaconda3/envs/ortools-env/bin/python`

Preferred checks after each round:

1. `py_compile` on touched files
2. small logic smoke test in `ortools-env`
3. if feasible, run one realistic Seoul workbook audit

## Session Rule

After each meaningful implementation round:

1. update this file
2. update `docs/architecture.md` if the runtime shape or module boundaries change
3. update `docs/development-release-workflow.md` if local/staging/prod commands change
4. update `docs/deployment-overview.md` if fresh-server requirements change

## Latest Fix

- Fixed a Streamlit session-state crash in `apps/client/app.py` after moving `Traffic Assumptions` and `Target Route Duration (min)` under the upload area.
- Root cause:
  - `maybe_autofill_planner_settings_from_current_plan(...)` was still writing to `st.session_state["planner_max_route_duration_minutes"]`
  - but the widget with key `planner_max_route_duration_minutes` is now instantiated earlier on the page
  - Streamlit forbids mutating a widget-backed key after instantiation
- Resolution:
  - removed that late assignment from the autofill function
  - kept the rest of fleet autofill behavior unchanged
- Validation:
  - `'/Users/developer/opt/anaconda3/envs/ortools-env/bin/python' -m py_compile 'apps/client/app.py'`

## Latest Mainline Upgrade

- Started the dual-direction routing upgrade so the product can support both:
  - `From School`
  - `To School`
- This round is intentionally backward-safe:
  - default remains `From School`
  - existing workbooks and current behavior should remain unchanged unless the user explicitly switches direction

What was implemented:

- Frontend:
  - added `Service Direction` selector near `Traffic Assumptions` and `Target Route Duration (min)` in `apps/client/app.py`
  - planner config snapshots and job submission metadata now carry `service_direction`
- Workbook parsing:
  - `apps/client/client_core.py` now parses `current_plan_assignments` according to service direction
  - `From School`: first row of each route must be the shared depot / school row with `passenger_count = 0`
  - `To School`: last row of each route must be the shared school row with `passenger_count = 0`
  - canonical baseline input records are built accordingly
- Backend config plumbing:
  - added `service_direction` to backend `PlannerConfig` in `apps/backend/planner_core.py`
  - `_apply_config(...)` now sets legacy planner global `SERVICE_DIRECTION`
- Assessment / constrained logic:
  - current-plan assessment route replay is now direction-aware
  - like-for-like order optimization for `To School` uses a reversed/transposed ordering strategy and then flips route order back for reporting
  - constrained-improvement route rebuilding is now aware of whether the school/depot row lives at the start or end of each route
  - route-model construction and transfer simulation were updated to avoid assuming the depot row is always first
- Map rendering:
  - both backend `apps/backend/BusingProblem.py` and frontend rerender layer `apps/client/client_runtime.py` now label route direction correctly
  - `To School` maps show the final stop as `School` instead of treating the first stop as the only anchor

Files touched:

- `apps/client/app.py`
- `apps/client/client_core.py`
- `apps/client/client_runtime.py`
- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`

Validation:

- `'/Users/developer/opt/anaconda3/envs/ortools-env/bin/python' -m py_compile 'apps/client/app.py' 'apps/client/client_core.py' 'apps/client/client_runtime.py' 'apps/backend/planner_core.py' 'apps/backend/BusingProblem.py' 'apps/backend/backend_service.py' 'apps/backend/backend_job_runner.py'`

Recommended next step:

- Run one real `From School` workbook and one real `To School` workbook end-to-end.
- Then review whether route-reallocation narrative / wording needs light direction-specific language tuning (for example “final school arrival” vs “outbound route” wording).

## Latest Fix

- Added a unified 10-minute acceptable operating buffer on top of `Target Route Duration (min)` across solving, audit overlong checks, and constrained-package merge-readiness logic.
- Solver change:
  - `apps/backend/BusingProblem.py` now applies the route-duration soft bound at `target + 10 min` instead of the raw target.
- Backend audit/reallocation change:
  - `apps/backend/planner_core.py` now treats routes as overlong only when they exceed `target + 10 min`, and reallocation feasibility / overlong route signals use the same effective limit.
- Frontend interpretation change:
  - `apps/client/app.py` now classifies overlong routes and merge readiness using the same `target + 10 min` buffer, and the UI caption explains the buffer.
- Validation:
  - `/Users/developer/opt/anaconda3/envs/ortools-env/bin/python -m py_compile apps/client/app.py apps/backend/planner_core.py apps/backend/BusingProblem.py`

- Follow-up pass cleaned up residual user-facing wording so `Service Direction` is now reflected in upload instructions, executive summary captions, current-plan audit captions, baseline captions, and backend planner logs.

- Updated the downloadable workbook template so `current_plan_assignments` now shows both a `From School` example route and a `To School` example route, and added a lightweight `template_notes` sheet for direction-specific guidance.

- Fixed the template workbook regression in `apps/client/client_core.py`: the new mixed-direction example had one extra address row, causing a pandas `All arrays must be of the same length` error when downloading the template.

## Latest Fix

- Added configurable baseline bus-type slot labels so the system no longer assumes the uploaded current plan uses the literal names `Large Bus`, `Mid Bus`, and `Small Bus`.
- Frontend changes in `apps/client/app.py`:
  - `Fleet Assumptions` now exposes three editable slot labels:
    - `Large Slot Label`
    - `Mid Slot Label`
    - `Small Slot Label`
  - current-plan autofill now reads uploaded `current_plan_fleet`, sorts imported bus types by seat count descending, and maps them into the three solver slots
  - missing imported slots now default to `0` max-count instead of silently restoring old default fleet counts
  - free-baseline vehicle-ratio labels and formatted summaries now follow the configured slot labels
  - planner config snapshots, rerender config, and job submission config now carry:
    - `large_bus_name`
    - `mid_bus_name`
    - `small_bus_name`
- Backend changes in `apps/backend/planner_core.py`:
  - `_apply_config(...)` now injects dynamic `BUS_TYPE_CONFIGS`, `VEHICLE_FIXED_COST`, `MIN_LOAD_TARGET`, and `MIN_LOAD_PENALTY` into the legacy solver using the configured slot labels
  - planner cache keys now include the three slot names to avoid stale cache collisions
  - rerendering uses the configured labels instead of hardcoded bus names
  - free-baseline metadata now reports configured vehicle ratios under the configured slot labels
  - oversized-route detection now compares against `config.large_bus_name` instead of the hardcoded string `Large Bus`
- Legacy solver change in `apps/backend/BusingProblem.py`:
  - `sort_express_preference(...)` and `sort_regular_preference(...)` now sort by capacity instead of hardcoded bus names, so custom labels still preserve small-first / large-first behavior
- Validation:
  - `/Users/developer/opt/anaconda3/envs/ortools-env/bin/python -m py_compile apps/client/app.py apps/client/client_core.py apps/backend/planner_core.py apps/backend/BusingProblem.py apps/backend/backend_service.py`

- Moved `Download Template` to the right of `Upload Workbook` in `apps/client/app.py` so upload remains the primary action and the template reads as optional help instead of a prerequisite.

- Added v1 nearby-aggregate private-access analysis. Backend now derives `nearby_private_access_analysis` from the nearby-aggregated scenario itself: for each rider folded into a shared nearby pickup, it reports the estimated private-drive time/distance needed to reach that pickup.
- Files: `apps/backend/planner_core.py`, `apps/client/app.py`.
- UI: this analysis now belongs to the nearby-aggregated baseline/results, not Current Plan Audit. The Baseline Scenarios tab shows `Nearby Aggregated Private Access v1`, and the audit export includes a `Nearby Private Access` sheet when available.
- Also fixed the earlier `transpose_matrix` runtime gap in `apps/backend/planner_core.py` by adding a local helper used by direction-aware route metric calculations.

- Extended nearby private-access v1 onto the nearby-aggregated map. Nearby scenarios now carry `outlying_private_access_rows`, and both backend/client map renderers overlay three layers: `Outlying Stops`, `Feasible Pickups`, and `Private Drive Connections` (dashed lines). Files: `apps/backend/planner_core.py`, `apps/backend/BusingProblem.py`, `apps/client/client_runtime.py`, `apps/client/client_core.py`.
- Extended nearby private-access v1 so each cluster center now carries grouped member detail. Backend now returns per-center `clusters` in `nearby_private_access_analysis`, the Baseline Scenarios tab shows a `Nearby Cluster Centers` table, the audit export splits this into `Nearby Private Access Clusters` and `Nearby Private Access Riders`, and the nearby map popup for a cluster center now lists the clustered addresses with their private-drive time/distance to that center.
- Nearby private-access map rendering now also preserves clustered rider locations as visible gray points and draws gray dashed connection lines from each clustered rider to its nearby cluster center, instead of only listing them in summary/popup text.
- Added a new scenario `Further Most Stop`, derived from the nearby-aggregated baseline. It keeps the nearby route structure as the base but converts all stops after the xx-minute mark into `private_drive_stop` entries, draws their OSRM-based private-drive paths back to the xx-minute-mark pickup, exposes a dedicated HTML output/map option, and lists these stops under `Private drive stops` in the map summary panel.

## Multi-Agent Editing Note
- This repo may be edited outside this Codex session as well, including by the user's Kimi extension. Do not assume Codex's last remembered state is the source of truth.
- Before changing behavior, inspect the current on-disk files directly, especially:
  - `apps/backend/planner_core.py`
  - `apps/backend/BusingProblem.py`
  - `apps/client/app.py`
  - `apps/client/client_core.py`
  - `apps/client/client_runtime.py`
- Prefer describing the current file state in handoff notes after each round so future sessions can reconcile Codex changes with Kimi changes.

## Latest Observed Code State
- `apps/client/client_runtime.py`
  - `build_map_summary_html(...)` now includes a `private_access_mode` argument and uses it to switch nearby-cluster wording vs `Further Most Stop` wording.
  - `render_map(...)` now supports:
    - visible gray `Private Drive Stops`
    - OSRM-based `private_drive_geometry`
    - `Further Most Pickups` vs `Nearby Cluster Centers` labeling
- `apps/backend/planner_core.py`
  - `analyze_nearby_private_access(...)` now stores per-rider:
    - coordinates
    - pickup coordinates
    - `private_drive_geometry`
    - `private_access_type`
  - `build_further_most_stop_scenario(...)` exists and is wired into backend results as:
    - `structured_results["further_most"]`
    - `further_most_private_access_analysis`
- `apps/client/app.py`
  - result handling now expects:
    - `further_most_html`
    - `further_most_private_access_analysis`
  - UI now exposes:
    - `Further Most Stop` in `Baseline Scenarios`
    - `Further Most Stop` in `Maps`
- `apps/client/client_core.py`
  - output-path handling and rerender loops already include `further_most`
- The earlier `private_access_mode` NameError appears resolved in current on-disk code because the summary helper now takes that parameter explicitly.

## Lightweight User Layer

- Added a first-pass user layer that delegates registration/login to Cloudflare Access and keeps BRP itself lightweight.
- Frontend identity:
  - `apps/client/app.py` reads `Cf-Access-Authenticated-User-Email` from `st.context.headers`.
  - If the Cloudflare header is absent, it falls back to `BRP_DEV_USER_EMAIL` and then `local@brp.dev`.
  - The current email is shown in the Streamlit sidebar.
- Backend request trust:
  - `apps/client/client_core.py` sends `X-BRP-User-Email` on backend requests.
  - If `BRP_BACKEND_SERVICE_TOKEN` is set, the client also sends `Authorization: Bearer <token>`.
  - `apps/backend/backend_service.py` requires that bearer token for all non-`/health` endpoints when the env var is configured.
- Job ownership:
  - New jobs are saved with top-level `owner_email`.
  - `GET /jobs`, `GET /jobs/<id>`, cancel, and delete are filtered by `owner_email`.
  - Emails listed in `BRP_ADMIN_EMAILS` bypass this filter and can see/manage all jobs, including legacy jobs without an owner.
  - The backend job store can now be moved with `BRP_BACKEND_JOBS_DIR`; the KR Windows runtime uses `state\jobs` under the project root and ignores it in git.
- Environment additions:
  - `BRP_AUTH_MODE=cloudflare`
  - `BRP_DEV_USER_EMAIL`
  - `BRP_ADMIN_EMAILS`
  - `BRP_BACKEND_SERVICE_TOKEN`
- Updated env files:
  - `ops/env/south-korea.example.env` uses a placeholder service token.
  - local env files carry matching local values for the KR deployment.
- Validation:
  - `.\ops\env\local.ps1`
  - `$env:BACKEND_PYTHON -m py_compile apps\backend\backend_service.py apps\client\client_core.py apps\client\app.py`

## AI Audit Report v1

- Added a DeepSeek-backed AI report layer for completed jobs.
- Scope:
  - AI only summarizes deterministic BRP audit outputs.
  - Full address lists are excluded from the prompt.
  - The prompt is bounded to fixed report sections and a maximum character budget.
- Backend:
  - `apps/backend/ai_audit.py` builds a compact fact payload and calls DeepSeek chat completions.
  - `POST /jobs/<job_id>/ai-audit` generates or returns the cached `ai_audit_report`.
  - Generated reports are stored back onto the job JSON as `ai_audit_report`.
- Frontend:
  - `apps/client/app.py` replaces the old `Executive Summary` tab with `AI Audit Report`.
  - The old `Current Plan Audit` tab is renamed to `Audit Evidence` so detailed metrics remain traceable.
  - The AI tab includes generate/regenerate buttons and Markdown download.
- Environment additions:
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_MODEL=deepseek-chat`
  - `BRP_AI_AUDIT_LANGUAGE=English`
  - `BRP_AI_AUDIT_TIMEOUT_SECONDS=90`
- Keep real API keys only in server-local env files; examples contain placeholders only.

## Frontend Modernization Direction

- Streamlit should remain the fast prototype / analysis client for now, but it should not be treated as the long-term production frontend.
- Target production frontend direction:
  - `Next.js`
  - `React`
  - `TypeScript`
  - `TanStack Query` for backend API state / polling
  - `MapLibre` or `Leaflet` for route maps
  - `TanStack Table` or `AG Grid` for large operational tables
  - Tailwind / shadcn-style component patterns for a cleaner app UI
- Rationale:
  - the app now needs richer auth/session behavior, job lifecycle UX, maps, reports, exports, and future fleet-planning workflows
  - those flows are increasingly awkward to maintain in Streamlit
  - keeping FastAPI/backend APIs as the stable contract will let Streamlit and the future web app run side by side during migration
- Suggested migration order:
  1. login / session / user identity display
  2. job submission
  3. job history and job status polling
  4. result tabs and audit evidence views
  5. maps
  6. AI report and printable/exportable report views
  7. Distance Checker
  8. Fleet Planner / future automatic planning workflow
- Near-term rule:
  - continue using Streamlit to validate algorithms and business workflow quickly
  - avoid adding deeply custom UI infrastructure to Streamlit when the same work belongs naturally in the future web frontend

## Automatic Demand Planning Prototype

- Added a first-pass automatic planning prototype behind `Fleet Planner Preview`.
- Scope is intentionally preview-only and does not affect existing audit job submission.
- Files:
  - `apps/client/vehicle_catalog.py`
  - `apps/client/planning_assumptions.py`
  - `apps/client/fleet_selector.py`
  - `apps/client/demand_input.py`
  - `apps/client/demand_clustering.py`
  - `apps/client/demand_routing.py`
  - `apps/client/demand_global_optimizer.py`
  - `apps/client/fleet_planner_page.py`
  - `apps/client/demodata/demand-demo-shanghai-50.xlsx`
- Implemented capabilities:
  - Korea / China default vehicle catalog
  - monitor-seat deduction from listed seats
  - hidden planning modes: `balanced`, `cost_saver`, `comfort_saver`
  - rider-count to recommended-vehicle selection
  - demand workbook template download
  - demand workbook parsing for address + student count inputs
  - Shanghai demand demo workbook with 50 pickup addresses around `上海市静安区南京西路1686号`
  - demand geocode preview with existing geocode cache reuse
  - obvious bad-address detection before provider calls
  - school / student demand map preview
  - demand clustering preview by school direction sector, capacity, and max stop count
  - cluster-level vehicle recommendations and colored map preview
  - OSRM + OR-Tools route preview inside each cluster
  - route preview supports `To School` and `From School`
  - route table reports route distance/time from OSRM road-network metrics
  - generated route preview can be downloaded as a workbook with:
    - `generated_plan_assignments`
    - `generated_plan_fleet`
    - `template_notes`
  - generated plan downloads should be treated as auto plans, not supplier current plans
  - route preview table flags routes that exceed the active mode's duration target
  - if a route exceeds the target, the preview attempts one automatic split by distance from school and reruns the route preview
  - global OR-Tools plan builder:
    - uses all geocoded demand points directly
    - builds a candidate vehicle pool from the catalog
    - uses OSRM road-network matrices
    - enforces vehicle capacity, max route duration, and max stops
    - lets OR-Tools decide selected vehicles and stop assignments
    - exports the same generated-plan workbook format
  - `Submit Global Plan as Job` in `Fleet Planner Preview`:
    - generates an internal in-memory legacy route-plan workbook
    - parses it back through existing `read_current_plan_from_excel`
    - auto-builds `PlannerConfig` from selected vehicle mix
    - prepares the existing backend payload
    - submits to `/jobs`
    - user-facing downloads use `generated_plan_*` sheet names; `current_plan_*` is only an internal compatibility schema
- Current limitation:
  - clustering preview is heuristic only and should be considered a fallback / explanation view
  - global OR-Tools is now the main automatic-planning candidate, but fixed-cost tuning still needs real workbook validation
  - exported generated plan is a reviewable auto-plan workbook, not a certified final operation plan
  - route preview map still draws straight connectors between ordered stops; tabular distance/time comes from OSRM
  - submitted generated jobs are opened from the main planner Job History after refresh; Fleet Planner Preview does not yet jump directly into the job detail
- Validation:
  - `python -m py_compile apps/client/demand_clustering.py apps/client/demand_input.py apps/client/fleet_planner_page.py apps/client/fleet_selector.py apps/client/planning_assumptions.py apps/client/vehicle_catalog.py apps/client/app.py`
  - conda `brp` synthetic clustering smoke test:
    - 3 resolved points
    - 1 bad address excluded
    - 2 clusters generated
    - selected vehicles: `25-seat mini bus / County class`, `35-seat mid bus`
  - local Seoul OSRM on `http://127.0.0.1:5006` returned `code=Ok` for a table request
  - conda `brp` synthetic OSRM + OR-Tools route preview returned:
    - 2 routes
    - total distance about `12.92 km`
    - total duration about `15.6 min`
    - solver labels: `ortools`, `trivial`
  - generated workbook smoke test returned sheets:
    - `generated_plan_assignments`
    - `generated_plan_fleet`
    - `template_notes`
    - fleet rows: 25-seat County x1, 35-seat mid bus x1
  - conda `brp` synthetic global OR-Tools smoke test returned:
    - 44 candidate vehicles generated from KR catalog
    - 2 selected routes
    - selected vehicles: 25-seat County x1, 35-seat mid bus x1
    - total distance about `12.92 km`
    - total duration about `15.6 min`
  - submit-chain smoke test without posting to backend:
    - global plan -> internal legacy workbook -> `read_current_plan_from_excel`
    - parsed as `To School`
    - route count `1`
    - fleet summary `25-seat mini bus / County class: 24 seats x 1`
    - generated `PlannerConfig` large slot matches the selected vehicle
  - Shanghai demand demo workbook validation:
    - `apps/client/demodata/demand-demo-shanghai-50.xlsx`
    - sheets: `demand`, `template_notes`
    - 50 demand rows
    - 107 total students
    - 0 workbook warnings
    - geocoding was not run during validation to avoid provider/API usage

### Naming Constraint

- Do not conflate Current Plan and Generated / Auto Plan.
- `Current Plan` means a real user/supplier operating plan uploaded for audit.
- `Generated Plan` / `Auto Plan` means a system-generated plan from demand inputs.
- `current_plan_assignments` and `current_plan_fleet` are legacy parser sheet names only.
- User-facing generated-plan downloads must use `generated_plan_assignments` and `generated_plan_fleet`.
- Internal submission may temporarily adapt generated plans into the legacy parser schema, but UI/docs/job labels should keep the concepts separate.

## Multi-Server OSRM Deployment Constraint

- Future staging/production may use three or more servers, and each server may host only a subset of OSRM regions.
- Do not require every server to configure every OSRM container after pulling code.
- `ops/scripts/run_osrm_stack.sh` now supports:
  - `OSRM_ENABLED_REGIONS=auto` to start only regions whose dataset files exist
  - explicit subsets such as `OSRM_ENABLED_REGIONS=south-korea` or `OSRM_ENABLED_REGIONS=shanghai,beijing`
- Server-local env files should keep only the `OSRM_BASE_URL_*` endpoints that this server can actually serve or proxy.
- For staging/production, set `OSRM_USE_BUILTIN_DEFAULTS=false` so unsupported regions fail clearly instead of falling back to local development default ports.
- Runtime endpoint resolution in both backend routing and client distance tools honors explicit `OSRM_BASE_URL_*` env values before any built-in local defaults.

## Windows Local Development Handoff

- Development has moved back from server-side editing to local Windows / Mac development.
- Windows local repo path currently used:
  - `C:\Users\ted.fu\OneDrive - EiM\python stuff\busing routing designer`
- Windows toolchain observed:
  - Git: `C:\Program Files\Git\cmd\git.exe`
  - Conda root: `C:\Users\ted.fu\AppData\Local\anaconda3`
  - Python env: `C:\Users\ted.fu\AppData\Local\anaconda3\envs\ortools_env\python.exe`
- Windows local OSRM access uses SSH local forwarding to the domestic server:
  - `127.0.0.1:5002` Shanghai
  - `127.0.0.1:5003` Beijing
  - `127.0.0.1:5004` Suzhou
  - `127.0.0.1:5005` Xian
  - `127.0.0.1:5006` South Korea
- Added Windows helpers:
  - `ops/scripts/import_local_env.ps1`
  - `ops/scripts/start_osrm_tunnel.ps1`
  - `ops/scripts/run_backend.ps1`
  - `ops/scripts/run_client.ps1`
- Local Windows `ops/env/local.env` remains ignored by Git and should carry machine-local values only.
- For local history browsing, `BRP_DEV_USER_EMAIL` should be an admin email if legacy jobs have empty `owner_email`.
- Historical jobs copied from Mac / server may carry absolute `output_paths` from another machine. Client-side rerendering now rewrites historical map outputs into the current checkout under `apps/client/outputs/<job_id>/`.

### Current Git Working Tree Note

- This repo may show many modified files on Windows because of line-ending metadata or prior edits from Mac / Kimi / server-side work.
- Before the next feature implementation, inspect `git status --short` and `git diff --stat`.
- At this handoff point, known newly added Windows-development files are the four PowerShell helpers under `ops/scripts/`.
- The cross-machine output-path compatibility fix is in `apps/client/client_core.py`.

## React Frontend Migration Handoff

- New isolated React preview lives in `apps/web`.
- It is a side-by-side migration target and does not replace Streamlit yet.
- Local ports:
  - backend API: `127.0.0.1:8001`
  - Streamlit production client: `127.0.0.1:8501`
  - React preview: `127.0.0.1:5173`
- Windows helper:
  - `ops/scripts/run_web.ps1`
  - auto-finds the winget-installed Node LTS path if `npm` is not yet on PATH
- Unix/Mac helper:
  - `ops/scripts/run_web.sh`
- Backend now accepts additive `/api/*` aliases for the React app:
  - `/api/health`
  - `/api/me`
  - `/api/jobs`
  - `/api/jobs/<job_id>`
- First React slice:
  - dashboard
  - job history
  - job detail JSON/result preview
  - build verified with `npm run build`
- Current implementation round added a functional current-plan submission path:
  - `/api/workbooks/preview`
    - accepts JSON `{ file_name, file_base64, config }`
    - parses `current_plan_assignments` and `current_plan_fleet`
    - returns summary, fleet facts, suggested planner config, and subway aggregation block reason
  - `/api/workbooks/submit`
    - accepts JSON `{ file_name, file_base64, config, job_custom_name }`
    - reuses Python `apps/client/client_core.py` preparation logic server-side
    - performs current-plan parsing, geocoding/cache reuse, aggregation prep, job metadata assembly, and `/jobs` creation
  - React route `/new`
    - workbook file input
    - service direction / traffic / target duration controls
    - optional custom job name
    - subway and nearby baseline toggles
    - validates workbook and autofills fleet slot assumptions
    - submits to backend and opens the created job detail
- Smoke validation:
  - previewed `apps/client/demodata/current-plan-assessment-test-shanghai.xlsx`
    - 3 routes
    - 10 assignment rows
    - 8 planning rows
    - fleet auto-filled to `Large Bus: 45 seats x 3`
  - submitted smoke job `4007db50d052`
    - name: `current-plan-assessment-test-shanghai - react-submit-smoke`
    - final status: `succeeded`
  - browser smoke checked `/new` and `/jobs/4007db50d052`

### React Deployment Notes

- CN and KR servers do not need changes while React remains preview-only.
- First production switch is not just `git pull`; it needs one deployment routing step:
  - build `apps/web/dist`
  - serve static files through nginx/cloudflared/backend static hosting
  - route preview or main domain to React static frontend
  - keep API traffic pointed to local backend `127.0.0.1:8001`
- Long-term preferred deployment:
  - build React in CI or on a dev machine
  - deploy static `dist` to servers
  - avoid installing Node/npm on production servers unless staging convenience requires it
- Safer rollout:
  - add a preview domain/path first
  - keep Streamlit on `8501`
  - switch the primary domain only after upload, submission, result rendering, AI audit, downloads, and rollback checks are at parity
- Weekend handoff expectation:
  - user may continue from Mac through OneDrive-synced repo
  - before handing over, sync this file plus `docs/development-release-workflow.md` with the final state of the React migration
