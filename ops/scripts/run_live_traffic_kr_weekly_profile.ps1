param(
    [string]$WeekStart = "",
    [switch]$DryRun,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $RootDir "state\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $LogDir "kr-weekly-traffic-profile-$stamp.log"

Start-Transcript -Path $logPath -Append | Out-Null
try {
    Set-Location $RootDir
    Write-Host "BRP KR weekly traffic profile refresh"
    Write-Host "root=$RootDir"
    Write-Host "log=$logPath"
    Write-Host "period=all"
    if ($WeekStart) {
        Write-Host "week_start=$WeekStart"
    } else {
        Write-Host "week_start=next Monday from server local date"
    }

    $profileArgs = @("-Period", "all")
    if ($WeekStart) {
        $profileArgs += @("-WeekStart", $WeekStart)
    }
    if ($DryRun) {
        $profileArgs += "-DryRun"
    }
    if ($ExtraArgs) {
        $profileArgs += @("-ExtraArgs")
        $profileArgs += $ExtraArgs
    }

    & (Join-Path $PSScriptRoot "run_live_traffic_kr_profile.ps1") @profileArgs
    if ($LASTEXITCODE -ne 0) {
        throw "KR weekly traffic profile refresh failed with exit code $LASTEXITCODE"
    }
} finally {
    Stop-Transcript | Out-Null
}
