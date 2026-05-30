function Require-Env($Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $Value) {
        throw "Set $Name before sourcing export_osrm_cloudflare_env.ps1."
    }
    return $Value
}

$env:OSRM_BASE_URL_CHINA_SHANGHAI = Require-Env "BRP_OSRM_SHANGHAI_URL"
$env:OSRM_BASE_URL_CHINA_BEIJING = Require-Env "BRP_OSRM_BEIJING_URL"
$env:OSRM_BASE_URL_CHINA_SUZHOU = Require-Env "BRP_OSRM_SUZHOU_URL"
$env:OSRM_BASE_URL_CHINA_XIAN = Require-Env "BRP_OSRM_XIAN_URL"
$env:OSRM_BASE_URL_SOUTH_KOREA = Require-Env "BRP_OSRM_SOUTH_KOREA_URL"
$env:OSRM_BASE_URL_SOUTH_KOREA_SEOUL = $env:OSRM_BASE_URL_SOUTH_KOREA
