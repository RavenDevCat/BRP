# Architecture Overview

## Client

Location: `apps/client`

Responsibilities:

- accept workbook uploads
- validate the required four columns
- geocode addresses
- search nearby subway stations
- build original, subway-aggregated, and nearby-aggregated stop sets
- submit prepared payloads to the backend
- render returned route maps

## Backend

Location: `apps/backend`

Responsibilities:

- receive prepared point sets
- select OSRM backend by country/city
- build full OSRM time and distance matrices
- solve routes with OR-Tools
- enrich final routes with OSRM geometry
- return structured JSON for the client

## Routing datasets

Current live OSRM coverage:

- Shanghai
- Beijing
- Suzhou
- Xi'an
- South Korea

## External runtime dependencies

- Docker OSRM containers
- Cloudflare Tunnel
- Kakao REST API key
- AMap API key

## Separation of concerns

- Client does user-facing preprocessing and presentation.
- Backend does heavy routing and optimization.
- OSRM provides map-aware travel times and route geometry.
- Cloudflare exposes client and backend over stable public URLs.
