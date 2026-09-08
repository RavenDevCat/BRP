# Route Display Measurements

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

## Verification

Run `tests/test_route_measurement_views.py` with the web TypeScript toolchain
installed. It exercises a shared Python/TypeScript case matrix, immutable source
views, aggregate unknowns, saved-map total duration and suppression of old
planning numbers. Run the native map, Audit and student time-impact export suites,
then compile React and verify desktop/mobile and standalone export rendering.
Unit contract parity alone does not prove browser or live road correctness.
