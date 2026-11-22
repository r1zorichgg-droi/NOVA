$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $root 'config.py'
$issVersionPath = Join-Path $root 'installer\version.iss'
$config = Get-Content $configPath -Raw
if ($config -notmatch '(?m)^APP_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){1,3})"') {
    throw 'APP_VERSION was not found or has an invalid format in config.py'
}
$version = $Matches[1]
Set-Content -Path $issVersionPath -Value ('#define MyAppVersion "{0}"' -f $version) -Encoding ascii
Write-Host "Inno Setup version synchronized: $version"
