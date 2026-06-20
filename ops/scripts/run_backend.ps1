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
if (-not $env:BRP_BACKEND_JOBS_DIR) {
    $env:BRP_BACKEND_JOBS_DIR = Join-Path $RootDir "state\jobs"
}
if (-not $env:BRP_SIDE_TOOLS_DIR) {
    $env:BRP_SIDE_TOOLS_DIR = Join-Path $RootDir "state\side_tools"
}
if (-not $env:BRP_CLIENT_CACHE_DIR) {
    $env:BRP_CLIENT_CACHE_DIR = Join-Path $RootDir "apps\client\cache"
}
if (-not $env:BRP_BACKEND_CACHE_DIR) {
    $env:BRP_BACKEND_CACHE_DIR = Join-Path $RootDir "apps\backend\cache"
}
if (-not $env:BRP_LIVE_TRAFFIC_SAMPLE_DIR) {
    $env:BRP_LIVE_TRAFFIC_SAMPLE_DIR = Join-Path $RootDir "state\traffic_samples"
}
if (-not $env:BRP_LIVE_TRAFFIC_BASELINE_DIR) {
    $env:BRP_LIVE_TRAFFIC_BASELINE_DIR = Join-Path $RootDir "state\traffic_baselines"
}
New-Item -ItemType Directory -Force $env:BRP_BACKEND_JOBS_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_SIDE_TOOLS_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_CLIENT_CACHE_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_BACKEND_CACHE_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_LIVE_TRAFFIC_SAMPLE_DIR | Out-Null
New-Item -ItemType Directory -Force $env:BRP_LIVE_TRAFFIC_BASELINE_DIR | Out-Null

Set-Location (Join-Path $RootDir "apps\backend")
if (-not $env:BRP_BACKEND_UVICORN_WORKERS) {
    $env:BRP_BACKEND_UVICORN_WORKERS = "1"
}
Write-Host "Starting BRP FastAPI backend on http://$($env:BRP_BACKEND_HOST):$($env:BRP_BACKEND_PORT) with $($env:BRP_BACKEND_UVICORN_WORKERS) worker(s)"
$uvicornArgs = @(
    "-m", "uvicorn", "api_app:app",
    "--host", $env:BRP_BACKEND_HOST,
    "--port", $env:BRP_BACKEND_PORT
)
if ($env:BRP_BACKEND_UVICORN_WORKERS -ne "1") {
    $uvicornArgs += @("--workers", $env:BRP_BACKEND_UVICORN_WORKERS)
}
$logDir = Join-Path $RootDir "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $logDir "backend-uvicorn-$stamp.out.log"
$stderrLog = Join-Path $logDir "backend-uvicorn-$stamp.err.log"
$process = Start-Process `
    -FilePath $env:BACKEND_PYTHON `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory (Get-Location).Path `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
Write-Host "BRP FastAPI uvicorn pid $($process.Id); stdout=$stdoutLog stderr=$stderrLog"
Start-Sleep -Seconds 3
$process.Refresh()
if ($process.HasExited) {
    Write-Error "BRP FastAPI uvicorn exited early with code $($process.ExitCode). See $stdoutLog and $stderrLog."
    if ($null -ne $process.ExitCode) {
        exit $process.ExitCode
    }
    exit 1
}
exit 0
