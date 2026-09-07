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

## Integration Boundaries

The runtime primitives do not independently start a scheduler, scan a database,
send provider requests on deployment, or expose a public API. A scheduler must
acquire the existing host-wide concurrency slot before `execute_saved_review`
and prioritize ordinary jobs. Process interruption requires explicit recovery;
an unobserved worker must not trigger automatic repeated billed requests.

Public adapters must require administrator authorization to enqueue or stop
work, authorize the source job for every read, and preserve workspace access
without copying private snapshots to broader audiences. They must also show the
selected-route scope, measurement times, request budget and outstanding input
clarification. No adapter may treat the source's prepared coordinates as
automatically re-geocoded or move an entrance to obtain a shorter route.

## Verification

Run `tests/test_measurement_reviews.py` with `tests/test_runtime_store_sqlite.py`,
shared route evidence, Direct-to-School and queue regression tests. The review
tests use synthetic routes and temporary SQLite files, including concurrent
create/claim, immutable source/result, cancellation races, budget exhaustion,
missing stops, stale inputs and the real shared provider with substituted I/O.
These tests do not establish live pickup correctness or browser acceptance.
