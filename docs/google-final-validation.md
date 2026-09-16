# Optional Google Final Timing Validation

## Scope

The task configuration `final_time_validation_mode` is `legacy` by default.
Legacy routing, upload preview, and independent planning tools retain their
existing providers. `google` enables an isolated final validation adapter for
China Route Audit, Fleet, Direct-to-School, Route Insert, and road-time tools;
it does not replace geocoding or the solver matrix.
It is not enabled for Korea. A frontend switch selects the mode per task.
The shared `final_timing.FinalTimingContext` supplies native receipts to the
independent business adapters. Full historical revalidation preserves the
provider and reserves both the review allowance and shared Google quota before
each call. Isolated legacy road correction is not used for Google source runs.

The Google panel selects `timing_policy`: `arrival_anchored` (AM default) or
`fixed_departure`. In fixed mode, `time_window_start` is the exact departure,
not an earliest bound. PM always uses fixed departure. Reference Distance
supports the selected policy and retains fixed departure for older callers
that do not send a policy. The shared panel shows service date, Asia/Shanghai
timezone, mode and prediction interval independently of task execution timing.
Pure distances and fuel arithmetic retain their previous basis; road-time
exports identify Google time and OSRM distance/cost separately. A Google time
does not certify an OSRM geometry. Receipt fields preserve both sources.

Audit excludes initial boarding from route dwell, matching its existing model.
Direct-to-School and Fleet retain their own stop-service conventions. Insert
preserves the saved baseline dwell and adds only the new stops' configured dwell.
Zero-rider service stops remain service stops. Native stop schedules must use
the receipt's actual departure, not a reconstructed default arrival time.

Google mode requires `validation_service_date`. An explicit prediction date is
never overwritten by a scheduled execution date. Older scheduled callers that
omit it retain the scheduled-date default. Submission assigns a server-generated `validation_budget_id`;
persisted retries share that budget. Past departure times are rejected, not
silently moved to another date. Current baseline and candidate timing must have
the same Google provider, service date, and policy before time-impact comparison.

## Isolation and Readiness

The adapter is in `apps/backend/google_final_validation.py`. It performs no
network or database work at import. All of the following must be configured
before the deployment capability becomes available:

- `BRP_GOOGLE_FINAL_ENABLED=true`
- `BRP_GOOGLE_FINAL_DATA_USE_APPROVED=true`
- `BRP_GOOGLE_ROUTES_API_KEY` from the deployment secret store
- `BRP_GOOGLE_FINAL_QUOTA_DB` pointing to a dedicated, persistent quota database

The approval flag records an operator decision; it is not evidence of licensing
permission. China traffic coverage, account billing, permitted use with the
existing non-Google map, and retention/export terms require explicit acceptance
before real activation. Do not copy existing runtime environments into tests.
No production migration or deployment is implied by this implementation.

An existing Google key may be bound to the dedicated Routes setting after an
explicit operator decision. Key presence and a working geocoding relay do not
prove Routes permission. Verify an actual Compute Routes request through the
intended egress before enabling rollout. A relay exposing only geocoding cannot
forward Routes requests without a separately implemented and tested interface.
HTTP 403 alone does not distinguish service activation, key restrictions, or
other project access conditions. Keep rollout disabled until access is verified.

### Restricted Routes Relay

`BRP_GOOGLE_ROUTES_RELAY_URL` selects the separate Routes relay, and
`BRP_GOOGLE_ROUTES_RELAY_TOKEN` authenticates the caller. Unset relay configuration
retains the direct Google transport. Invalid configured relay settings fail
closed; they never trigger direct or legacy fallback. HTTP is allowed only for
explicit private/loopback IP addresses over an operator-protected network;
otherwise use HTTPS. Relay URLs cannot include credentials, paths or queries.

Deploy `ops/relay/google_routes_relay.py` separately from the geocoding service.
Its only routing endpoint is POST `/compute-routes`; destination, field mask,
driving policy and stop-order preservation are fixed. It requires a dedicated
Google key binding, bearer token and `BRP_GOOGLE_ROUTES_RELAY_QUOTA_DB`.
The relay's own persistent cap is supplementary egress protection, not an
additional paid allowance or a replacement for the client's authoritative
task/day/month/campaign reservation. Failed attempts are not refunded and HTTP
redirects and automatic retries are disabled. The relay's success counter means
a native HTTP response, while the client also validates geometry and leg data.

Both client and relay enforce 10,000 attempts per calendar month in Asia/Shanghai.
There is no task, day or lifetime campaign call ceiling. The retired
`BRP_GOOGLE_ROUTES_RELAY_CAMPAIGN_LIMIT` setting is ignored. Preserve both stores
so the current month's existing attempts remain charged. Keep tokens
out of logs and browser settings. `run_google_routes_relay.ps1 -EnvFile ...`
uses an explicitly selected private runtime and environment file on Windows.
No production geocoding or business service needs a restart to install it.

## Timing and Evidence

Audit pickup timestamps denote the start of boarding. The final-gate vehicle
departure excludes origin boarding, matching the existing solver convention.
With a one-minute dwell, a 07:52 pickup and a 07:53 verified departure are
consistent. Intermediate boarding is included once; AM school arrival follows
the verified arrival. In PM, stop labels denote arrival before that stop's dwell;
the final gate includes completion of the final stop service. Map and template
export timestamps must preserve the same convention, not force every pickup or
drop-off label to equal the vehicle departure or service-completion label.

The Google endpoint is Routes API Compute Routes. Requests keep stop order and
use DRIVE with TRAFFIC_AWARE_OPTIMAL. Native per-leg durations, distances, snaps,
and geometry are validated together. They are never represented as measurements
of the AMap geometry. Stop service time is counted once. Nonzero dwell requires
rolling leg requests so each leg uses its actual departure time.

Arrival mode seeds departure from OSRM drive time plus the adapter's dwell
model. It queries that departure and evaluates the measured arrival. Late
arrival triggers an earlier prediction; Google mode has no late-arrival grace.
An arrival up to three minutes early is accepted. An earlier arrival may trigger
a later prediction targeting two minutes early, subject to quota. A measured
late/feasible bracket bounds oscillation. At most four complete rounds are run.
Only a queried departure can pass; no global latest-departure optimum is claimed.
An optional refinement failure or exhausted allowance retains the latest
already-verified feasible departure. Cancellation is never swallowed. Without
a feasible receipt, unavailable data cannot become success; a measured overrun
still reaches the existing solver gate/replan. Fixed mode never shifts departure.
Missing native legs, inconsistent totals, invalid
coordinates, excessive endpoint gaps, quota exhaustion, cancellation, and
non-convergence do not silently become successful legacy validation.

## Request Budget

The shared Google controls show current monthly remaining attempts. Direct-to-School
upload previews also show the minimum initial direct-plus-current-route requests,
calculated by the same rolling-dwell rules as execution. Additional prediction
rounds and removal checks consume the same monthly allowance. Submit and worker
preflight enforce the remaining budget; the estimate is not a completion guarantee.

## Pickup Resolution And Failed Runs

Google timing uses explicit school POI entrance coordinates when available and
plausible, instead of a campus centroid. The normalization is local to Google
execution and never edits geocode caches or historical inputs. Operator-confirmed
points, explicit named gates and existing entrance selections take precedence.
The same resolver is used by Audit and the shared final-timing context; evidence
retains the requested waypoints and entrance provenance. Google-native terminal
coordinates still undergo the unchanged 100 m snap check and cross-request joins
retain their 30 m check. No missing road segment or duration is fabricated.

Failed Direct-to-School jobs retain checkpointed measurements as incomplete, not
accepted business results. They cannot export a completed Google report. Failure
metadata identifies the point, endpoint, route context, requested/returned WGS84
coordinates and measured offset when provided by the validator. The UI shows
a readable explanation, affected address and offset. Reads/exports make no paid
calls. Retained checkpoints are diagnostic evidence, not an automatic promise
to resume or reuse traffic predictions from a different departure time.

## Budget Accounting

Every attempt reserves persistent quota before the request. Failed or timed-out
calls are not refunded, and there are no automatic HTTP retries. The only usage
ceiling is 10,000 requests per calendar month, using the request execution time
in Asia/Shanghai, not the future service date. Preflight compares the estimated
calls plus already attempted calls with this ceiling; equality is permitted.
Atomic per-request checks prevent retries, extra rounds or concurrent tasks from
exceeding it. Task, day and campaign counters remain for attribution only.
Legacy provider_call_limit settings do not restrict Google jobs or reviews;
Google-OFF provider budgets are unchanged. Do not delete the quota database.
The new month starts a new counter without removing history. Rate is at most two
requests per second through the shared quota store.

Four validation rounds do not mean four requests: dwell can require one request
per leg, multiplied by rounds, baseline routes, candidates, and outer replans.
The pilot must be sized from these actual attempts, not final route count.

Each round is preflighted at its actual request cost. Audit scenario validation
and Direct-to-School reserve headroom for the remaining initial measurements;
optional later-departure exploration also leaves one corrective round. Each
request atomically checks that headroom without charging it as an API attempt.
This does not guarantee that an entire solver search fits its budget: additional
candidates/recovery work still need allowance, and concurrent jobs may exhaust
the global cap. Saved feasible receipts remain usable without another call.
Direct-to-School's provider call limit can lower, but never raise, its Google cap.
Map reads and exports consume no prediction requests.

## Verification

`tests/test_google_arrival_policy.py` covers the three-minute boundary, two-minute
target, late correction, fixed departure, oscillation, saved feasible receipts,
cancellation and atomic quota headroom. The solver lifecycle tests exercise
both policies without paid calls.

`tests/test_google_final_validation.py` covers configuration, quotas, native
evidence, cancellation, rolling dwell, reverse validation, mode isolation,
map/export measurement contracts, and same-source time-impact comparison.
`tests/google_no_network.py` is an opt-in pytest plugin rejecting requests-based
HTTP during regression tests. Tests use synthetic coordinates and temporary
SQLite databases. It is not a general operating-system network sandbox.

`tests/google_validation_preview.tsx` exercises the actual frontend control in
an isolated fixture. It does not submit tasks or call mapping providers.
`tests/test_google_timing_entrypoints.py` adds synthetic end-to-end classification,
removal, insertion, Fleet map/export and full-review worker tests, including
persistent review budgets and preservation of source records. Disabled rollout
must reject every optional entrypoint before file preparation or paid routing.
`tests/test_google_solver_lifecycle.py` runs the real OR-Tools new-Audit pipeline
against synthetic matrices and Google responses. It covers AM/PM, scheduled
execution, all three plan variants, and an initial measured overrun triggering
the existing outer replan before a later validated solution. No live provider
request or production runtime store is used by these tests.

Offline tests and a frontend build do not establish real-route acceptance.
Before activation, verify complete task execution, restart/retry accounting,
scheduled dates, all plan variants, arrival-triggered replans, displayed/exported
values, cancellation, quota exhaustion, and representative China routes with
a limited live pilot. Confirm OFF behavior against the legacy baseline first.
