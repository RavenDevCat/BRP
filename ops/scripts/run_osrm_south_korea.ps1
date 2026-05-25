$ErrorActionPreference = "Stop"

$dataRoot = if ($env:OSRM_LOCAL_DATA_DIR) { $env:OSRM_LOCAL_DATA_DIR } else { "C:\brp-osrm-data" }
$bindHost = if ($env:OSRM_BIND_HOST) { $env:OSRM_BIND_HOST } else { "127.0.0.1" }
$port = if ($env:OSRM_SOUTH_KOREA_PORT) { $env:OSRM_SOUTH_KOREA_PORT } else { "5006" }
$maxTableSize = if ($env:OSRM_MAX_TABLE_SIZE) { $env:OSRM_MAX_TABLE_SIZE } else { "1000" }
$datasetDir = if ($env:OSRM_SOUTH_KOREA_DATASET_DIR) { $env:OSRM_SOUTH_KOREA_DATASET_DIR } else { Join-Path $dataRoot "south-korea" }
$datasetFile = if ($env:OSRM_SOUTH_KOREA_DATASET_FILE) { $env:OSRM_SOUTH_KOREA_DATASET_FILE } else { "south-korea-latest.osrm" }
$containerName = if ($env:OSRM_SOUTH_KOREA_CONTAINER_NAME) { $env:OSRM_SOUTH_KOREA_CONTAINER_NAME } else { "osrm-south-korea" }

if (-not (Test-Path -LiteralPath $datasetDir -PathType Container)) {
    throw "South Korea OSRM dataset directory not found: $datasetDir"
}

$datasetPath = Join-Path $datasetDir $datasetFile
if (-not (Test-Path -LiteralPath $datasetPath -PathType Leaf)) {
    throw "South Korea OSRM dataset file not found: $datasetPath"
}

docker rm -f $containerName 2>$null | Out-Null

$dockerDatasetDir = $datasetDir -replace "\\", "/"
docker run -d --name $containerName `
    -p "${bindHost}:${port}:5000" `
    -v "${dockerDatasetDir}:/data:ro" `
    osrm/osrm-backend `
    osrm-routed --algorithm mld --max-table-size $maxTableSize "/data/$datasetFile"

Write-Host "South Korea OSRM is starting at http://${bindHost}:${port}"
