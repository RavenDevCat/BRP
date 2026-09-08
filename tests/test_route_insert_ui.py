"""Exercise Insert's actual formatters and result markup without a browser."""
from test_measurement_review_ui import run


def test_insert_unknown_measurements_and_time_window_labels():
    run(r'''
const React = nativeRequire('react');
const {renderToStaticMarkup} = nativeRequire('react-dom/server');
const file = path.join(web, 'src/features/insert/route-insert-advisor-page.tsx');
const source = fs.readFileSync(file, 'utf8');
const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['measurementNumber', 'minutes', 'meters', 'signed', 'text', 'beforeAfter',
  'recommendationList', 'proposalNewStopAddress', 'ProposalResults', 'Metric'];
const declarations = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
assert.equal(declarations.length, names.length);
const code = declarations.map(node => node.getText(ast)).join('\n') + '\nexport {' + names.join(',') + '};';
const compiled = ts.transpileModule(code, {compilerOptions: {module: ts.ModuleKind.CommonJS,
  target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText;
const mod = {exports: {}};
new Function('require', 'module', 'exports', 'useT', 'Badge', compiled)(nativeRequire, mod, mod.exports,
  () => value => value, load('@/components/ui/badge').Badge);
const {minutes, meters, signed, beforeAfter, ProposalResults} = mod.exports;
for (const value of [null, undefined, true, false, '', ' ', NaN, Infinity, [], {}]) {
  assert.equal(minutes(value), '-'); assert.equal(meters(value), '-');
  assert.equal(signed(value, minutes), '-');
}
assert.equal(minutes(0), '0 min'); assert.equal(meters(0), '0 m');
assert.equal(signed(60, minutes), '+1 min'); assert.equal(signed(-60, minutes), '-1 min');
assert.equal(beforeAfter(null, 600, minutes), '- -> 10 min');
function render(time_window_ok, provider_verified = false) {
  const result = {proposals: [], summary: {}, scenarios: [{id: 'selected', selected_plan: {
    feasible: time_window_ok === true && provider_verified, total_added_duration_s: null,
    total_added_distance_m: null, affected_routes: [{route_id: 'R1', time_window_ok,
      provider_required: true, provider_verified, base_duration_s: null, selected_duration_s: null,
      base_distance_m: null, selected_distance_m: null}],
  }}]};
  return renderToStaticMarkup(React.createElement(ProposalResults,
    {result, activeScenarioId: 'selected', onSelectScenario: () => {}}));
}
const unknown = render(null);
assert(unknown.includes('Unverified'));
assert(!unknown.includes('Outside time window') && !unknown.includes('Time window passed'));
assert(!unknown.includes('Road estimate only') && !unknown.includes('AMap verified'));
assert(!unknown.includes('0 min') && !unknown.includes('0 m<'));
assert(render(false, true).includes('Outside time window'));
assert(render(true, true).includes('Time window passed'));
''')
