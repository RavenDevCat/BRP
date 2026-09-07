# Historical Measurement Review Contract

## Scope And Ownership

`measurement_reviews.py` defines bounded remeasurement requests and their worker
entry point. It is not a route solver. A review captures an explicit selection
of saved routes, measures those same directed stops through `FreshRouteProvider`,
and records a new comparison without updating the source job or its result.

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

## Execution And Results

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
The measurement review does not recalculate student classification, additional
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
Unknown fields and implicit numeric/string conversions are rejected. Coordinates
and ownership come from the saved source, never from a submitted override.
Repeated identical submissions return the existing row rather than new work.

`POST /jobs/{job_id}/measurement-reviews/{review_id}/actions/{action}` accepts
administrator-only `pause`, `resume` and `cancel` commands. Control stays in the
environment that enqueued the review; sharing the runtime database does not
authorize a different environment to adopt or terminate its process.

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
remaining original budget and a new run-local cache. Partially measured routes
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

The APIs and queue return measurement comparisons, not fully corrected source
results. Consumers must show the selected-route scope, measurement times,
request budget and outstanding input clarification. Full domain-gate and
student-classification recomputation, remaining tool-history adapters, and an
administrator/result UI require their own integration. No consumer may treat
prepared coordinates as automatically re-geocoded or move an entrance to obtain
a shorter route.

## Verification

Run `tests/test_measurement_reviews.py`, `tests/test_measurement_review_api.py`
and `tests/test_measurement_review_queue.py` with runtime-store, API-shell,
shared route evidence, Direct-to-School and queue regression tests. The review
tests use synthetic routes and temporary SQLite files, including concurrent
create/claim, immutable source/result, cancellation races, budget exhaustion,
missing stops, stale inputs and the real shared provider with substituted I/O.
Queue/API tests additionally cover source access, strict confirmation, schema
upgrade, interrupted-call budgets, pause/resume, ordinary-job priority, launch
failure and process-death recovery. These tests do not establish live pickup
correctness or browser acceptance.
