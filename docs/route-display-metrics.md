# Route Display Measurements

## Coordinates And Pickup Precision

Coordinate resolution and pickup-entrance confirmation are separate. An older
cache schema or a coarse provider precision label must not remove an otherwise
valid, city-matched stop. Reused coordinates stay unchanged; uncertain entrances
and road sides use the existing address-review warning flow. Invalid coordinates,
wrong-city matches and city-centre fallbacks remain blocked.

AMap measurements describe travel between the supplied coordinates, not proof
that a pickup entrance has been physically confirmed. Saved road evidence keeps
`pickup_precision_reviews` separately from road-geometry and detour issues.
The latter still prevent questionable measurements from passing the final gate.
Old precision-only negative cache entries are retried under the revised policy;
normal successful cache reuse does not trigger new geocoding.

Named bus-stop lookups with a known city now prefer a matching station POI over
a generic road/intersection geocode. Road-name order matters: `Road A Road B`
must not silently select `Road B Road A`, which may be a different stop. Multiple
same-name platforms remain ambiguous. Failed or ambiguous POI searches retain
a usable structured geocode with its review warning, rather than removing the
address. Ordinary building addresses still use structured geocoding first.
Existing valid cache coordinates remain unchanged until explicitly corrected.

## Continuous Turn Evidence

The v3 continuous-turn comparison distinguishes an unexplained stop turnaround from
the same turnaround confirmed by a continuous AMap waypoint request. Existing
bounded context checks are reused; the rule does not add a second measurement
pass or choose a shorter alternative.

Only the turnaround issue at that specific stop may be resolved. The adjacent
road endpoints must join within one metre. Reported and geometry lengths must
agree within five metres, and duration difference must be at most thirty seconds
or ten percent of the smaller total, whichever is greater. Travel-ordered shapes
are compared at all vertices and at five-metre intervals, with at most one metre
separation. Different road order, a gap, an alternate detour, invalid context,
exhausted comparison budget or a significant time difference remains unresolved.
The geometry comparison is bounded and fails closed for oversized inputs.

Confirmed turn observations and their context resolution are retained in saved
evidence. Independent detour, snap or distance warnings cannot be cleared by a
turn confirmation. Per-leg times, distances, geometry, stop order and dwell are
never replaced or proportionally redistributed from the context total. Existing
final gates and all consumers use the resulting complete evidence normally.
Older cached evidence is not upgraded silently, and reading a historical result
does not rerun this rule or make new provider requests.

## Native Continuous Measurements

`amap-route-evidence-v5` makes a continuous itinerary the primary measurement for
every multi-stop route, including routes without an obvious turnaround. Complete
native evidence avoids all independent-edge and junction-comparison requests.
Two-point trips still use one directed pair measurement. The native boundary and
geometry checks below remain mandatory; waypoint and student counts are unchanged.

Long routes use up to eighteen points per request, overlapping the previous two
complete legs. Before accepting a chunk, compare those two directed road traces
with the retained incoming legs using the continuous-turn evidence tolerances.
Only a proven matching approach may join the chunks. Discard the repeated legs
from the accumulated time, distance and stop sequence, not from the proof record.
Each chunk retains its request, timestamps, native steps and overlap resolution.
The same per-job request budget and fresh directed cache apply to every chunk.

Missing native segmentation or an unproven chunk join may retain independent
edges as diagnostic preview evidence, but never certify the route's time window.
The saved `continuous_measurement` explains the primary attempt and failures;
`continuous_itinerary_unverified` prevents a fallback from appearing verified.
Reading historical records does not silently run or upgrade this new policy.

### Previous Recovery Policy

`amap-route-evidence-v4` adds a bounded recovery for complete adjacent measurements
whose stop approach or turnaround still conflicts with continuous driving. For
routes with one to sixteen interior waypoints, use
one whole ordered AMap v5 itinerary, reusing an existing identical context result.
The request uses the same job call budget and ten-minute directed cache. Longer
routes are not truncated, reordered or certified by this recovery.

Only native `navi.assistant_action` waypoint-arrival boundaries can divide the
provider's steps into legs. Every step must include finite nonnegative distance,
duration and geometry. All waypoint boundaries and the final arrival must exist;
step totals must reconcile with path totals within one metre and one second.
Single-coordinate one-metre arrival instructions retain their native cost.
Missing geometry, road-step gaps over fifty metres, ambiguous zero-length legs
or missing boundaries prevent replacement. No time is allocated proportionally.

When complete, `amap_continuous_waypoint_legs` supplies the geometry, route totals
and each student's remaining route ride from the same continuous response. It
may be longer or shorter than independently requested edges; the policy does
not choose the shortest response. Each leg retains its native steps and boundary
indexes. `adjacent_comparison` preserves the original measurements and warnings;
`continuous_recovery` records the replacement decision and provider evidence.
Continuous context legs never overwrite the independent two-point cache.

Road snapping, direct-distance detours and OSRM-distance disagreement checks run
again on the replacement legs. A valid continuous response proves provider
continuity, not the correctness of a supplied pickup or unrestricted real-world
access. Remaining issues still prevent time-window certification. No historical
result or original workbook is rewritten by this policy.

## Read Contract

`route_display_metrics` in the shared `route_measurement_view.py` selects values
for presentation. It does not change solver references, stop ordering, points,
gates or saved historical results. The frontend `routeDisplayMetrics` consumes
the same `display_metrics` contract and supports existing raw result shapes.

The contract has `duration_s`, `distance_m` and `source`:

- `measurement`: verified complete saved road evidence. Total duration adds
  the saved route dwell exactly once. Unknown dwell leaves total time unavailable.
- `historical_measurement`: an older final gate with saved measured totals.
  Missing gate totals stay unavailable rather than falling back to planning data.
- `planning_reference`: no recorded measurement contract. Original route
  estimates remain visibly labelled references, not certified live measurements.
- `unavailable`: incomplete/uncertain evidence, retained quality issues, a
  failed measurement, or disagreement between gate totals and saved evidence.
  Primary values are null. A time-window failure with complete coherent evidence
  is still a known measurement, not a missing measurement.

Zero is a valid value. Missing values, booleans, negatives, NaN and infinity
must not become zero. An incomplete set cannot produce a complete aggregate
by summing or averaging only successful routes.

## Consumers

Map payloads retain explicit `raw_duration_s` and `raw_distance_m` references,
but primary route totals use the display contract. Schedule calculations occur
before presentation normalization so the existing AM/PM timing rules remain
unchanged. Unavailable measurements have no displayed stop cumulative metrics or
schedule; their map summary is incomplete instead of a healthy zero.

Result reads attach `display_metrics` and `display_summary` without replacing
original scenario/assessment metrics in stored data. Measured averages and
overlong-route display counts use the original saved duration-limit convention.
Tables and map summaries must use these display fields. Planning financial
metrics and the original optimization inputs remain separate references.

The standalone interactive map export must preserve nulls and show unavailable
values explicitly, including route hover, route list and stop details. Unknown
durations do not qualify for the Long filter.

Duration tiebreakers in the result recommendation use measured totals only.
An empty route set, a planning reference or any missing measured route time
makes the total unavailable. Unavailable totals sort after known totals when
the existing higher-priority comparisons tie; they do not become the fastest
option. Vehicle-count and rider-impact priorities, scenario eligibility and
the solver itself are unchanged.

Both legacy HTML summary builders also use the shared display contract.
Unknown totals print `Not available`; partial/contradictory evidence is labelled
for review. Saved planning references remain separate. Exporting a saved result
does not fetch road measurements or silently replace its evidence.

## Fleet Readouts

Fleet uses `fleet_route_display_view` after its unchanged assignment/order
solver and on historical reads. Newly captured CN results save the existing
Fleet dwell convention explicitly. Route table and map duration is driving
time plus saved dwell once; planning references remain separately available.
Missing or contradictory evidence cannot fall back to an old OSRM duration.

Stop schedules require complete saved legs whose totals agree with the route
measurement and known dwell. A route total alone cannot reconstruct a stop
time. Zero-passenger waypoints remain in the ordered service sequence.
AMap geometry uses its saved segments; a partial attempted measurement cannot
substitute OSRM geometry or draw a synthetic line across missing segments.

Historical read conversion rebuilds rows, map payload and downloadable workbook
from saved data only. It does not write the history record or call a provider.
It omits stale cached HTML/workbook responses, and conversion failure does not
fall back to their old values. This is display consistency, not a new traffic
sample or proof that an old pickup coordinate was correct.

The generated workbook retains its Audit input sheets and adds a readable
`Route Measurements` sheet with route total, driving time, dwell, distance,
source, timestamp and review note. Missing totals stay blank and highlighted;
the original saved workbook remains unchanged.

## Verification

### Residential And Named-Gate Pickups

CN residential pickups are outside at an entrance, not at a compound-centre
POI. Both geocoders use `amap-pickup-review-v4`. Explicit directional, numbered
and lettered gates must match the gate POI identity, not a parent POI's address.
For example, gate 1 is not gate 11, and a south gate is not a southeast gate.
Building numbers, phases, branches and the original input address remain intact.

Fresh named-gate and recognizable residential lookups try city-limited POIs.
An ordinary street-number geocode remains first, but a returned residential-area
level triggers an entrance check. POI requests use `extensions=all`. A unique
matching entrance POI uses its own coordinate; a residential POI without an
explicit requested gate may use its valid `entr_location`, never its centre as
proof of an entrance. Multiple matching entrances require review; they are not
selected by proximity, short route length or provider ranking as a confirmed
pickup. A generic entrance cannot stand in for an explicitly named gate.

`pickup_entrance_source`, `amap_poi_location` and `amap_poi_entr_location` record
the choice separately. Routing receives GCJ02; map coordinates are converted
to WGS84 once after choosing the pickup coordinate. See the provider's
[POI entrance field documentation](https://lbs.amap.com/api/webservice/guide/api/search/).

Coordinate availability and entrance confirmation remain distinct. Ambiguous,
missing or failed POI checks retain a usable geocode for display with review
metadata. Existing valid caches are annotated, not silently relocated or mass
invalidated, and no background bulk refresh is triggered by a policy version.
Resolving a known old pickup requires an explicitly confirmed correction or a
controlled re-preparation; a metadata warning does not prove its route fixed.
Confirmed corrections take precedence over automated entrance matching.

### Operator-Confirmed Pickups

`GET /api/pickup-corrections` reads the current revision for `country`, `city`
and the exact original `address`. `POST /api/pickup-corrections` requires service
authentication and a global administrator, `confirm: true`, `expected_revision`,
an explicit confirmation `reason`, and the confirmed AMap `poi_id`, `poi_name`,
GCJ02 `lat` and `lng`. A different POI name may be an intentional operator
correction to an old landmark; it is not silently accepted by geocode matching.
Unknown fields and unsupported cities are rejected. `active: false` appends a
deactivation at the expected revision; it does not erase the earlier audit.

The independent SQLite registry defaults to `pickup_overrides.sqlite` beside
`BRP_RUNTIME_DB_PATH`, or uses explicit `BRP_PICKUP_OVERRIDES_DB_PATH`. Reads do
not create files. Consumers read the current revision before cached geocoding
on each new preparation; stale in-memory geocode caches cannot overwrite it.
Country/city normalization uses each existing geocoder's supported-city table.
Addresses match exactly after whitespace normalization, not fuzzy matching.
The API performs no geocode or route request and computes WGS84 plotting
coordinates from the explicitly declared GCJ02 point exactly once.

Existing workbooks, prepared job snapshots (including scheduled jobs) and
historical results are immutable. Re-prepare a new run from its original input
to apply a correction; do not present old routes as recalculated. Route order,
passengers, school identity and zero-passenger waypoints are unchanged.

This maintenance API currently has administrator-only English validation
messages; it has no new end-user form. User-facing localization is deferred
until a confirmation UI is added, not mixed into ordinary geocode failure UI.

### Test Coverage

Fresh AMap pickup resolution (v6) separates `pickup_resolution_status` from
coordinate availability. `reference_only` remains visible/cacheable but is
rejected by the shared route precision guard before provider I/O, unless an
audited operator confirmation applies. This flag is retained in serialized
point provenance; cache reuse does not promote it to a measured service stop.
Old cache entries without the flag are not automatically reclassified or moved.
Prepared snapshots retain their original evidence; deployment does not rerun
them. Re-prepare original inputs explicitly to apply fresh resolution.

Numbered-building POIs require entrance evidence; ordinary precise door-number
geocodes keep the existing fast path. Explicit gates request child POIs and
match the exact gate, retaining the parent ID but never inheriting its location.
Numbered transit exits such as `3号口` are gate identities, not door numbers.
Only a compound/building's own POI can supply its residential entrance; tenant
entrances cannot establish a compound pickup. When several sites match, exact
door-number evidence in the site's address is stronger than name-only evidence
paired with a nearby junction address. Equal precise site candidates stay ambiguous.
Failure to establish a unique entrance is not fixed by choosing the shortest
route, deleting a waypoint, or replacing the provider's returned geometry.

Route Insert uses the same CN evidence for geometry, exact driving legs,
distance and timing. A walking-only selection remeasures its existing driving
sequence once, using the same run cache as inserted stops. A missing or
unverified AMap result cannot fall back to OSRM as a measured time; failed OSRM
requests supply neither a synthetic straight-line route nor a comparison
baseline. Successful non-CN OSRM behavior remains a planning estimate.

The selected route duration is driving plus saved base dwell plus inserted-stop
dwell. An explicit zero is preserved. Map summaries total their displayed routes;
unknown values propagate through totals and deltas. Individual map stop
`cumulative_duration_s` remains driving-only, not a promised arrival clock.
Source `display_metrics` and limit markers are discarded from the derived map
so they cannot override its new measurement. The original source stays intact.

Each affected-route `measurement_inputs` snapshot records ordered base/selected
points, explicit school/waypoint roles, saved-versus-derived base dwell,
inserted dwell, direction and window configuration. It is input evidence for
future review, not an implemented Fleet/Insert historical-correction adapter.
Existing histories are not auto-refreshed. Run a new proposal for new evidence.

Run `tests/test_route_insert_measurements.py` and `tests/test_route_insert_ui.py`
alongside the existing Insert suite. They cover zero dwell, walking-only refresh,
unknown values, source immutability, stale display metadata and actual result
markup. Rendered markup tests do not replace desktop/mobile map acceptance.

Run `tests/test_route_measurement_views.py` with the web TypeScript toolchain
installed. It exercises a shared Python/TypeScript case matrix, immutable source
views, aggregate unknowns, saved-map total duration and suppression of old
planning numbers. Run the native map, Audit and student time-impact export suites,
then compile React and verify desktop/mobile and standalone export rendering.
Unit contract parity alone does not prove browser or live road correctness.
Also run `tests/test_fleet_measurement_views.py` and the Fleet behavior suites:
they cover read immutability, partial/zero values, saved schedules, real Folium
HTML generation and workbook contents without external provider requests.
