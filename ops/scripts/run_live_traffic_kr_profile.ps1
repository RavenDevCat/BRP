param(
    [ValidateSet("am_peak", "pm_peak", "off_peak", "both")]
    [string]$Period = "both",
    [string]$WeekStart = "",
    [switch]$DryRun,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "import_local_env.ps1") -RootDir $RootDir

function Set-DefaultEnv {
    param([string]$Name, [string]$Value)
    $current = (Get-Item -Path "Env:$Name" -ErrorAction SilentlyContinue).Value
    if (-not $current -and $Value) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

$defaultCondaPython = Join-Path $env:LOCALAPPDATA "anaconda3\envs\ortools_env\python.exe"
if (-not $env:BACKEND_PYTHON -and (Test-Path -LiteralPath $defaultCondaPython)) {
    $env:BACKEND_PYTHON = $defaultCondaPython
}
if (-not $env:BACKEND_PYTHON) {
    $env:BACKEND_PYTHON = "python"
}

Set-DefaultEnv "BRP_BACKEND_JOBS_DIR" (Join-Path $RootDir "state\jobs")
Set-DefaultEnv "BRP_SIDE_TOOLS_DIR" (Join-Path $RootDir "state\side_tools")
Set-DefaultEnv "BRP_LIVE_TRAFFIC_SAMPLE_DIR" (Join-Path $RootDir "state\traffic_samples")
Set-DefaultEnv "BRP_LIVE_TRAFFIC_BASELINE_DIR" (Join-Path $RootDir "state\traffic_baselines")
Set-DefaultEnv "BRP_GOOGLE_ROUTES_USAGE_PATH" (Join-Path $RootDir "state\google_routes_usage.json")
Set-DefaultEnv "BRP_LIVE_TRAFFIC_TIMEZONE" "Asia/Seoul"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_PROVIDER" "google_routes"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_SOURCE" "baseline_json"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_PROVIDER" "google_routes"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_MARKET" "KR"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_CITY" "Seoul"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_AM_TARGET_ARRIVAL_LOCAL_TIME" "08:00"
Set-DefaultEnv "BRP_LIVE_TRAFFIC_KR_PM_DEPARTURE_LOCAL_TIME" "15:40"
Set-DefaultEnv "BRP_GOOGLE_ROUTES_MONTHLY_SAFETY_CAP" "4000"
Set-DefaultEnv "BRP_GOOGLE_ROUTES_DAILY_CAP" "250"
Set-DefaultEnv "BRP_GOOGLE_ROUTES_MAX_CALLS_PER_REFRESH" "250"

foreach ($dir in @($env:BRP_BACKEND_JOBS_DIR, $env:BRP_SIDE_TOOLS_DIR, $env:BRP_LIVE_TRAFFIC_SAMPLE_DIR, $env:BRP_LIVE_TRAFFIC_BASELINE_DIR)) {
    if ($dir) {
        New-Item -ItemType Directory -Force $dir | Out-Null
    }
}

function Get-NextMonday {
    $today = (Get-Date).Date
    $daysUntilMonday = ([int][System.DayOfWeek]::Monday - [int]$today.DayOfWeek + 7) % 7
    return $today.AddDays($daysUntilMonday)
}

function Resolve-WeekStart {
    param([string]$Value)
    if ($Value) {
        return [DateTime]::ParseExact($Value, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
    }
    if ($env:BRP_LIVE_TRAFFIC_KR_PROFILE_WEEK_START) {
        return [DateTime]::ParseExact($env:BRP_LIVE_TRAFFIC_KR_PROFILE_WEEK_START, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
    }
    return Get-NextMonday
}

function Build-SamplerArgs {
    param([string]$SamplePeriod, [string]$SampleDate)

    $source = if ($env:BRP_LIVE_TRAFFIC_KR_SOURCE) { $env:BRP_LIVE_TRAFFIC_KR_SOURCE } else { "baseline_json" }
    $market = if ($env:BRP_LIVE_TRAFFIC_KR_MARKET) { $env:BRP_LIVE_TRAFFIC_KR_MARKET } else { "KR" }
    $city = if ($env:BRP_LIVE_TRAFFIC_KR_CITY) { $env:BRP_LIVE_TRAFFIC_KR_CITY } else { "Seoul" }
    $provider = if ($env:BRP_LIVE_TRAFFIC_KR_PROVIDER) { $env:BRP_LIVE_TRAFFIC_KR_PROVIDER } else { "google_routes" }
    $baselineDir = if ($env:BRP_LIVE_TRAFFIC_KR_BASELINE_DIR) { $env:BRP_LIVE_TRAFFIC_KR_BASELINE_DIR } else { $env:BRP_LIVE_TRAFFIC_BASELINE_DIR }
    $jobId = ""
    $runId = ""
    $baselinePath = ""
    $timingArgs = @()

    switch ($SamplePeriod) {
        "am_peak" {
            $jobId = $env:BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_JOB_ID
            $runId = $env:BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_RUN_ID
            $baselinePath = if ($env:BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH) { $env:BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH } else { $env:BRP_LIVE_TRAFFIC_TO_SCHOOL_BASELINE_PATH }
            $timingArgs = @("--target-arrival-local-time", $env:BRP_LIVE_TRAFFIC_KR_AM_TARGET_ARRIVAL_LOCAL_TIME)
        }
        "pm_peak" {
            $jobId = $env:BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_JOB_ID
            $runId = $env:BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_RUN_ID
            $baselinePath = if ($env:BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH) { $env:BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH } else { $env:BRP_LIVE_TRAFFIC_FROM_SCHOOL_BASELINE_PATH }
            $timingArgs = @("--departure-local-time", $env:BRP_LIVE_TRAFFIC_KR_PM_DEPARTURE_LOCAL_TIME)
        }
        "off_peak" {
            $jobId = $env:BRP_LIVE_TRAFFIC_KR_OFF_PEAK_JOB_ID
            $runId = $env:BRP_LIVE_TRAFFIC_KR_OFF_PEAK_RUN_ID
            $baselinePath = if ($env:BRP_LIVE_TRAFFIC_KR_OFF_PEAK_BASELINE_PATH) { $env:BRP_LIVE_TRAFFIC_KR_OFF_PEAK_BASELINE_PATH } else { $env:BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH }
        }
    }

    $args = @("live_traffic_sampler.py", "--source", $source, "--period", $SamplePeriod, "--provider", $provider, "--market", $market, "--city", $city, "--sample-date", $SampleDate)
    if ($baselineDir) {
        $args += @("--baseline-dir", $baselineDir)
    }
    if ($source -eq "baseline_json") {
        if (-not $baselinePath) {
            throw "Missing KR baseline path for $SamplePeriod. Set BRP_LIVE_TRAFFIC_KR_TO_SCHOOL_BASELINE_PATH or BRP_LIVE_TRAFFIC_KR_FROM_SCHOOL_BASELINE_PATH."
        }
        $args += @("--baseline-path", $baselinePath)
    } elseif ($source -eq "fleet_planner") {
        if (-not $runId) { throw "Missing KR fleet planner run id for $SamplePeriod." }
        $args += @("--run-id", $runId)
    } elseif ($source -eq "route_audit_job") {
        if (-not $jobId) { throw "Missing KR route audit job id for $SamplePeriod." }
        $args += @("--job-id", $jobId)
    } else {
        throw "Unsupported KR live traffic source: $source"
    }
    $args += $timingArgs
    if ($DryRun) {
        $args += "--dry-run"
    }
    if ($ExtraArgs) {
        $args += $ExtraArgs
    }
    return $args
}

$weekStartDate = Resolve-WeekStart -Value $WeekStart
$periods = if ($Period -eq "both") { @("am_peak", "pm_peak") } else { @($Period) }
Set-Location (Join-Path $RootDir "apps\backend")

for ($offset = 0; $offset -lt 5; $offset++) {
    $sampleDate = $weekStartDate.AddDays($offset).ToString("yyyy-MM-dd")
    foreach ($samplePeriod in $periods) {
        $samplerArgs = Build-SamplerArgs -SamplePeriod $samplePeriod -SampleDate $sampleDate
        Write-Host "Sampling KR $samplePeriod profile for $sampleDate"
        & $env:BACKEND_PYTHON @samplerArgs
        if ($LASTEXITCODE -ne 0) {
            throw "KR traffic profile sampler failed with exit code $LASTEXITCODE"
        }
    }
}
