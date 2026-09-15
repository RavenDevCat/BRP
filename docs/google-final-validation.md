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

Reference Distance uses the configured earliest departure as a fixed departure.
Route tools use arrival-window validation for AM and fixed departure for PM.
Pure distances and fuel arithmetic retain their previous basis; road-time
exports identify Google time and OSRM distance/cost separately. A Google time
does not certify an OSRM geometry. Receipt fields preserve both sources.

Audit excludes initial boarding from route dwell, matching its existing model.
Direct-to-School and Fleet retain their own stop-service conventions. Insert
preserves the saved baseline dwell and adds only the new stops' configured dwell.
Zero-rider service stops remain service stops. Native stop schedules must use
the receipt's actual departure, not a reconstructed default arrival time.

Google mode requires `validation_service_date`. Scheduled submissions freeze
their scheduled date. Submission assigns a server-generated `validation_budget_id`;
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

The relay permits at most 200 attempts per task and 500 per day/month/campaign.
`BRP_GOOGLE_ROUTES_RELAY_CAMPAIGN_LIMIT` may lower its campaign ceiling, never
raise it above 500. Private deployments must preserve both stores. Keep tokens
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

AM validation queries a departure, evaluates arrival, and can query a revised
departure up to four rounds. Only a queried departure can pass. The existing
arrival grace and outer solver replan remain applicable. PM begins at the
configured window start. Missing native legs, inconsistent totals, invalid
coordinates, excessive endpoint gaps, quota exhaustion, cancellation, and
non-convergence do not silently become successful legacy validation.

## Request Budget

Every attempt reserves persistent quota before the request. Failed or timed-out
calls are not refunded, and there are no automatic HTTP retries. Default limits
are 200 requests per task, 500 per day, 500 per month, and a fixed 500-request
pilot campaign. The pilot does not reset automatically. Do not delete the quota
database or change campaign identity to obtain more calls. Rate is at most two
requests per second through the shared quota store.

Four validation rounds do not mean four requests: dwell can require one request
per leg, multiplied by rounds, baseline routes, candidates, and outer replans.
The pilot must be sized from these actual attempts, not final route count.

## Verification

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
