# BRP Web Preview

This is the isolated React frontend for the BRP migration path.

It runs beside the current Streamlit client:

- Backend API: `127.0.0.1:8001`
- Streamlit client: `127.0.0.1:8501`
- React web preview: `127.0.0.1:5173`

The preview reads additive `/api/*` backend routes and does not import from
`apps/client`.

## Development

```powershell
cd apps/web
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8001`.

## First Slice

The first slice is intentionally read-only:

- `/api/health`
- `/api/me`
- `/api/jobs`
- `/api/jobs/:jobId`

## Submission Slice

The `/new` route now covers the first current-plan submission path:

- upload `.xlsx` / `.xlsm`
- validate `current_plan_assignments` and `current_plan_fleet`
- auto-fill fleet slot assumptions from the workbook
- submit a backend job and open its detail route

The browser sends the workbook as base64 JSON to:

- `/api/workbooks/preview`
- `/api/workbooks/submit`

Python still performs workbook parsing, geocoding/cache reuse, aggregation prep,
and job creation server-side so provider keys are not exposed in the browser.
