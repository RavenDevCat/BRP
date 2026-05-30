# BRP React Frontend

This is the React frontend for BRP: Bus Route Planner. It is the long-term
browser UI for Route Audit and Side Tools. KR already serves this app publicly
behind `brp-kr.example.com`; domestic public hostnames still serve Streamlit
until their separate React cutover.

The browser talks to the backend through same-origin `/api/*` routes. Workbook
parsing, provider keys, geocoding/cache reuse, aggregation prep, job creation,
and generated outputs stay server-side in Python.

Current local ports:

- Backend API: `127.0.0.1:8001`
- legacy Streamlit client: `127.0.0.1:8501`
- Vite React dev server: `127.0.0.1:5173`

Current routes:

- `/`: Route Audit dashboard
- `/new`: new Route Audit job
- `/jobs`: job history
- `/jobs/$jobId`: job detail
- `/distance`: Distance & Cost
- `/fleet`: Fleet Planner

## Development

```powershell
cd apps/web
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8001`.

## Production-Style Serving

Production-style serving is static assets plus an API proxy:

- build with `npm run build`
- serve `apps/web/dist/assets/*` as static files
- serve `apps/web/dist/index.html` for non-API paths so direct navigation works
- proxy `/api/*` to the backend service

The repository includes a lightweight static/proxy server for hosts that do not
have Node.js in PATH:

```bash
python ops/scripts/serve_react_static.py \
  --dist-dir apps/web/dist \
  --backend-url http://127.0.0.1:8001 \
  --host 127.0.0.1 \
  --port 4173
```

KR uses this static/proxy pattern. Build React locally and copy `apps/web/dist`
to KR when frontend assets change.

## API Surface

The React app uses backend routes for:

- health, identity, job list, job detail, and AI Audit
- workbook template, demo workbook download, preview, and submit
- Distance & Cost workbook preview, reference distance, and route cost
- Fleet Planner preview, geocoding, clustering, route preview, global plan,
  generated workbook download, and generated-plan submission
