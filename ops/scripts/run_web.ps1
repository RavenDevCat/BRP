param(
    [string]$HostAddress = "127.0.0.1",
    [string]$Port = "5173"
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$webDir = Join-Path $repoRoot "apps\web"

if (-not (Test-Path (Join-Path $webDir "package.json"))) {
    throw "BRP web package not found: $webDir"
}

function Resolve-NpmCommand {
    if ($env:NPM_CMD) {
        return $env:NPM_CMD
    }

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        return "npm"
    }

    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetPackages) {
        $candidate = Get-ChildItem -Path (Join-Path $wingetPackages "OpenJS.NodeJS.LTS_*") -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                Get-ChildItem -Path (Join-Path $_.FullName "node-v*-win-x64\npm.cmd") -File -ErrorAction SilentlyContinue
            } |
            Sort-Object FullName -Descending |
            Select-Object -First 1

        if ($candidate) {
            $env:PATH = "$($candidate.DirectoryName);$env:PATH"
            return $candidate.FullName
        }
    }

    return "npm"
}

Push-Location $webDir
try {
    $env:VITE_API_BASE_URL = if ($env:VITE_API_BASE_URL) { $env:VITE_API_BASE_URL } else { "/api" }
    $npmCommand = Resolve-NpmCommand
    & $npmCommand run dev -- --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
