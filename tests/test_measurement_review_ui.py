"""Exercise native TypeScript contracts and React markup without live provider calls."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const web = path.join(process.cwd(), 'apps/web');
const nativeRequire = createRequire(path.join(web, 'package.json'));
const ts = nativeRequire('typescript');
const cache = new Map();
function load(name) {
  if (name === '@/lib/i18n/context') return {useT: () => value => value};
  let file = name.startsWith('@/') ? path.join(web, 'src', name.slice(2)) : name;
  if (!path.isAbsolute(file)) return nativeRequire(name);
  if (!fs.existsSync(file)) file += fs.existsSync(file + '.tsx') ? '.tsx' : '.ts';
  if (cache.has(file)) return cache.get(file).exports;
  const mod = {exports: {}}; cache.set(file, mod);
  const source = fs.readFileSync(file, 'utf8').replace('import.meta.env.VITE_API_BASE_URL', 'undefined');
  const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true}}).outputText;
  new Function('require', 'module', 'exports', js)(key => load(key.startsWith('.') ? path.resolve(path.dirname(file), key) : key), mod, mod.exports);
  return mod.exports;
}
'''


def run(script):
    result = subprocess.run(["node", "-e", HARNESS + script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_native_review_states_and_budgets():
    run(r'''
const {reviewIsActive, reviewActions, reviewHasNativeResult, reviewBudget} = load('@/lib/measurement-review-state');
for (const status of ['queued', 'running', 'pausing', 'yielding']) assert.equal(reviewIsActive({status}), true);
for (const status of ['paused', 'succeeded', 'needs_review', 'failed', 'canceled', 'unknown']) assert.equal(reviewIsActive({status}), false);
assert.equal(reviewIsActive(), false);
assert.deepEqual(reviewActions('paused'), ['resume', 'cancel']);
assert.deepEqual(reviewActions('running'), ['pause', 'cancel']);
assert.deepEqual(reviewActions('yielding'), ['cancel']);
assert.deepEqual(reviewActions('succeeded'), []);
for (const value of ['', '0', '-1', '501', '1.1', '1e2', 'Infinity', ' 10', 'true']) assert.equal(reviewBudget(value), null);
for (const value of ['1', '100', '500']) assert.equal(reviewBudget(value), Number(value));
for (const [mode, scope, body] of [
  ['full_audit', 'full_audit_result', {audit_result: {structured_results: {}, current_plan_assessment: {}}}],
  ['full_direct_school', 'full_direct_school_result', {analysis_result: {stops: []}}],
]) {
  const row = {status: 'succeeded', request: {mode}, result: {scope, status: 'complete', ...body}};
  assert.equal(reviewHasNativeResult(row, mode), true);
  assert.equal(reviewHasNativeResult({...row, status: 'needs_review', result: {...row.result, status: 'partial'}}, mode), true);
  for (const status of ['running', 'queued', 'canceled', 'failed']) assert.equal(reviewHasNativeResult({...row, status}, mode), false);
  assert.equal(reviewHasNativeResult({...row, result: {...row.result, status: 'running'}}, mode), false);
  assert.equal(reviewHasNativeResult({...row, result: {...row.result, scope: 'selected_routes_only'}}, mode), false);
  assert.equal(reviewHasNativeResult({...row, request: {mode: 'selected_routes'}}, mode), false);
  assert.equal(reviewHasNativeResult({...row, result: null}, mode), false);
}
assert.equal(reviewHasNativeResult({status: 'succeeded', request: {mode: 'full_audit'}, result: {status: 'complete', scope: 'full_audit_result', audit_result: {}}}, 'full_audit'), false);
''')


def test_api_requests_and_exports_are_scoped_to_source_and_review():
    run(r'''
const api = load('@/lib/api');
const requests = [];
global.fetch = async (url, options) => { requests.push({url, options}); return {ok: true, headers: {get: () => 'application/json'}, json: async () => ({})}; };
(async () => {
  await api.getMeasurementRisk('source/id');
  await api.listMeasurementReviews('source/id');
  await api.getMeasurementReview('source/id', 'review/id');
  await api.getMeasurementReviewMapData('source/id', 'review/id', 'current_plan');
  assert.equal(requests.length, 4);
  assert(requests.every(row => !row.options.method || row.options.method === 'GET'));
  assert.equal(requests[3].url, '/api/jobs/source%2Fid/measurement-reviews/review%2Fid/map-data/current_plan');
  assert.equal(api.getMeasurementReviewExportUrl('source/id', 'review/id'), '/api/jobs/source%2Fid/measurement-reviews/review%2Fid/export');
  const body = {mode: 'full_audit', provider_call_limit: 12, request_key: 'retry-key', confirm_provider_calls: true};
  await api.createMeasurementReview('source/id', body);
  assert.equal(requests[4].options.method, 'POST');
  assert.deepEqual(JSON.parse(requests[4].options.body), body);
  await api.controlMeasurementReview('source/id', 'review/id', 'pause');
  assert.equal(requests[5].url, '/api/jobs/source%2Fid/measurement-reviews/review%2Fid/actions/pause');
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_real_react_markup_preserves_original_and_limits_admin_controls():
    run(r'''
const React = nativeRequire('react');
const {renderToStaticMarkup} = nativeRequire('react-dom/server');
const {QueryClient, QueryClientProvider} = nativeRequire('@tanstack/react-query');
const {MeasurementReviewWorkspace} = load('@/features/results/measurement-review-panel');
global.fetch = () => { throw Error('Rendering must not issue requests'); };
function render(admin) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: Infinity, gcTime: Infinity}}});
  client.setQueryData(['me'], {is_admin: admin});
  client.setQueryData(['measurement-risk', 'source'], {source_supported: true, routes: [{route_key: 'current_plan:R1', route_id: 'R1', risk_reasons: ['missing_unified_measurement'], input_issues: []}], full_review: {mode: 'full_audit', available: true, scope_summary: {route_count: 1}}});
  client.setQueryData(['measurement-reviews', 'source'], {reviews: [{review_id: 'review', source_job_id: 'source', created_at: '2026-01-01T00:00:00Z', status: 'running', api_calls: 1, request: {mode: 'full_audit', provider_call_limit: 5}}]});
  const html = renderToStaticMarkup(React.createElement(QueryClientProvider, {client},
    React.createElement(MeasurementReviewWorkspace, {job: {job_id: 'source', status: 'succeeded', result: {}}, mode: 'full_audit'},
      correction => React.createElement('div', null, correction ? 'CORRECTED' : 'ORIGINAL_CONTENT'))));
  client.clear();
  return html;
}
const viewer = render(false), admin = render(true);
assert(viewer.includes('ORIGINAL_CONTENT') && admin.includes('ORIGINAL_CONTENT'));
assert(viewer.includes('Missing unified measurement'));
assert(!viewer.includes('New correction') && !viewer.includes('aria-label="Pause"'));
assert(admin.includes('New correction') && admin.includes('aria-label="Pause"'));
assert(!admin.includes('Start correction')); // Explicit form opening and confirmation are required.
''')
