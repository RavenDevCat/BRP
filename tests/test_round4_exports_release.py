import base64
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import openpyxl
import pytest

ROOT=Path(__file__).resolve().parents[1]
for sub in ("apps","apps/client","apps/backend","ops/scripts"):
    sys.path.insert(0,str(ROOT/sub))


@pytest.mark.parametrize("current",[True,False])
def test_deferred_export_uses_saved_cells_only(monkeypatch,current):
    import api_app as api
    import backend_service as service
    import demand_routing as fleet
    from test_fleet_measurement_views import source
    sample=source()
    sample["routes"][0]["ordered_points"][0]["address"]="=sample"
    if not current:
        sample["routes"][0]["route_evidence"]["complete"]=False
    original=deepcopy(sample)
    monkeypatch.delenv("BRP_DEFER_FLEET_EXPORTS",raising=False)
    ref=service._route_plan_response(sample,workbook_file_name="plan.xlsx",already_measured=True)
    monkeypatch.setenv("BRP_DEFER_FLEET_EXPORTS","1")
    events=[]
    build=fleet.build_generated_plan_workbook_bytes
    def book(*args):
        events.append("export")
        return build(*args)
    monkeypatch.setattr(fleet,"build_generated_plan_workbook_bytes",book)
    monkeypatch.setattr(fleet,"build_route_preview_map_html",lambda *_:pytest.fail("unexpected HTML"))
    monkeypatch.setattr(service,"_attach_fleet_route_measurements",lambda *a,**kw:pytest.fail("no routing at export"))
    new=service._route_plan_response(sample,workbook_file_name="plan.xlsx",already_measured=True)
    history=service._ensure_fleet_planner_map_data(new)
    assert history["workbook_deferred"] and new["workbook_deferred"] and "workbook_base64" not in new
    assert events==[] and sample==original and ref["map_data"]==new["map_data"]
    def compact(obj):
        if isinstance(obj,list):
            return [compact(v) for v in obj]
        if isinstance(obj,dict):
            return {k:compact(v) for k,v in obj.items() if k not in {"geometry","geometry_segments","polyline","leg_details"}}
        return obj
    resp=api.fleet_workbook(api.FlexiblePayload(**compact({k:history[k] for k in ("routes","school","summary")})))
    assert events==["export"] and resp.status_code==200
    def read(payload):
        return openpyxl.load_workbook(BytesIO(base64.b64decode(payload)))
    a,b=read(ref["workbook_base64"]),read(json.loads(resp.body)["workbook_base64"])
    assert a.sheetnames==b.sheetnames
    for name in a.sheetnames:
        assert list(a[name].values)==list(b[name].values)
    assert b["current_plan_assignments"]["F2"].data_type=="s"
    assert b["Route Measurements"]["B2"].value == (7 if current else None)


@pytest.mark.skipif(sys.platform!="linux",reason="CN systemd deployment is Linux-only")
@pytest.mark.parametrize("bad_ready",[True,False])
def test_real_align_script_rolls_back_on_readiness_failure(tmp_path,bad_ready):
    def git(where,*args):
        return subprocess.check_output(["git","-C",str(where),*args],text=True,stderr=subprocess.STDOUT).strip()
    origin=tmp_path/"origin";stage=tmp_path/"stage";prod=tmp_path/"prod"
    for d in (origin,stage):d.mkdir()
    git(origin,"init","--bare","-b","main")
    git(stage,"init","-b","main")
    git(stage,"config","user.name","QA");git(stage,"config","user.email","qa@example.invalid")
    (stage/"tracked").write_text("1")
    git(stage,"add","tracked");git(stage,"commit","-m","initial")
    git(stage,"remote","add","origin",str(origin));git(stage,"push","-u","origin","main")
    old=git(stage,"rev-parse","HEAD")
    git(tmp_path,"clone",str(origin),str(prod))
    (stage/"tracked").write_text("2");git(stage,"commit","-am","second");git(stage,"push")
    version=git(stage,"rev-parse","--short","HEAD")
    from test_round4_data_release import bundle
    bundle(stage/"apps/web/dist",version)
    bundle(prod/"apps/web/dist",old[:7])
    tools=stage/"ops/scripts";tools.mkdir(parents=True)
    for name in ["verified_web_release.py","align_cn_prod_from_staging.sh"]:
        shutil.copy2(ROOT/"ops/scripts"/name,tools/name)
    fake=tmp_path/"bin";fake.mkdir()
    for name,body in [("sudo",'if [[ $1 == -n && $2 == -u && $3 == www-data && $4 == -- ]]; then shift 4; exec "$@"; fi\necho "$*" >> "$RESTARTS"'),("curl",'''case "${*: -1}" in */ready) echo '{"status":"ready"}'; exit "$BAD_READY";; *) exit 0;; esac''')]:
        cmd=fake/name;cmd.write_text("#!/usr/bin/env bash\n"+body+"\n");cmd.chmod(0o755)
    log=tmp_path/"restarts"
    env={**os.environ,"PATH":str(fake)+":"+os.environ["PATH"],"PROD_ROOT":str(prod),"STAGING_ROOT":str(stage),
         "BAD_READY":str(int(bad_ready)),"RESTARTS":str(log),"PROD_BACKEND_HEALTH_ATTEMPTS":"1","PROD_BACKEND_HEALTH_DELAY_SECONDS":"0"}
    run=subprocess.run(["bash",str(tools/"align_cn_prod_from_staging.sh"),version],env=env,capture_output=True,text=True)
    assert run.returncode==(1 if bad_ready else 0),run.stderr
    assert git(prod,"rev-parse","HEAD")== (old if bad_ready else git(stage,"rev-parse","HEAD"))
    assert len(log.read_text().splitlines())==(2 if bad_ready else 1)
    from verified_web_release import verified_entry
    assert verified_entry(prod/"apps/web/dist",old[:7] if bad_ready else version)
