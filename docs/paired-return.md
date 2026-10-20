# Paired Scheduled Return Measurements

Status: staging feature; production release is not authorized in this task.
Google work is paused and the staging rollout flag must remain false.

## Operator Workflow

1. Open Direct-to-School and validate the original To-School workbook.
2. Choose Scheduled, set the morning measurement time and afternoon return
   measurement time (default 15:40), then choose service dates.
3. Create scheduled snapshots. Each date creates two independently executable
   jobs, linked by an immutable pair identity and original-plan fingerprint.
4. Open the history entry and switch Morning to school / Afternoon return.
5. In Afternoon return, select All routes or Under 1 hour; select a route to
   inspect school-to-stop ride times. Export statistics or the visible map.

The afternoon plan is the original uploaded morning plan in reverse stop order,
not the morning analysis's proposed removal plan. Stops with zero riders remain
stops. The explicit school becomes the start. Pickup coordinates are unchanged;
the system does not invent an opposite-side platform for the return journey.

## Timing And Failure Policy

- Fresh AMap routing at execution time; no Google timing/geocoding requests.
- Driving plus all non-school dwell is the whole-route total. Strictly less
  than 3,600 raw seconds is highlighted; exactly 3,600 is not.
- A student's ride ends upon arrival at their stop, before their own dwell.
- Each route records its own sampling timestamp. This is a current traffic
  snapshot, not a future traffic forecast or an observed bus journey.
- Unavailable routes retain stop and rider counts and remain non-passing.
  Partial coverage is explicit in the result and exports.
- A 15-minute trigger grace period applies to new pairs, including retry.
  Beyond it the job fails with a clear reason; historical traffic is not replayed.
- Morning failure/cancellation does not cancel the afternoon job. Individual
  retry remeasures the afternoon snapshot, subject to the same time window.
- Submission identity plus owner, queue scope and date provides idempotency.
  Both jobs insert in one SQLite transaction. Conflicting identities are rejected.

## Acceptance On 2026-10-20

- 138 automated tests passed across pairing, scheduler, SQLite, history, API,
  existing direct-school analysis and Google entrypoint isolation.
- Tests cover atomic rollback, concurrent duplicate submissions, user isolation,
  cancellation, zero-rider stops, vehicle/assignment preservation, expiry,
  missing measurements, exact 3,600-second boundary and Excel green fill.
- Frontend TypeScript check and production build passed. Existing large-chunk
  build warnings remain; no new runtime browser errors were observed.
- An isolated queue and database executed one real 20-route DEMH return job,
  preserving 294 riders and 144 stop rows including school starts. All 20 routes
  completed, using 20 AMap route requests and zero Google requests.
- Seven whole routes were below one hour: R10, R11, R14, R15, R16, R19, R22.
  Example R10: total 59:30, driving 51:30, dwell 8:00, final student's ride 58:30.
- The run sampled approximately 09:45 Asia/Shanghai. The test deliberately
  advanced only the isolated PM job to current time; it is not evidence of
  afternoon peak traffic. The original future AM test job was not executed.
- Browser checks: desktop 1440x1000, mobile 390x844, pair tabs, short-route
  filter, map zoom, schedule date/time controls, PNG and XLSX downloads.
  No page-level mobile horizontal overflow. XLSX totals and seven green rows
  match the API result exactly.

## Release Boundaries

- No production deployment, historical-result rewrite, shared-cache cleanup,
  or automatic backfill of the 33 existing production schedules.
- Before production release, decide how to supplement existing future schedules
  and perform the normal controlled deployment. Do not reinterpret past AM jobs
  as paired jobs or fabricate PM observations.
- Real afternoon peak samples and operational bus travel-time calibration remain
  future evidence gathering, not claims made by this technical acceptance.
- The separate Google development branch remains preserved, not merged here.
