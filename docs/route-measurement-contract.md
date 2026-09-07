# Saved Route Measurement Contract

## Ownership

`apps/backend/route_evidence.py` owns China final-road measurement. Audit final
gates, Direct-to-School analysis (including route recovery), Fleet Planner final
checks, and Route Insert Advisor use the same AMap interface through
`planner_core._amap_route_stats` or `FreshRouteProvider.route`.

OSRM still supplies the optimization matrices and reference estimates. This
contract does not change the solver's vehicle assignment or stop-order search
algorithm. Correcting coordinates and final travel measurements can change
acceptance, passenger time impact, and the finally accepted plan. A shorter
OSRM path is not evidence that AMap is wrong and must not replace AMap timing.
KR retains Kakao future-direction validation.

## Coordinates And Requests

- Preserve geocoder identity and explicit coordinate system through point
  transformations. AMap receives GCJ-02; OSRM and map display receive WGS84.
- Invalid coordinates fail the measurement; never silently drop a stop.
- Preserve the selected stop order and measure each directed adjacent pair.
  Metrics and geometry are taken from the same AMap v5 strategy-32 path.
- Exactly co-located consecutive points retain their stop boundaries with a
  zero-length leg and an explicit `co_located_stops` source. They do not consume
  an API call. Passenger count zero does not identify the school endpoint.
- The shared helper takes a run-owned cache and budget state. Fresh directed
  segments may be reused for at most 600 seconds within that run. Persistent
  whole-route caches cannot satisfy a new final measurement.

## Snapshot

Each measured route stores `route_evidence` with version, provider, status,
measurement time range, completeness, ordered legs, totals, geometry segments,
quality issues, and optional continuous-waypoint comparisons. Each leg retains
request coordinates, display coordinates, sanitized request parameters, road
names, actual duration/distance, optional OSRM references, and attempt metadata.
Provider credentials and raw exception URLs must never enter a snapshot.

`geometry_segments` is authoritative for drawing. Do not introduce an artificial
road across a gap between two segment endpoints. `geometry` is a convenience
flattening for bounds and compatibility, not proof of junction continuity.

## Quality And Time Windows

Quality states are independent of time-window acceptance:

- `verified`: complete data and no unresolved quality guard.
- `needs_review`: complete returned metrics, but a detour, endpoint snap, or
  junction guard remains unresolved. These are estimates, not certified input
  for acceptance or removal recommendations.
- `unavailable`: missing/invalid data or request budget exhausted; route totals
  are not certified and must not be filled by scaled OSRM values.

Endpoint deviations, implausibly short lengths, material distance disagreement,
and large detours trigger review. Each pair gets at most two attempts. A failed
attempt is counted. An anomaly is a review signal, not proof of illegal driving.

Pair queries can reset the approach direction at a stop. Questionable junctions
receive up to four three-point continuous-waypoint comparison requests per
route, within the same request budget. These comparisons are saved for review;
they do not automatically clear the quality guard or distribute a continuous
route's total duration into invented per-stop durations. There is no guarantee
of matching a consumer map application's recommendation at another time.

CN final-gate request budget uses `BRP_AMAP_FINAL_ROUTE_MAX_CALLS` (default 500)
instead of the smaller non-CN gate default. Existing job-wide configured caps,
Direct-to-School call limits, and shared provider QPS coordination remain in
force. Budgets include retries and contextual comparisons. The 500-call cap is
per gate, or per Fleet/Insert shared measurement context, not a promise of a
500-call upper bound for an entire Audit job with several candidates/gates.

Time-window checks use saved drive time plus the configured applicable stop
dwell. Per-student timing uses actual downstream legs for To School and upstream
legs for From School, following each workflow's pickup/dropoff dwell convention.
Never prorate a route total across the old OSRM legs when saved per-leg evidence
exists. An uncertain result keeps an unknown acceptance state, not a passed or
mathematically infeasible claim.

## Consumers And History

Interactive maps, legacy HTML maps, and statistics exports read saved evidence.
Opening a completed map must not issue a new AMap request. Legacy cached map
geometry may still be shown, but is labelled as historical and is not evidence
that its geometry and timing were captured together. This label must not depend
on current API credentials. Historical results are not silently rewritten.

Direct-to-School analysis version 6 adds a Route Evidence worksheet. It includes
direct, current-route, first-removal and final measurements. Old version 5 and
earlier results require a new run to obtain the unified snapshot. A scheduled
run executes the deployed implementation when released; previously prepared
coordinates are not automatically re-geocoded solely by deploying this change.

## Acceptance

Offline tests cover coordinate provenance, directed cache freshness, request
limits, retries, co-located points, invalid data, junction comparison retention,
exact stop timing, read-only history maps, shared Fleet measurement, and legacy
export geometry. Run `tests/test_route_evidence.py` together with final traffic,
Direct-to-School, Fleet, Route Insert, scheduled-worker, and map/export tests.

Live acceptance additionally requires a scoped replay of the reported routes.
Keep the same inputs, direction, stop order, vehicle and dwell configuration;
record the actual new request times. Compare requested locations, returned
roads, segment totals, per-student timing, map lines, and exported values. Traffic
from two different dates must not be presented as a controlled before/after
performance improvement. A screenshot or an offline test alone cannot prove
the cause of a particular historical detour. Keep identifiable replay data and
operational release evidence outside the public repository.
