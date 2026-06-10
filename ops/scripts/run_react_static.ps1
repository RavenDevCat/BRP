param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8501,
    [string]$BackendUrl = "http://127.0.0.1:8001"
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

$distDir = Join-Path $RootDir "apps\web\dist"
$serverScript = Join-Path $RootDir "ops\scripts\serve_react_static.py"
if (-not (Test-Path -LiteralPath $distDir)) {
    throw "React dist not found: $distDir"
}
if (-not (Test-Path -LiteralPath $serverScript)) {
    throw "React static server script not found: $serverScript"
}

Write-Host "Starting BRP React static server on http://$($HostAddress):$($Port)"
& $env:CLIENT_PYTHON $serverScript --dist-dir $distDir --backend-url $BackendUrl --host $HostAddress --port $Port
