param(
    [string]$HostAddress = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir

$defaultBackendPython = Join-Path $env:LOCALAPPDATA "anaconda3\envs\ortools_env\python.exe"
if (-not $env:RELAY_PYTHON -and $env:BACKEND_PYTHON) {
    $env:RELAY_PYTHON = $env:BACKEND_PYTHON
}
if (-not $env:RELAY_PYTHON -and (Test-Path -LiteralPath $defaultBackendPython)) {
    $env:RELAY_PYTHON = $defaultBackendPython
}
if (-not $env:RELAY_PYTHON) {
    $env:RELAY_PYTHON = "python"
}
if ($HostAddress) {
    $env:BRP_GOOGLE_GEOCODE_RELAY_HOST = $HostAddress
}
if ($Port -gt 0) {
    $env:BRP_GOOGLE_GEOCODE_RELAY_PORT = [string]$Port
}
if (-not $env:BRP_GOOGLE_GEOCODE_RELAY_HOST) {
    $env:BRP_GOOGLE_GEOCODE_RELAY_HOST = "127.0.0.1"
}
if (-not $env:BRP_GOOGLE_GEOCODE_RELAY_PORT) {
    $env:BRP_GOOGLE_GEOCODE_RELAY_PORT = "8811"
}
if (-not $env:BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH) {
    $env:BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH = Join-Path $RootDir "state\google_geocode_relay_usage.json"
}

$StateDir = Split-Path -Parent $env:BRP_GOOGLE_GEOCODE_RELAY_USAGE_PATH
if ($StateDir) {
    New-Item -ItemType Directory -Force $StateDir | Out-Null
}

Set-Location $RootDir
$RelayAppDir = Join-Path $RootDir "ops\relay"
$UvicornArgs = @(
    "-m", "uvicorn", "google_geocode_relay:app",
    "--app-dir", $RelayAppDir,
    "--host", $env:BRP_GOOGLE_GEOCODE_RELAY_HOST,
    "--port", $env:BRP_GOOGLE_GEOCODE_RELAY_PORT
)
Write-Host "Starting BRP Google geocode FastAPI relay on http://$($env:BRP_GOOGLE_GEOCODE_RELAY_HOST):$($env:BRP_GOOGLE_GEOCODE_RELAY_PORT)"
& $env:RELAY_PYTHON @UvicornArgs
