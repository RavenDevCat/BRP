#!/bin/bash

export OSRM_BASE_URL_SOUTH_KOREA="${OSRM_BASE_URL_SOUTH_KOREA:-http://127.0.0.1:5006}"
export OSRM_BASE_URL_SOUTH_KOREA_SEOUL="${OSRM_BASE_URL_SOUTH_KOREA_SEOUL:-$OSRM_BASE_URL_SOUTH_KOREA}"

# Keep the generic fallback pointed at Korea for Korea-only deployments.
export OSRM_BASE_URL="${OSRM_BASE_URL:-$OSRM_BASE_URL_SOUTH_KOREA}"
