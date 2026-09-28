# Google-mode verified pickup fallback

## Scope

The Google switch retains the legacy/OFF pipeline. Google-mode preparation,
route auditing, Direct-to-School and insertion share the same pickup resolver.
Google timing remains preferred. Verified pickup fallback and explicitly
authorized local timing fallback are separate policies; neither accepts an
unresolved identity or fabricated geometry.

Resolution priority is an authoritative operator correction, then a matching
Google geocode, then an identity-checked AMap cache entry or targeted lookup.
An operator correction must come from the correction registry. Input row
metadata or an arbitrary cache flag cannot self-certify a pickup.

## Evidence And Failure Handling

- Keep original row ownership, stop order, school flags and passenger counts.
- Keep provider-native coordinates and canonical WGS84 display coordinates
  separate. Apply the existing Shanghai Google coordinate profile exactly once.
- Store fallback candidates in `google_service_point_cache.json`, separate
  from both provider caches, with policy and source metadata. Reads do not extend
  its seven-day TTL. Recheck identity before reuse.
- Check `location_resolution` on each requested waypoint for actual location
  provider, timing provider, resolution policy and operator revision when used.
  A location match alone is not route endpoint acceptance.
- Only local Google identity failures can trigger fallback. Auth, quota,
  transport and provider-wide failures remain errors.
- Treat AMap data-response error 30001 as an unsuccessful address lookup,
  allowing the alternate endpoint. It never produces a successful coordinate.
  Cancellation and auth/quota errors are not swallowed.
- Memoize failed fallback lookups within one solve to avoid repeated paid calls.
- Strip only explicitly parenthesized proximity hints from matching/query text.
  Preserve gates and road-side requirements. Opposite-road pickups still need
  an authoritative confirmation.
- Limit POI search to the requested bus/platform or entrance category. An empty
  POI ID must not promote an address fallback into verified gate evidence.
- Resolve numbered compound addresses through explicit provider child-to-parent
  links when the child preserves the requested road and number, is within 750m
  of its residential parent, and the parent supplies an entrance. Preserve both
  native addresses and the relationship receipt. This does not override a
  named gate, opposite-road requirement or ambiguous parent.
- Store operator-selected address intent independently from coordinates in
  `google_pickup_intents.json`. An intent still requires provider evidence.
  R18's north/south choice is south; do not ask for that choice again.

## Local Timing Fallback

The user explicitly approved clearly labeled local timing fallback on September
28. The isolated `google_timing_fallback.py` adapter is enabled only by
`BRP_GOOGLE_LOCAL_TIME_FALLBACK_ENABLED=1`; the Google/OFF legacy path is unchanged.
It is not enabled in the serving environments by this development checkpoint.

- Only Google pickup-snap and cross-request join failures trigger it. Identity,
  credentials, transport and quota failures do not.
- Reuse the existing native AMap driving engine on the affected ordered edge,
  with verified identity, valid finite metrics and endpoint offsets at most100m.
  If the handoff differs by more than30m, remeasure the adjacent incident edge
  too. Reject unresolved native joins; do not draw or time a synthetic connector.
- Recompute downstream Google departure times and arrival-anchored inversion
  using the mixed durations and dwell. Cache current-traffic receipts for at
  most10minutes without sliding expiry. They remain current traffic, never a
  future prediction, even when reused for tomorrow's departure inversion.
- Record `timing_provider`, `time_basis`, `provider_called_at`, original failure
  and native evidence per leg. Aggregate `forecast_complete`,
  `fallback_leg_indexes`, `timing_note` and `fallback_policy_version`.
- Public results, maps, ordinary Excel exports and standalone direct-map exports
  disclose AMap current traffic and explicitly say it is not a complete Google
  future forecast. A rider boarding after all fallback edges retains Google-only
  provenance for their own remaining ride.
- `amap_timing_api_calls` is separate from Google and geocoding counters. The
  Google shared monthly10000 limit and submission accounting remain in force.

Live checks passed for Fuye199 and XiangyuXinyuan1177 direct trips, plus the
verified contiguous sequence5-7 segments of R10 and R22 through the shared final
timing adapter with dwell and inversion. These are not whole-route acceptance.
R10's tested segment ends at a pickup, not at the school. Location coverage is
84/124;40 remain unresolved. R1 and R9 are the only fully located routes with
genuine final-timing acceptance so far. No overall completion or release claim.

## Accounting And Reconciliation

Google geocoding and AMap pickup calls are reported separately; their sum is
`pickup_resolution_api_calls`. Google usage stays in the shared monthly ledger.

Keep the 100m pickup snap limit, 30m geometry/join checks, strict duration
reconciliation and arrival-inversion rules. Route distance versus summed leg
distance uses `max(2m, min(20m, 2m * leg_count, 0.1% * route_distance))`.
Both original values, the difference and tolerance are retained in leg evidence;
no distance or duration is rescaled. Significant mismatches remain failures.

Policy version: `google-final-v5-verified-pickups`.

## Release Gate

Passing offline tests is not full live acceptance. Verify pickup identities,
each direct trip, ordered current routes, dwell-aware timing, arrival inversion,
resume accounting, exports and visible provenance before promoting the entire
Google feature. Do not label geometry-only diagnostics on unresolved coordinates
as accepted routes. Do not mutate historical results to hide rejected cases.

Provider references: [AMap POI search](https://lbs.amap.com/api/webservice/guide/api-advanced/search),
[AMap category codes](https://lbs.amap.com/api/webservice/download).
