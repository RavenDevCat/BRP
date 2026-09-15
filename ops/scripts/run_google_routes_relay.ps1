param([Parameter(Mandatory=$true)][string]$EnvFile)
$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir -EnvFile $EnvFile
if (-not $env:RELAY_PYTHON -or -not $env:BRP_GOOGLE_ROUTES_RELAY_HOST) {
    throw "Dedicated Routes relay Python and private bind address must be configured"
}
$port = if ($env:BRP_GOOGLE_ROUTES_RELAY_PORT) { $env:BRP_GOOGLE_ROUTES_RELAY_PORT } else { "8813" }
Set-Location $RootDir
& $env:RELAY_PYTHON -m uvicorn google_routes_relay:app --app-dir (Join-Path $RootDir "ops\relay") --host $env:BRP_GOOGLE_ROUTES_RELAY_HOST --port $port --no-access-log
exit $LASTEXITCODE
