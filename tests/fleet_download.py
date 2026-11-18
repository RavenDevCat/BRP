import base64
import json
import os
from io import BytesIO

from openpyxl import load_workbook


def download_workbook(result):
    if os.getenv("BRP_DEFER_FLEET_EXPORTS") == "1":
        import api_app
        assert result["workbook_deferred"] and "workbook_base64" not in result
        response = api_app.fleet_workbook(api_app.FlexiblePayload(
            **{key: result[key] for key in ("routes", "school", "summary")}))
        assert response.status_code == 200
        raw = json.loads(response.body)["workbook_base64"]
    else:
        raw = result["workbook_base64"]
    return load_workbook(BytesIO(base64.b64decode(raw)))
