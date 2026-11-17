from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_disabled_impact_limit_does_not_hide_complete_traffic_or_missing_measurements():
    script = r'''
const ts = require("typescript");
const fs = require("fs");
const vm = require("vm");
const assert = require("node:assert/strict");
const source = ts.createSourceFile("view.tsx", fs.readFileSync("src/features/results/job-result-view.tsx", "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ["buildScenarioDecisionMetrics", "asRecord", "asRecordArray", "asStringArray", "stringValue", "routePassengerCount"];
const functions = source.statements.filter(s => ts.isFunctionDeclaration(s) && names.includes(s.name?.text));
assert.equal(functions.length, names.length);
const compiled = ts.transpileModule(functions.map(s => s.getText(source)).join("\n"), {compilerOptions:{target:ts.ScriptTarget.ES2020}}).outputText;
const context = {exports:{}};
vm.runInNewContext(compiled + "\nexports.metrics = buildScenarioDecisionMetrics;", context);
const metrics = context.exports.metrics;
const scenario = {
  routes:[{route_id:"R1",load:4,nodes:[1,0],final_route_traffic_gate:{status:"failed",passes:false,time_window_overrun_minutes:5}}],
  points:[{passenger_count:0},{passenger_count:4}],
  traffic_gate:{status:"failed",checked_route_count:1,unavailable_route_count:0,failed_route_ids:["R1"]},
  final_time_impact_gate:{status:"not_applicable",limit_enabled:false,max_adverse_minutes:39,threshold_minutes:null,unavailable_route_count:0,unavailable_stop_count:0}
};
const original = JSON.stringify(scenario);
let value = metrics(scenario);
assert.equal(value.evidenceComplete,true);
assert.equal(value.timeImpactMaxAdverseMinutes,39);
assert.equal(value.timeImpactMaxOverLimitMinutes,0);
assert.equal(value.timeWindowMaxOverrunMinutes,5);
assert.equal(value.affectedRiderCount,4);
assert.equal(value.worstSource,"time_window");
assert.equal(JSON.stringify(scenario),original);
assert.equal(metrics({...scenario,final_time_impact_gate:{}}).evidenceComplete,false);
assert.equal(metrics({...scenario,final_time_impact_gate:{...scenario.final_time_impact_gate,limit_enabled:true}}).evidenceComplete,false);
assert.equal(metrics({...scenario,final_time_impact_gate:{...scenario.final_time_impact_gate,unavailable_stop_count:1}}).evidenceComplete,false);
assert.equal(metrics({...scenario,traffic_gate:{...scenario.traffic_gate,unavailable_route_count:1}}).evidenceComplete,false);
value = metrics({...scenario,final_time_impact_gate:{...scenario.final_time_impact_gate,status:"failed",limit_enabled:true,threshold_minutes:15}});
assert.equal(value.evidenceComplete,true);
assert.equal(value.timeImpactMaxOverLimitMinutes,24);
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT / "apps/web", capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
