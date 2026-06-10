$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir

$nginxExe = if ($env:BRP_NGINX_EXE) { $env:BRP_NGINX_EXE } else { "C:\tools\nginx\nginx.exe" }
$nginxRoot = if ($env:BRP_NGINX_ROOT) { $env:BRP_NGINX_ROOT } else { Join-Path $RootDir "state\nginx" }
$listenPort = if ($env:BRP_NGINX_PUBLIC_PORT) { $env:BRP_NGINX_PUBLIC_PORT } else { "8501" }

if (-not (Test-Path -LiteralPath $nginxExe)) {
    throw "nginx executable not found: $nginxExe"
}
if (-not (Test-Path -LiteralPath $nginxRoot)) {
    throw "nginx root not found: $nginxRoot"
}

$listenPattern = "127.0.0.1:$listenPort"
$listening = netstat -ano | Select-String -SimpleMatch $listenPattern | Select-String -SimpleMatch "LISTENING"
if ($listening) {
    Write-Host "BRP nginx public already listening on $listenPattern"
    exit 0
}

Write-Host "Starting BRP nginx public from $nginxRoot"
Start-Process -FilePath $nginxExe -ArgumentList @("-p", $nginxRoot, "-c", "conf\nginx.conf") -WindowStyle Hidden
