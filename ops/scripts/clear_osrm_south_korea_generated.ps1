$ErrorActionPreference = "Stop"

$dataRoot = if ($env:OSRM_LOCAL_DATA_DIR) { $env:OSRM_LOCAL_DATA_DIR } else { "C:\brp-osrm-data" }
$datasetDir = if ($env:OSRM_SOUTH_KOREA_DATASET_DIR) { $env:OSRM_SOUTH_KOREA_DATASET_DIR } else { Join-Path $dataRoot "south-korea" }
$datasetFile = if ($env:OSRM_SOUTH_KOREA_DATASET_FILE) { $env:OSRM_SOUTH_KOREA_DATASET_FILE } else { "south-korea-latest.osrm" }

$resolvedDatasetDir = (Resolve-Path -LiteralPath $datasetDir).Path
if ($resolvedDatasetDir -ne "C:\brp-osrm-data\south-korea") {
    throw "Unexpected South Korea OSRM dataset directory: $resolvedDatasetDir"
}

$baseName = [System.IO.Path]::GetFileName($datasetFile)
Get-ChildItem -LiteralPath $resolvedDatasetDir -Filter "$baseName*" |
    Where-Object { $_.Name -ne "south-korea-latest.osm.pbf" } |
    Remove-Item -Force

Get-ChildItem -LiteralPath $resolvedDatasetDir | Select-Object Name,Length,LastWriteTime
