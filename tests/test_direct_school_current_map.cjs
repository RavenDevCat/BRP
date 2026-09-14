const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const web = createRequire(path.resolve(__dirname, '../apps/web/package.json'));
const { transformSync } = web('esbuild');
const source = fs.readFileSync(path.resolve(__dirname, '../apps/web/src/features/distance/direct-school-current-map.ts'), 'utf8');
const compiled = transformSync(source, { loader: 'ts', format: 'cjs', target: 'node18' }).code;
const adapter = { exports: {} };
new Function('module', 'exports', compiled)(adapter, adapter.exports);
const { buildDirectSchoolCurrentMap: build, currentMapStopId } = adapter.exports;
function fixture() {
  return { service_direction: 'To School', school: { lng: 121.4, lat: 31.2, address: 'School' },
    parameters: { stop_service_minutes: 1 },
    stops: [
      { stop_key: 'shared', address: 'Renamed', lng: 121.42, lat: 31.22, riders: 9,
        route_contexts: [{ route_id: 'R1', stop_sequence: 1, riders: 2, estimated_current_ride_min: 12 },
          { route_id: 'R2', stop_sequence: 1, riders: 7, estimated_current_ride_min: 40 }] },
      { stop_key: 'empty', address: 'Via', lng: 121.41, lat: 31.21, riders: 0,
        route_contexts: [{ route_id: 'R1', stop_sequence: 2, riders: 0, estimated_current_ride_min: 7 }] }],
    routes: [{ route_id: 'R1', riders: 2, stop_count: 2, total_duration_min: 12,
      provider_duration_min: 10, provider_distance_km: 3,
      geometry: [[121.42,31.22],[121.41,31.21],[121.4,31.2]],
      route_evidence: { status: 'verified', complete: true, leg_durations_s: [240,360], leg_distances_m: [1000,2000],
        geometry_segments: [[[121.42,31.22],[121.415,31.215]],[[121.41,31.21],[121.4,31.2]]] } },
      { route_id: 'R2', riders: 7, stop_count: 1, total_duration_min: 40, geometry: [] }] };
}
test('uses saved route metrics, context riders and original sequence', () => {
  const input = fixture();
  const before = JSON.stringify(input);
  const data = build(input, 'R1');
  assert.equal(data.routes[0].duration_s, 720);
  assert.equal(data.routes[0].distance_m, 3000);
  assert.equal(data.stops[0].passenger_count, 2);
  assert.equal(data.stops[0].order, 1);
  assert.equal(data.stops[0].id, currentMapStopId('R1',1,'shared'));
  assert.equal(JSON.stringify(input), before);
});
test('zero-rider via stop is not school', () => {
  const data = build(fixture(), 'R1');
  assert.equal(data.stops[1].is_depot, false);
  assert.equal(data.stops[1].passenger_count, 0);
  assert.equal(data.stops.filter(s => s.is_depot).length, 1);
});
test('preserves split evidence without bridging', () => {
  const input = fixture();
  const data = build(input, 'R1');
  assert.deepEqual(data.routes[0].geometry_segments, input.routes[0].route_evidence.geometry_segments);
  assert.equal(data.routes[0].geometry_segments.length, 2);
});
test('AM cumulative native travel excludes dwelling, current ride stays separate', () => {
  const data = build(fixture(), 'R1');
  assert.deepEqual(data.stops.map(s => s.cumulative_duration_s), [0,240,600]);
  assert.deepEqual(data.stops.map(s => s.cumulative_distance_m), [0,1000,3000]);
});
test('PM school starts before all service stops', () => {
  const input = fixture(); input.service_direction = 'From School';
  const data = build(input, 'R1');
  assert.deepEqual(data.stops.map(s => s.cumulative_duration_s), [240,600,0]);
  assert.equal(data.stops.find(s => s.is_depot).order, 0);
});
test('missing raw legs stay unknown; no inferred cumulative numbers', () => {
  const input = fixture(); delete input.routes[0].route_evidence;
  const data = build(input, 'R1');
  assert.ok(data.stops.every(s => s.cumulative_duration_s === null));
});
test('missing routes or geometry do not fabricate a route', () => {
  assert.equal(build(fixture(), 'missing').routes.length, 0);
  assert.deepEqual(build(fixture(), 'R2').routes[0].geometry, []);
  const input = fixture(); input.routes[0].route_evidence.geometry_segments = [];
  assert.deepEqual(build(input, 'R1').routes[0].geometry, []);
});
test('route/sequence matching is independent of address labels', () => {
  const input = fixture(); input.stops.reverse(); input.stops.forEach(s => s.address = 'Same label');
  const data = build(input, 'R1');
  assert.deepEqual(data.stops.filter(s => !s.is_depot).map(s => s.order), [1,2]);
  assert.equal(build(input, 'R2').stops[0].passenger_count, 7);
});
