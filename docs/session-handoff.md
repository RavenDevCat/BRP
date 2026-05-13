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

- `docs/current-product-overview.md`

Current note:

- `docs/current-product-overview.md` was refreshed against the live codebase on 2026-04-17 and now explicitly includes:
  - dual service direction support
  - flexible baseline fleet labels
  - nearby private-access analysis
  - `Further Most Stop` scenario
  - richer map / post-package output behavior
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

- `docs/backlog.md`
- `docs/implementation-roadmap.md`
- `docs/route-reallocation-design.md`
- `docs/traffic-assumptions-design.md`
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
2. update `docs/backlog.md` if the change affects future work
3. update `docs/implementation-roadmap.md` if the main sequence changes

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
