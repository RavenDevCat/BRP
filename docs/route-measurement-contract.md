# Saved Route Measurement Contract

## Ownership

`apps/backend/route_evidence.py` owns China final-road measurement. Pre-analysis, Audit final
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

## Pickup Precision

`apps/amap_geocode_quality.py` owns AMap address precision and matching for both
geocoding clients. City plausibility alone is insufficient: road/area-level or
unknown precision cannot certify a pickup. A single structured geocode request
may be followed by one city-code-bounded POI query. Matching preserves requested
roads, bus-stop identity, building numbers, named landmarks, branches and explicit
entrances. Unrequested parking and tenant POIs cannot substitute for a pickup.
Multiple eligible POIs require clarification; never select by first rank or
shortest resulting route.

Persist `geocode_quality_version`, precision level and POI provenance alongside
the coordinates, including through Fleet point transformations. Older AMap cache
entries are revalidated only when their addresses are prepared again; there is no
bulk cache deletion or silent correction of saved coordinates. Failure to locate
the school cannot promote the next passenger or zero-passenger waypoint to depot.

Before new final measurements, the common guard checks saved AMap point precision,
including inputs of scheduled runs. Missing/outdated evidence produces
`pickup_precision_needs_review`, consumes no route API calls for that route and
does not certify time windows or removal recommendations. Re-prepare the affected
addresses before rerunning. This version boundary also invalidates resume reuse
of measurements made before the pickup guard. It does not rewrite old results or
prove that an accepted building/POI is the operator's intended pickup entrance.

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
- `needs_review`: pickup precision is unverified, or returned metrics have an
  unresolved detour, endpoint snap or junction guard. Available metrics are not certified input
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

Direct-to-School classification retains every original route occurrence even
when that route cannot be measured. A known direct-trip excess still counts
those students; a missing direct trip must not be inferred to fit the limit
from a measured shared-route ride. Otherwise, either missing direct or current
ride evidence leaves the occurrence in `data_review`. Recovery recommendations
are not certified for a route with unclassified occurrences.

`operational_conclusion.data_review` counts affected students, unique addresses
and routes at occurrence level. Pages, map summaries and statistics workbooks
expose this uncertainty; a missing excess or post-removal comparison is blank,
not zero or "within limit". The Route Evidence worksheet retains route-level
review reasons even if a pickup guard prevented any segment request. The job
worker may finish successfully with a `partial` analysis; successful execution
must not be interpreted as complete measurement or a certified route window.

Workbook Pre-analysis measures its imported current routes through the same
final traffic gate before building the preview. Its route-budget measurement
summary reuses those snapshots, rather than issuing a separate AMap pass.
Only a complete set of verified routes can claim a ready AMap budget; the
OSRM planning-budget estimate remains separately identified. The preview uses
the supplied stop dwell and service direction, including zero-passenger
waypoints. Missing provider data is unavailable, not a legacy-result label.

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
Measurement quality is not proof that an address was geocoded to its intended
pickup point. Road, residential-area, or other coarse geocodes may still land
on the wrong access road or road level. Do not move such points automatically
just to obtain a shorter route; confirm the intended stop and re-prepare it.

## Acceptance

Offline tests cover coordinate provenance, directed cache freshness, request
limits, retries, co-located points, invalid data, junction comparison retention,
exact stop timing, read-only history maps, shared Fleet measurement, and legacy
export geometry. Run `tests/test_route_evidence.py` together with final traffic,
`tests/test_preanalysis_route_evidence.py`, workbook upload/readiness,
Direct-to-School, Fleet, Route Insert, scheduled-worker, and map/export tests.
`tests/test_amap_geocode_quality.py` covers coarse geocodes, unrelated POIs,
ambiguous matches, cache versioning, saved-input guards and school identity.
Direct-to-School tests also exercise the real manual and scheduled worker
branches with substituted provider I/O and persistence. They check unknown
classification, preserved input, partial checkpoints and workbook blanks,
without making live map requests or writing real jobs.

Live acceptance additionally requires a scoped replay of the reported routes.
Keep the same inputs, direction, stop order, vehicle and dwell configuration;
record the actual new request times. Compare requested locations, returned
roads, segment totals, per-student timing, map lines, and exported values. Traffic
from two different dates must not be presented as a controlled before/after
performance improvement. A screenshot or an offline test alone cannot prove
the cause of a particular historical detour. Keep identifiable replay data and
operational release evidence outside the public repository.
