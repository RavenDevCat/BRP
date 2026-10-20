from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_return_map_adapter_preserves_identity_limits_and_measurements():
    script = r"""
const ts = require("typescript");
const fs = require("fs");
const vm = require("vm");
const assert = require("node:assert/strict");
const compiled = ts.transpileModule(fs.readFileSync("src/features/distance/return-map-data.ts", "utf8"),
  {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const exports = {};
vm.runInNewContext(compiled, {exports, Intl, Date, Number, Math});
const {underReturnLimit, returnMapData} = exports;
const route = {route_id:"internal", display_route_id:"R2", route_index:4, status:"resolved",
  riders:2, stop_count:1, total_duration_s:2700, distance_m:5000, vehicle_id:3, bus_capacity:20,
  geometry:[[116,40],[116.1,40.1]], stops:[
    {stop_sequence:0, source_stop_sequence:1, source_node_id:0, address:"School", is_depot:true,
     lat:40,lng:116,passenger_count:0,ride_duration_s:0,arrival_at:"2026-10-20T07:40:00Z"},
    {stop_sequence:1, source_stop_sequence:0, source_node_id:8, address:"Stop", is_depot:false,
     lat:40.1,lng:116.1,passenger_count:2,ride_duration_s:2640}]};
assert.equal(underReturnLimit(route,45),false);
assert.equal(underReturnLimit(route,60),true);
assert.equal(underReturnLimit({...route,status:"failed"},60),false);
assert.equal(underReturnLimit({...route,total_duration_s:NaN},60),false);
const result = {routes:[route],summary:{route_count:1,rider_count:2}};
const before = JSON.stringify(result);
const data = returnMapData(result,"Protected Plan",45);
assert.equal(data.duration_limit_s,2700);
assert.equal(data.routes[0].id,"R2");
assert.equal(data.routes[0].route_index,4);
assert.equal(data.routes[0].bus_capacity,20);
assert.equal(data.stops[0].route_id,"R2");
assert.equal(data.stops[0].scheduled_time_label,"15:40");
assert.equal(data.stops[1].node_index,8);
assert.equal(data.summary.passenger_count,2);
assert.equal(data.summary.distance_m,5000);
assert.equal(data.stops[1].cumulative_duration_s,2640);
assert.equal(JSON.stringify(result),before);
const unavailable = returnMapData({...result,routes:[{...route,status:"failed"}]},"Current",60);
assert.equal(unavailable.routes[0].duration_s,null);
assert.equal(unavailable.summary.duration_s,null);
console.log("Return adapter assertions passed");
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT / "apps/web",
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
