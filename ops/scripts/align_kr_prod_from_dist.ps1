param(
  [Parameter(Mandatory = $true)]
  [string]$TargetHead,

  [string]$Repo = "C:\Users\Bus.EIM\BRP",
  [string]$ArchivePath = "",
  [string]$BackendTaskName = "BRP Backend",
  [string]$NginxTaskName = "BRP-Nginx-Public",
  [int]$BackendPort = 8001
)

$ErrorActionPreference = "Stop"

function Wait-BrpBackendHealth {
  param(
    [string]$Uri = "http://127.0.0.1:8001/health",
    [int]$Attempts = 15,
    [int]$DelaySeconds = 2
  )

  $lastError = $null
  for ($index = 1; $index -le $Attempts; $index++) {
    try {
      return Invoke-RestMethod -Uri $Uri -TimeoutSec 10
    } catch {
      $lastError = $_
      Start-Sleep -Seconds $DelaySeconds
    }
  }
  throw "KR backend health did not become ready after $Attempts attempts: $lastError"
}

function Stop-BrpBackendListener {
  param(
    [int]$Port = 8001,
    [int]$Attempts = 15,
    [int]$DelaySeconds = 1
  )

  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  $processIds = @($connections | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)
  foreach ($processId in $processIds) {
    if (-not $processId) {
      continue
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
      continue
    }
    Write-Output "Stopping existing KR backend listener pid=$processId process=$($process.ProcessName) port=$Port"
    Stop-Process -Id $processId -Force -ErrorAction Stop
  }

  for ($index = 1; $index -le $Attempts; $index++) {
    $stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $stillListening) {
      return
    }
    Start-Sleep -Seconds $DelaySeconds
  }
  throw "KR backend port $Port is still occupied after stopping Scheduled Task and listener process."
}

if (-not $ArchivePath) {
  $ArchivePath = Join-Path $Repo "state\brp-web-dist-$TargetHead.tgz"
}

if (-not (Test-Path -LiteralPath $ArchivePath)) {
  throw "Dist archive not found: $ArchivePath"
}

Set-Location $Repo

git fetch origin main
git checkout main
git pull --ff-only origin main

$after = (git rev-parse --short HEAD).Trim()
if ($after -ne $TargetHead) {
  throw "KR head $after does not match target $TargetHead"
}

$webDir = Join-Path $Repo "apps\web"
$dist = Join-Path $webDir "dist"
$newDist = Join-Path $webDir "dist.new-$TargetHead"
$extractDir = Join-Path $webDir "dist.extract-$TargetHead"
$backup = Join-Path $webDir ("dist.prev-kr-$TargetHead-" + (Get-Date -Format "yyyyMMddHHmmss"))

if (Test-Path -LiteralPath $newDist) {
  Remove-Item -LiteralPath $newDist -Recurse -Force
}
if (Test-Path -LiteralPath $extractDir) {
  Remove-Item -LiteralPath $extractDir -Recurse -Force
}

$null = New-Item -ItemType Directory -Force -Path $extractDir
tar -xzf $ArchivePath -C $extractDir
$extractedDist = Join-Path $extractDir "dist"
if (-not (Test-Path -LiteralPath $extractedDist)) {
  throw "Archive did not create dist directory"
}
Move-Item -LiteralPath $extractedDist -Destination $newDist
Remove-Item -LiteralPath $extractDir -Recurse -Force

$oldAssets = Join-Path $dist "assets"
$newAssets = Join-Path $newDist "assets"
if ((Test-Path -LiteralPath $oldAssets) -and (Test-Path -LiteralPath $newAssets)) {
  Copy-Item -Path (Join-Path $oldAssets "*") -Destination $newAssets -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $dist) {
  Move-Item -LiteralPath $dist -Destination $backup
}
Move-Item -LiteralPath $newDist -Destination $dist

$found = Select-String -Path (Join-Path $dist "assets\index-*.js") -Pattern $TargetHead -Quiet
if (-not $found) {
  throw "Active KR dist does not contain version marker $TargetHead"
}

Stop-ScheduledTask -TaskName $BackendTaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Stop-BrpBackendListener -Port $BackendPort
Start-ScheduledTask -TaskName $BackendTaskName

$backendTask = Get-ScheduledTask -TaskName $BackendTaskName
$health = Wait-BrpBackendHealth -Uri "http://127.0.0.1:$BackendPort/health"

Start-ScheduledTask -TaskName $NginxTaskName
Start-Sleep -Seconds 2
$nginxTask = Get-ScheduledTask -TaskName $NginxTaskName

Write-Output "KR_HEAD=$after"
Write-Output "KR_DIST=ok"
Write-Output "KR_BACKEND_TASK=$($backendTask.State)"
Write-Output "KR_BACKEND_HEALTH=$($health.status)"
Write-Output "KR_NGINX_TASK=$($nginxTask.State)"
