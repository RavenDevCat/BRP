# Operations Checklist

This document is the practical runbook for the live BRP stack.

## What must be running

The live system depends on four parts:

1. OSRM Docker containers
2. Backend service
3. Client Streamlit app
4. Cloudflare Tunnel

## Startup order

Use this order whenever you restart the full stack.

### 1. Start OSRM

```bash
/Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_osrm_stack.sh
```

Expected ports:

- `5002` Shanghai
- `5003` Beijing
- `5004` Suzhou
- `5005` Xian
- `5006` South Korea

To expose the Docker OSRM ports to operator access, bind them to all interfaces or to the operator access IP explicitly:

```bash
OSRM_BIND_HOST=0.0.0.0 /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_osrm_stack.sh
```

or

```bash
OSRM_BIND_HOST=<TAILSCALE_IP> /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_osrm_stack.sh
```

### 2. Start backend

```bash
source /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/export_osrm_env.sh
/Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_backend.sh
```

Expected port:

- `8001`

To expose the backend to operator access:

```bash
source /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/export_osrm_env.sh
BRP_BACKEND_HOST=0.0.0.0 /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_backend.sh
```

### 3. Start client

```bash
/Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_client.sh
```

Expected port:

- `8501`

To expose the client to operator access:

```bash
STREAMLIT_SERVER_ADDRESS=0.0.0.0 /Users/developer/Library/CloudStorage/OneDrive-EiM/python\ stuff/busing\ routing\ designer/ops/scripts/run_client.sh
```

### 4. Start Cloudflare Tunnel

```bash
/Users/developer/bin/cloudflared tunnel run osrm-tunnel
```

Expected public URLs:

- `https://client.example.com`
- `https://brp.example.com`
- `https://osrm-shanghai.example.com`
- `https://osrm-beijing.example.com`
- `https://osrm-suzhou.example.com`
- `https://osrm-xian.example.com`
- `https://osrm-south-korea.example.com`

## Quick health checks

### Check OSRM containers

```bash
docker ps
```

If Cloudflare Tunnel is configured for OSRM hostnames, you can also test them through the public domains:

```bash
curl -i https://osrm-shanghai.example.com
curl -i https://osrm-beijing.example.com
curl -i https://osrm-suzhou.example.com
curl -i https://osrm-xian.example.com
curl -i https://osrm-south-korea.example.com
```

### Check backend

```bash
lsof -iTCP:8001 -sTCP:LISTEN -n -P
curl -s http://127.0.0.1:8001/health
curl -i https://brp.example.com/health
```

Expected result:

- local backend listens on `8001`
- health endpoint returns `{"status": "ok"}`

### Check client

```bash
lsof -iTCP:8501 -sTCP:LISTEN -n -P
curl -I https://client.example.com
```

Expected result:

- local client listens on `8501`
- public client returns `HTTP 200`

### Check Cloudflare Tunnel

```bash
ps aux | grep cloudflared
```

Expected result:

- one active `cloudflared tunnel run osrm-tunnel` process

## What to restart when something changes

### Restart backend if:

- backend code changes
- OSRM environment mapping changes
- backend config changes

### Restart client if:

- Streamlit UI code changes
- local preprocessing logic changes
- client-side settings or text changes

### Restart Cloudflare Tunnel if:

- tunnel process exits
- `~/.cloudflared/config.yml` changes
- public domains stop forwarding while local services are still healthy

### Restart OSRM stack if:

- OSRM datasets are replaced
- Docker containers stop
- OSRM port mappings change

## Common issue patterns

### Client page opens, but `Run Planner` fails

Check:

```bash
lsof -iTCP:8001 -sTCP:LISTEN -n -P
curl -i https://brp.example.com/health
```

Most likely cause:

- backend is down

### Public URLs fail, but local services are healthy

Check:

```bash
ps aux | grep cloudflared
```

Most likely cause:

- Cloudflare Tunnel is down

### Routing requests fail for one city or country

Check:

```bash
docker ps
```

Most likely cause:

- relevant OSRM container is missing or unhealthy

### Korean geocoding fails

Common causes:

- input is a pure English postal-style Korean address
- Kakao key or Kakao service availability issue

Best practice:

- use Korean addresses
- or use English place names instead of full English postal addresses

## Notes

- Codebase root:
  - `/Users/developer/Library/CloudStorage/OneDrive-EiM/python stuff/busing routing designer`
- Client root:
  - `/Users/developer/Library/CloudStorage/OneDrive-EiM/python stuff/busing routing designer/apps/client`
- Backend root:
  - `/Users/developer/Library/CloudStorage/OneDrive-EiM/python stuff/busing routing designer/apps/backend`
- OSRM data root:
  - `/Users/developer/brp-osrm-data`
