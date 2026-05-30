# BRP

Client-only project for the BRP: Bus Route Planner split architecture.

What lives here:
- `app.py`: Streamlit client
- `client_core.py`: client-side workflow and backend API calls
- `client_runtime.py`: Excel-to-address preprocessing helpers, geocode, subway lookup, nearby clustering, and map rendering

What does not live here:
- route solving
- OSRM compute pipeline
- backend HTTP service

Expected backend:
- `/health`
- `/compute`

Typical local run:

```bash
streamlit run app.py
```

Then point `Backend Base URL` to your backend service or Cloudflare Tunnel URL.
