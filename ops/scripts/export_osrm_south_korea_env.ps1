$env:OSRM_BASE_URL_SOUTH_KOREA = if ($env:OSRM_BASE_URL_SOUTH_KOREA) { $env:OSRM_BASE_URL_SOUTH_KOREA } else { "http://127.0.0.1:5006" }
$env:OSRM_BASE_URL_SOUTH_KOREA_SEOUL = if ($env:OSRM_BASE_URL_SOUTH_KOREA_SEOUL) { $env:OSRM_BASE_URL_SOUTH_KOREA_SEOUL } else { $env:OSRM_BASE_URL_SOUTH_KOREA }

# Keep the generic fallback pointed at Korea for Korea-only deployments.
$env:OSRM_BASE_URL = if ($env:OSRM_BASE_URL) { $env:OSRM_BASE_URL } else { $env:OSRM_BASE_URL_SOUTH_KOREA }
