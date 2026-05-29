param(
    [string]$HostAddress = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir

$defaultCondaPython = Join-Path $env:LOCALAPPDATA "anaconda3\envs\ortools_env\python.exe"
if (-not $env:BACKEND_PYTHON -and (Test-Path -LiteralPath $defaultCondaPython)) {
    $env:BACKEND_PYTHON = $defaultCondaPython
}
if (-not $env:BACKEND_PYTHON) {
    $env:BACKEND_PYTHON = "python"
}
if ($HostAddress) {
    $env:BRP_BACKEND_HOST = $HostAddress
}
if ($Port -gt 0) {
    $env:BRP_BACKEND_PORT = [string]$Port
}
if (-not $env:BRP_BACKEND_HOST) {
    $env:BRP_BACKEND_HOST = "127.0.0.1"
}
if (-not $env:BRP_BACKEND_PORT) {
    $env:BRP_BACKEND_PORT = "8001"
}

Set-Location (Join-Path $RootDir "apps\backend")
Write-Host "Starting BRP backend on http://$($env:BRP_BACKEND_HOST):$($env:BRP_BACKEND_PORT)"
& $env:BACKEND_PYTHON backend_service.py
