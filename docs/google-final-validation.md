# Optional Google Final Timing Validation

## Scope

The task configuration `final_time_validation_mode` is `legacy` by default.
Legacy routing, upload preview, and independent planning tools retain their
existing providers. `google` enables an isolated final validation adapter for
China Route Audit tasks; it does not replace geocoding or the solver matrix.
It is not enabled for Korea. A frontend switch selects the mode per task.

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

## Timing and Evidence

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

Offline tests and a frontend build do not establish real-route acceptance.
Before activation, verify complete task execution, restart/retry accounting,
scheduled dates, all plan variants, arrival-triggered replans, displayed/exported
values, cancellation, quota exhaustion, and representative China routes with
a limited live pilot. Confirm OFF behavior against the legacy baseline first.
