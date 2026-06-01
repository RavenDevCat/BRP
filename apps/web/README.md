# BRP React Frontend

This is the React frontend for BRP: Bus Route Planner. It is the long-term
browser UI for Route Audit and Side Tools.

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

Production-style serving is Nginx static hosting plus an API proxy:

- build with `npm run build`
- serve `apps/web/dist/assets/*` as static files
- serve `apps/web/dist/index.html` for non-API paths so direct navigation works
- proxy `/api/*` to the backend service

Linux deployments should install the managed Nginx site with:

```bash
sudo SITE_NAME=brp-staging \
  APP_ROOT=/opt/brp/staging/app \
  FRONTEND_PORT=8501 \
  BACKEND_URL=http://127.0.0.1:8001 \
  SERVER_NAMES="staging.example.com" \
  ops/scripts/install_nginx_react_site.sh
```

When `BRP_BACKEND_SERVICE_TOKEN` is set in the server environment, the generated
Nginx include injects the backend bearer token server-side for `/api/*`
requests. The browser does not receive the token.

## API Surface

The React app uses backend routes for:

- health, identity, job list, job detail, and AI Audit
- workbook template, demo workbook download, preview, and submit
- Distance & Cost workbook preview, reference distance, and route cost
- Fleet Planner preview, geocoding, clustering, route preview, global plan,
  generated workbook download, and generated-plan submission
