# Google-mode verified pickup fallback

## Scope

The Google switch retains the legacy/OFF pipeline. Google-mode preparation,
route auditing, Direct-to-School and insertion share the same pickup resolver.
This changes location resolution, not the timing provider: a valid AMap pickup
still gets Google Routes timing and endpoint validation.

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
