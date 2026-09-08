# Historical Measurement Review Contract

## Scope And Ownership

`measurement_reviews.py` defines bounded remeasurement requests and their worker
entry point. It is not a route solver. A selected-route review captures an
explicit selection of saved routes, measures those same directed stops through
`FreshRouteProvider`, and records a new comparison without updating the source
job or its result. Full Direct-to-School and Audit corrections have explicit
scopes and reuse their existing domain pipelines, as described below.

The saved-source adapters cover Route Audit current, Strict and Protected
scenarios, and Direct-to-School current-route measurements. Unsupported result
types require their own explicit adapter; do not infer an arbitrary nested
object to be a route or silently claim coverage of other tool histories.
Upload previews without saved inputs are not historical jobs.
Scheduled/running inputs, absent results and unsupported adapters return an
explicit unavailable risk summary, never a healthy zero-risk claim.

The risk pool consists of routes lacking unified evidence, using an older
measurement contract, or retaining unresolved quality flags. Risk is not proof
that a road is invalid. The summary exposes route identifiers and reasons, not
student addresses or coordinates. A request captures only selected routes,
their original measurement/reference fields, ordered points, vehicle identity,
direction, and available dwell/window inputs. Missing nodes are retained as
input failures, never omitted; zero-passenger stops are still route points.

## Source Integrity

Every request includes the source job, requesting administrator, idempotency
key, result digest, selected-input digest and measurement contract version.
The runtime store reconstructs the request from the saved source inside a
transaction before inserting it. Source changes, different requests reusing
the same key, ambiguous route IDs and selections outside the risk pool fail.
Editing a saved request cannot bypass the worker's digest/version validation.

`route_measurement_reviews` is a separate SQLite table. Inserting or completing
a review never updates the source job. Repeating an identical submission returns
the existing review; a deliberate later sample gets a new request key and row.
Source deletion cascades reviews through the existing job lifecycle rather than
leaving orphaned private snapshots.

## Selected-Route Execution And Results

Requests select 1-20 distinct routes and explicitly cap provider calls at 1-500.
A worker owns one fresh provider/cache context. The shared AMap budget includes
failed requests and diagnostic comparisons. A canceled request does not start
provider work; cancellation in flight prevents subsequent work and late result
publication, but cannot recall a request already sent to the map service.

The store atomically claims queued work with a worker token. Only that running
worker can checkpoint or finish it. Terminal/canceled records reject later
writes. Completed reports must cover exactly the selected route keys and retain
their source digest. Unexpected worker exceptions preserve the last checkpoint
and record only the exception type, never a credential-bearing URL.

Each route retains `before`, `after`, signed `delta`, saved measurement evidence,
selection reasons and input failures. Missing data stays null. Planned OSRM
values remain separately labelled references; they cannot become historical
live values simply because the latter are absent. Unverified evidence does not
populate certified after-measurement totals. Known dwell is added without
changing the source convention; unknown dwell does not become zero.

These comparisons are fresh traffic samples, not controlled before/after
experiments or a claim that a smaller number proves a software improvement.
The selected-route review does not recalculate student classification, additional
removal, solver feasibility or full AM/PM acceptance. Its result explicitly
sets `student_classification_recomputed` and `time_window_revalidated` to false.
Consumers must use the existing domain gates before presenting any such claim.

## API Authorization

The job API exposes `GET /jobs/{job_id}/measurement-risk` and
`GET /jobs/{job_id}/measurement-reviews`, with a detail endpoint at
`/jobs/{job_id}/measurement-reviews/{review_id}`. Every read authorizes the parent
job through its existing owner/admin/workspace rules. A review ID under a
different parent returns not found. Summary responses omit worker tokens,
process IDs, filesystem paths and the private selected-point snapshot.

`POST /jobs/{job_id}/measurement-reviews` requires an administrator and the
existing backend service authorization. Its strict body supplies `route_keys`,
`request_key`, `provider_call_limit`, and boolean `confirm_provider_calls: true`.
The default `mode: selected_routes` requires 1-20 explicit `route_keys`.
Unknown fields and implicit numeric/string conversions are rejected. Coordinates
and ownership come from the saved source, never from a submitted override.
Repeated identical submissions return the existing row rather than new work.

`POST /jobs/{job_id}/measurement-reviews/{review_id}/actions/{action}` accepts
administrator-only `pause`, `resume` and `cancel` commands. Control stays in the
environment that enqueued the review; sharing the runtime database does not
authorize a different environment to adopt or terminate its process.

## Result UI

Audit and Direct-to-School result pages share a historical-measurement workspace.
The default remains the original result. Selecting a correction changes the
native result, map data and report export together; Audit query keys include
the correction identity, and Direct-to-School uses the correction's saved
analysis geometry. The separately labelled original-statistics shortcut still
exports the preserved source. Corrected results do not inherit original
multi-day comparisons or solver/deep-verification action panels.

Risk reads also expose `full_review` availability and aggregate scope, built
from the same native request validation as submission. This does not enqueue
work, call a provider, expose coordinates or modify the source. Missing saved
inputs keep the risk visible but disable full correction.

Only administrators see creation and pause/resume/cancel controls. Creation
requires opening the form and confirming the saved-input scope and a 1-500 API
attempt limit. A failed retry with unchanged inputs reuses its idempotency key.
Ordinary viewers inherit source read access but cannot start provider work.

Queued, running and diagnostic-only records are not full-result replacements.
Finalized partial native snapshots remain inspectable with an explicit warning;
unresolved measurements are never a time-window pass. Comparisons retain null
values and explain that fresh traffic may contribute to before/after changes.

## Full Direct-to-School Corrections

An administrator can explicitly select `mode: full_direct_school` at the same
creation endpoint, omitting `route_keys`. This is not a partial route selection:
it includes every route and service address from the saved workbook, including
routes absent from a partial previous result. The supported bound is 1-200 routes
and the same explicitly confirmed 1-500 provider-attempt budget. A request does
not extend that budget automatically to complete a large workbook.

The source must be a finished China AMap Direct-to-School analysis with saved
workbook inputs and original parameters. Missing student limits, dwell, clock
windows or direction are rejected instead of receiving current defaults.
Ambiguous order, inconsistent school placement and fractional/missing passenger
counts are rejected. Zero-passenger waypoints remain service stops, not school
terminals. Neither pickup coordinates nor stop ordering is replaced.

The request captures immutable prepared inputs, normalized original settings,
their digest and the previous conclusion. The only overridden parameter is the
new administrator-approved provider budget. Full correction calls the existing
`run_direct_school_analysis` pipeline with an injected bounded provider. It does
not implement another classifier, removal ranking, time-window policy or solver.
Direct trips, current per-student rides, primary-removal routes and additional
removal routes all pass through the shared measurement provider. AM downstream
and PM upstream ride calculations and longest-direct-trip-first removal order
remain the existing analysis behavior.

`route_measurement_review_snapshots` persists review-owned road snapshots under
the active worker token. A route snapshot may be reused after a pause only if
its full point/reference key and evidence version match, it remains fresh from
the earliest measurement time, and its leg measurements reconcile with its
total. Expired or uncertain snapshots are not accepted. Call reservations remain
persisted, including interrupted calls. Derived student classifications are
recomputed from the inputs; an old classification is not a measurement cache.
Deleting the original job cascades these private snapshots with its review.

The appended envelope has `scope: full_direct_school_result` and contains the
native `analysis_result`, route comparisons, conclusion differences and changed
address categories. Missing original numbers remain null, not zero. Final
publication validates original route/address coverage, each route occurrence's
student count, and unchanged parameters. Shared addresses retain their separate
route-specific student counts rather than replacing them with an address total.
`student_classification_recomputed` indicates the classification pipeline ran;
`classification_complete` and `time_window_revalidated` separately report whether
its evidence is complete. An exhausted budget or uncertain measurement produces
a `needs_review` record and partial analysis with unknowns, not a fully verified
or within-window conclusion. Complete measurement does not itself mean that
every route satisfies the window.

Authorized source readers can download a finished full correction at
`GET /jobs/{job_id}/measurement-reviews/{review_id}/export`. It reuses the native
Direct-to-School statistics workbook, retaining student bases, original/final
ridership, unknown values and the one-passenger yellow warning. Additional
comparison and classification-change worksheets describe what changed, with
the three student groups in business order. These are fresh traffic samples,
not a controlled proof that code changes alone improved the result. Reading or
exporting never requests new map measurements and never changes the original.

## Full Audit Corrections

`mode: full_audit` includes all three saved scenarios: Current, Strict and
Protected. It requires the original Current Plan baseline, point pools, ordered
route nodes, leg references, physical capacities and acceptance settings. The
bound is 1-200 routes across the scenarios and 1-500 explicitly confirmed
provider attempts. Missing whole scenarios are rejected. A saved empty candidate
remains unresolved, not a new infeasibility proof. Legacy Strict routes without
an explicit route ID use the native Audit display identity and fixed saved
sequence; collisions are rejected and the original route object is unchanged.

`audit_measurement_review.py` injects the shared bounded provider into
`attach_final_route_traffic_gate`. It then reuses the existing final time-impact
validator against the freshly measured Current Plan, capacity/stop-count/vehicle
saving feasibility report and Protected exception rules. Original points,
vehicles, student counts, route order and frozen roles are retained. This does
not invoke the assignment/order solver or prove a new minimum fleet count.
The native effective duration-limit helper supplies the original operating
buffer; afternoon acceptance still uses the configured departure/end window.

Audit's original dwell convention is preserved: dwell is charged on arrival at
a non-school node. The morning route's first pickup is its origin, so its dwell
is not included in the route total; afternoon service destinations count.
Direct-to-School retains its own existing dwell convention. Review adapters
must validate and reuse their source convention rather than harmonizing it by
silently adding or removing a stop's dwell.

Full Audit uses the same active-token snapshots, freshness, persistent budget,
pause/cancel and queue rules as full Direct-to-School. Disabled measurement is
unavailable in a correction, not permission to certify an old result. Missing
measurements clear old gates and expose unknown totals. Old search, acceptance
and ancillary recommendations are retained only as source-reference context.
Protected output reports both all-route failures and non-frozen acceptance;
an unchanged frozen exception cannot disappear from the comparison report.

The appended `scope: full_audit_result` contains a native `audit_result`, route
comparisons and scenario acceptance changes. Its primary and legacy aliases
share the corrected result. Current assessment totals and scenario averages
use verified road measurements. Raw solver route/financial metrics remain
planning references, explicitly marked by `measurement_summary`; consumers
must not display these as corrected live measurements. Final integrity checks
retain every scenario and original route/point identity. `complete` indicates
evidence and gate coverage, not that all plans passed. Partial coverage is
`needs_review`; minimum-fleet and old search-completeness claims are not renewed.

Authorized source readers can obtain corrected Audit map payloads at
`GET /jobs/{job_id}/measurement-reviews/{review_id}/map-data/{scenario_key}`
and the workbook at the shared review `/export` endpoint. Only finalized reviews
are eligible. Map payloads reuse the existing Audit map builder and saved
geometry/time-impact evidence. The selected scenario and Current baseline must
have complete measurements or the map endpoint returns unavailable. Corrected
map headline duration/distance use verified total route measurements, not the
legacy drive-time or planning fallback. Exports show original/corrected acceptance and
road measurements, followed by the native Strict/Protected student time-impact
worksheets. Comparison route labels reuse Audit's existing lineage/display
rules; stable review keys remain separate. Unknowns remain unavailable. Viewing or exporting never requests
new road measurements or changes the source result.
If a candidate or its Current baseline lacks complete measurements, its native
student detail sheets are replaced by an availability explanation; the measured
route comparison remains available. Old planning values are not student evidence.

## Queue And Recovery

`MeasurementReviewQueue` runs under the existing job scheduler, not a second
independent scheduling loop. It acquires the shared host concurrency gate.
Ordinary queued jobs, including released scheduled jobs, run first and can
preempt an owned review process. At most one review worker lease is active in
the runtime database. Terminal status alone does not prove that a process has
exited, so its lease remains until reaped. No database-wide risk scan or automatic
review creation occurs when deploying this code or reading a result.

Manual pause becomes `paused` after process exit and requires explicit resume.
Foreground preemption becomes `queued` after exit and resumes when capacity is
available. Resume retains completed route checkpoints and the persisted number
of provider calls. Each provider attempt reserves its budget atomically before
outbound I/O; an interrupted attempt remains counted even if its response or
route checkpoint was lost. Only unfinished routes are remeasured, with the
remaining original budget and a new run-local cache. This completed-route
checkpoint rule describes selected-route comparisons; full correction follows
the review-owned snapshot freshness policy above. Partially measured routes
may repeat already attempted legs; the persistent cap bounds that cost.

Cancellation and pause prevent further reservations and late checkpoints, but
cannot recall an already sent request. Capacity is released only after observed
process death, never after a timeout waiting for termination. A unique per-claim
slot owner prevents a stale reaper from releasing a replacement worker's slot.
After backend restart, reconciliation waits for the attach grace period and
does not kill a PID it does not own. A known dead worker without a terminal
checkpoint becomes failed, keeping evidence and budget; it is not automatically
retried. A still-live unowned worker can observe persisted cancellation/pause
cooperatively; the new backend does not forcibly preempt it using a saved PID.

## Remaining Consumer Boundaries

Selected-route APIs return measurement comparisons, not fully corrected source
results. Consumers must show the applicable scope, measurement times,
request budget and outstanding input clarification. Other tool-history adapters
and the administrator/result UI require their own integration. This includes
selecting appended corrected results and replacing legacy UI reads of raw
solver metrics with saved measurement evidence. Full backend correction does
not establish browser, mobile or exported HTML acceptance. No consumer may treat
prepared coordinates as automatically re-geocoded or move an entrance to obtain
a shorter route.

## Verification

Run `tests/test_measurement_reviews.py`, `tests/test_measurement_review_api.py`
and `tests/test_measurement_review_queue.py`, plus `tests/test_full_measurement_review.py`
and `tests/test_audit_measurement_review.py`,
with runtime-store, API-shell,
shared route evidence, Direct-to-School and queue regression tests. The review
tests use synthetic routes and temporary SQLite files, including concurrent
create/claim, immutable source/result, cancellation races, budget exhaustion,
missing stops, stale inputs and the real shared provider with substituted I/O.
Queue/API tests additionally cover source access, strict confirmation, schema
upgrade, interrupted-call budgets, pause/resume, ordinary-job priority, launch
failure and process-death recovery. These tests do not establish live pickup
correctness or browser acceptance.

Full correction tests exercise both service directions, all three student
groups, zero-rider waypoints, one remaining rider, equality with the existing
analysis pipeline, bounded partial results, pause recovery, immutable source,
snapshot expiry/ownership/cascade, strict input validation and the real workbook
exporter using only synthetic inputs and substituted map I/O.

Audit tests cover native AM/PM dwell, empty and legacy-ID candidates, unchanged
pickup identities, physical capacity and vehicle-saving failures, student-impact
rejection despite passing road windows, Protected frozen/all-route distinction,
missing evidence, pause/resume, saved-map reads and native workbook parity.
