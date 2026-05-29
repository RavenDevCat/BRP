param(
    [string]$Server = "203.0.113.10",
    [string]$User = "deployuser",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

$forwards = @(
    "127.0.0.1:5002:127.0.0.1:5002",
    "127.0.0.1:5003:127.0.0.1:5003",
    "127.0.0.1:5004:127.0.0.1:5004",
    "127.0.0.1:5005:127.0.0.1:5005",
    "127.0.0.1:5006:127.0.0.1:5006"
)

$listeningPorts = foreach ($port in 5002, 5003, 5004, 5005, 5006) {
    $portOpen = (netstat -ano | Select-String "127\.0\.0\.1:$port\s+.*LISTENING")
    if ($portOpen) {
        $port
    }
}

if ($listeningPorts) {
    Write-Host "OSRM tunnel ports already listening: $($listeningPorts -join ', ')"
    return
}

$args = @(
    "-N",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=60"
)
foreach ($forward in $forwards) {
    $args += @("-L", $forward)
}
$args += "$User@$Server"

if ($Foreground) {
    Write-Host "Starting OSRM SSH tunnel in foreground. Press Ctrl+C to stop."
    & ssh @args
    return
}

$process = Start-Process -FilePath "ssh.exe" -ArgumentList $args -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2

$readyPorts = foreach ($port in 5002, 5003, 5004, 5005, 5006) {
    $portOpen = (netstat -ano | Select-String "127\.0\.0\.1:$port\s+.*LISTENING")
    if ($portOpen) {
        $port
    }
}

if ($readyPorts.Count -ne 5) {
    throw "OSRM tunnel did not open all expected ports. Open ports: $($readyPorts -join ', ')"
}

Write-Host "OSRM SSH tunnel started. PID: $($process.Id). Ports: $($readyPorts -join ', ')"
