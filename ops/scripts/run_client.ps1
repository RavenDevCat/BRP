param(
    [string]$HostAddress = "",
    [int]$Port = 0,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir

$defaultCondaPython = Join-Path $env:LOCALAPPDATA "anaconda3\envs\ortools_env\python.exe"
if (-not $env:CLIENT_PYTHON -and (Test-Path -LiteralPath $defaultCondaPython)) {
    $env:CLIENT_PYTHON = $defaultCondaPython
}
if (-not $env:CLIENT_PYTHON) {
    $env:CLIENT_PYTHON = "python"
}
if ($HostAddress) {
    $env:STREAMLIT_SERVER_ADDRESS = $HostAddress
}
if ($Port -gt 0) {
    $env:STREAMLIT_SERVER_PORT = [string]$Port
}
if (-not $env:STREAMLIT_SERVER_ADDRESS) {
    $env:STREAMLIT_SERVER_ADDRESS = "127.0.0.1"
}
if (-not $env:STREAMLIT_SERVER_PORT) {
    $env:STREAMLIT_SERVER_PORT = "8501"
}
if (-not $env:BRP_CLIENT_CACHE_DIR) {
    $env:BRP_CLIENT_CACHE_DIR = Join-Path $RootDir "apps\client\cache"
}
if (-not $env:BRP_BACKEND_CACHE_DIR) {
    $env:BRP_BACKEND_CACHE_DIR = Join-Path $RootDir "apps\backend\cache"
}
New-Item -ItemType Directory -Force $env:BRP_CLIENT_CACHE_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_BACKEND_CACHE_DIR | Out-Null

$streamlitArgs = @(
    "-m", "streamlit", "run", "app.py",
    "--server.address", $env:STREAMLIT_SERVER_ADDRESS,
    "--server.port", $env:STREAMLIT_SERVER_PORT
)
if ($Headless) {
    $streamlitArgs += @("--server.headless", "true")
}

Set-Location (Join-Path $RootDir "apps\client")
Write-Host "Starting BRP client on http://$($env:STREAMLIT_SERVER_ADDRESS):$($env:STREAMLIT_SERVER_PORT)"
& $env:CLIENT_PYTHON @streamlitArgs
