"""Build an anonymous offline fixture for round4-history.cjs. Pass the output file."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / p) for p in ("apps","apps/client","apps/backend","tests")]
with TemporaryDirectory() as tmp:
    os.environ.update(BRP_RUNTIME_DB_PATH=str(Path(tmp)/"runtime.sqlite"),
        BRP_QUOTA_DB_PATH=str(Path(tmp)/"quota.sqlite"), BRP_DEFER_FLEET_EXPORTS="1")
    sys.addaudithook(lambda e,a: (_ for _ in ()).throw(RuntimeError("network disabled")) if e=="socket.connect" else None)
    from test_fleet_measurement_views import source
    from backend_service import _route_plan_response
    from api_app import fleet_workbook,FlexiblePayload
    plan=_route_plan_response(source(),workbook_file_name="qa.xlsx",already_measured=True)
    out=fleet_workbook(FlexiblePayload(**{k:plan[k] for k in ("school","routes","summary")}))
    Path(sys.argv[1]).write_text(json.dumps({"plan":plan,"export":json.loads(out.body)}))
