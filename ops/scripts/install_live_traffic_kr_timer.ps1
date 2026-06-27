param(
    [string]$TaskName = "BRP-KR-Weekly-Traffic-Profile"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed retired KR traffic coefficient task: $TaskName"
} else {
    Write-Host "Retired KR traffic coefficient task is already absent: $TaskName"
}

Write-Host "KR route timing now uses per-job Kakao Navi future directions in the final route gate."
Write-Host "Do not reinstall weekly KR coefficient sampling for normal production operation."
