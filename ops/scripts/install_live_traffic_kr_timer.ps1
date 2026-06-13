param(
    [string]$TaskName = "BRP-KR-Weekly-Traffic-Profile",
    [string]$At = "08:00",
    [ValidateSet("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")]
    [string]$DayOfWeek = "Sunday"
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunnerPath = Join-Path $RootDir "ops\scripts\run_live_traffic_kr_weekly_profile.ps1"
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Missing runner script: $RunnerPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`"" `
    -WorkingDirectory $RootDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At ([DateTime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture))
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable:$false

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Host "Installed $TaskName"
Write-Host "Schedule: weekly $DayOfWeek at $At server local time"
Write-Host "Runner: $RunnerPath"
