$ErrorActionPreference = "Stop"

$dataRoot = if ($env:OSRM_LOCAL_DATA_DIR) { $env:OSRM_LOCAL_DATA_DIR } else { "C:\brp-osrm-data" }
$datasetDir = if ($env:OSRM_SOUTH_KOREA_DATASET_DIR) { $env:OSRM_SOUTH_KOREA_DATASET_DIR } else { Join-Path $dataRoot "south-korea" }
$pbfFile = if ($env:OSRM_SOUTH_KOREA_PBF_FILE) { $env:OSRM_SOUTH_KOREA_PBF_FILE } else { "south-korea-latest.osm.pbf" }
$datasetFile = if ($env:OSRM_SOUTH_KOREA_DATASET_FILE) { $env:OSRM_SOUTH_KOREA_DATASET_FILE } else { "south-korea-latest.osrm" }
$maxTableSize = if ($env:OSRM_MAX_TABLE_SIZE) { $env:OSRM_MAX_TABLE_SIZE } else { "1000" }
$extractThreads = if ($env:OSRM_EXTRACT_THREADS) { $env:OSRM_EXTRACT_THREADS } else { "2" }

New-Item -ItemType Directory -Force -Path $datasetDir | Out-Null

$pbfPath = Join-Path $datasetDir $pbfFile
if (-not (Test-Path -LiteralPath $pbfPath -PathType Leaf)) {
    throw "South Korea PBF file not found: $pbfPath"
}

$dockerDatasetDir = $datasetDir -replace "\\", "/"

docker run --rm -t -v "${dockerDatasetDir}:/data" osrm/osrm-backend `
    osrm-extract -p /opt/car.lua --threads $extractThreads "/data/$pbfFile"
if ($LASTEXITCODE -ne 0) {
    throw "osrm-extract failed with exit code $LASTEXITCODE"
}
$requiredAfterExtract = @(
    "$datasetFile",
    "$datasetFile.ebg",
    "$datasetFile.ebg_nodes",
    "$datasetFile.enw",
    "$datasetFile.geometry",
    "$datasetFile.names",
    "$datasetFile.properties"
)
foreach ($file in $requiredAfterExtract) {
    if (-not (Test-Path -LiteralPath (Join-Path $datasetDir $file) -PathType Leaf)) {
        throw "osrm-extract did not produce required file: $file"
    }
}

docker run --rm -t -v "${dockerDatasetDir}:/data" osrm/osrm-backend `
    osrm-partition "/data/$datasetFile"
if ($LASTEXITCODE -ne 0) {
    throw "osrm-partition failed with exit code $LASTEXITCODE"
}
$requiredAfterPartition = @(
    "$datasetFile.partition",
    "$datasetFile.cells"
)
foreach ($file in $requiredAfterPartition) {
    if (-not (Test-Path -LiteralPath (Join-Path $datasetDir $file) -PathType Leaf)) {
        throw "osrm-partition did not produce required file: $file"
    }
}

docker run --rm -t -v "${dockerDatasetDir}:/data" osrm/osrm-backend `
    osrm-customize "/data/$datasetFile"
if ($LASTEXITCODE -ne 0) {
    throw "osrm-customize failed with exit code $LASTEXITCODE"
}
$requiredAfterCustomize = @(
    "$datasetFile.cell_metrics"
)
foreach ($file in $requiredAfterCustomize) {
    if (-not (Test-Path -LiteralPath (Join-Path $datasetDir $file) -PathType Leaf)) {
        throw "osrm-customize did not produce required file: $file"
    }
}

Write-Host "South Korea OSRM dataset prepared in $datasetDir"
Write-Host "Run .\ops\scripts\run_osrm_south_korea.ps1 to start OSRM on port 5006."
