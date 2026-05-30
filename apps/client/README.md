# BRP Client Helpers And Legacy UI

This package contains the legacy Streamlit client and shared Python helper code
used by the backend for workbook parsing, geocoding, cache reuse, aggregation,
and output rendering.

New product UI work should normally happen in `apps/web`. Changes here are still
important when they affect shared Python behavior or the operator/legacy
Streamlit workflow.

## What Lives Here

- `app.py`: legacy Streamlit/operator UI
- `client_core.py`: legacy client workflow and backend API calls
- `client_runtime.py`: workbook preprocessing, geocoding, usage counters,
  cache handling, subway/nearby aggregation helpers, and map rendering
- `api_rate_limit.py`: cross-process provider request limiter for client-side
  helper calls
- `cache/`: ignored runtime cache directory with selected committed seed files
- `outputs/`: ignored generated map/report artifacts

## What Does Not Live Here

- backend HTTP service
- planner worker orchestration
- React product UI
- OSRM Docker runtime

## Setup

From the repository root:

```bash
pip install -r apps/client/requirements.txt
```

Provider keys and runtime settings belong in `ops/env/local.env` or the shell,
not in committed files.

## Run Legacy UI

From the repository root:

```bash
./ops/scripts/run_client.sh
```

Default local URL:

```text
http://127.0.0.1:8501
```

## Runtime Data

Preserve these across server moves and deployments:

- `apps/client/cache`
- `apps/client/cache/google_geocode_usage.json`
- `apps/client/outputs`
- server-local env files

Do not write the Google usage JSON directly. Use the reservation helpers in
`client_runtime.py` so concurrent processes do not lose increments or overshoot
the monthly cap.
