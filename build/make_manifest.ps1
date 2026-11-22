$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content (Join-Path $root 'config.py') -Raw
if ($config -notmatch '(?m)^APP_VERSION\s*=\s*"([0-9.]+)"') {
    throw 'APP_VERSION was not found in config.py'
}
$version = $Matches[1]
$fileName = "NOVA_Setup_$version.exe"
$installerPath = Join-Path $root "release\$fileName"
if (-not (Test-Path $installerPath)) {
    throw "Installer not found: $installerPath. Compile installer\NOVA.iss first."
}
$sha256 = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$repo = $env:NOVA_UPDATE_REPO
if (-not $repo) {
    # For local builds, try to infer OWNER/REPO from the configured git origin.
    $remote = (& git -C $root remote get-url origin 2>$null | Select-Object -First 1)
    if ($remote -match 'github\.com[:/](?<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$') {
        $repo = $Matches['repo']
    }
}
$installerUrl = ''
if ($repo -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    $installerUrl = "https://github.com/$repo/releases/download/v$version/$fileName"
}
$manifest = [ordered]@{
    version = $version
    installer_url = $installerUrl
    sha256 = $sha256
    notes = "NOVA $version: игровые профили, расширенная статистика FPS, FPS History, Game Boost, Network Monitor и автообновление."
}
$out = Join-Path $root 'release\latest.json'
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $out -Encoding utf8
Write-Host "Update manifest created: $out"
if (-not $installerUrl) {
    Write-Warning 'NOVA_UPDATE_REPO is not set. latest.json will need its installer_url filled in before publication.'
}
