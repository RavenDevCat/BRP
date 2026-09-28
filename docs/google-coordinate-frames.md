# Google Coordinate Frames

Google's published Routes `LatLng` contract normally specifies WGS84. Do not
infer that a generic Google coordinate should always be converted, or that a
small road-snap distance proves the intended physical pickup was selected.

## Verified Regional Compatibility

The `google-shanghai-gcj02-v1` profile is limited to Google-mode addresses
explicitly resolved in Shanghai, China. Captured Geocoding and same-identity
Place ID Routes endpoints agree in the native frame. For two sampled routes,
interpreting the native geometry as GCJ02 and converting it to WGS84 brought all
sampled points within 15 meters of independently loaded local OSM roads. Native
coordinates interpreted directly as WGS84 did not have that alignment.

This is a regional compatibility policy based on captured evidence, not a claim
that all Google APIs or all Chinese cities have the same undocumented behavior.
Other markets keep their existing coordinate contract until independently
verified. Address identity, entrances, traffic accuracy, and licensing are
separate acceptance gates.

## Boundaries

- Google Geocoding preserves raw `lat`/`lng` and declares their native frame.
  `plot_lat`/`plot_lng` are normalized WGS84 and explicitly labeled as such.
- Matrix, display, and final-timing adapters pass canonical WGS84 coordinates.
  The Google client converts to the selected wire frame once at request creation.
- Native response endpoints and every polyline vertex are normalized together
  before snap, join, and geometry checks. Drive duration and distance are never
  recomputed or scaled by coordinate conversion.
- Both side-tool timing and the solver final gate configure the same task-scoped
  client profile. Rolling dwell-time legs use that profile too.
- Measurement cache identity includes the frame profile. Geocoding cache policy
  changes invalidate old assumptions rather than silently reinterpreting a
  cached coordinate. Historical results and the AMap cache are not rewritten.
- Mixed or unknown explicit profiles fail before a route request. Do not try
  multiple frames and choose the shortest trip. Legacy Google-OFF code is not
  routed through this adapter.

Place ID controls help preserve a provider identity during diagnostics. Address
strings can invoke another internal geocoding lookup and are not a substitute
for verified identity. Relay support alone does not automatically enable Place
ID waypoints in the production solver.

## References

- https://developers.google.com/maps/documentation/routes/reference/rest/v2/LatLng
- https://developers.google.com/maps/documentation/routes/specify_location
