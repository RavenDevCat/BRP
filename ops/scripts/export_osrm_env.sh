#!/bin/bash

# Convenience defaults for a full local/dev OSRM stack. Server-local env files
# may define only the regions available on that server; do not overwrite them.
export OSRM_BASE_URL_CHINA_SHANGHAI="${OSRM_BASE_URL_CHINA_SHANGHAI:-http://127.0.0.1:5002}"
export OSRM_BASE_URL_CHINA_BEIJING="${OSRM_BASE_URL_CHINA_BEIJING:-http://127.0.0.1:5003}"
export OSRM_BASE_URL_CHINA_SUZHOU="${OSRM_BASE_URL_CHINA_SUZHOU:-http://127.0.0.1:5004}"
export OSRM_BASE_URL_CHINA_XIAN="${OSRM_BASE_URL_CHINA_XIAN:-http://127.0.0.1:5005}"
export OSRM_BASE_URL_SOUTH_KOREA="${OSRM_BASE_URL_SOUTH_KOREA:-http://127.0.0.1:5006}"
export OSRM_BASE_URL_SOUTH_KOREA_SEOUL="${OSRM_BASE_URL_SOUTH_KOREA_SEOUL:-$OSRM_BASE_URL_SOUTH_KOREA}"
