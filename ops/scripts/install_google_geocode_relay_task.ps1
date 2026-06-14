param(
    [string]$TaskName = "BRP-Google-Geocode-Relay",
    [string]$HostAddress = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunnerPath = Join-Path $RootDir "ops\scripts\run_google_geocode_relay.ps1"
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Missing runner script: $RunnerPath"
}

$runnerArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$RunnerPath`"")
if ($HostAddress) {
    $runnerArgs += @("-HostAddress", "`"$HostAddress`"")
}
if ($Port -gt 0) {
    $runnerArgs += @("-Port", [string]$Port)
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($runnerArgs -join " ") `
    -WorkingDirectory $RootDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable:$true

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Host "Installed $TaskName"
Write-Host "Trigger: at startup"
Write-Host "Runner: $RunnerPath"
if ($HostAddress) {
    Write-Host "Host override: $HostAddress"
}
if ($Port -gt 0) {
    Write-Host "Port override: $Port"
}
