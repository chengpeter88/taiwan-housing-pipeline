# Dot-source from PowerShell: . .\scripts\activate.ps1
$projectDirectory = Split-Path -Parent $PSScriptRoot
$toolsDirectory = Join-Path (Split-Path -Parent $projectDirectory) 'tools'
$gcloudBin = Join-Path $toolsDirectory 'google-cloud-sdk/bin'
if (Test-Path -LiteralPath $gcloudBin) {
    $env:Path = "$gcloudBin;$env:Path"
}
$env:UV_CACHE_DIR = Join-Path (Split-Path -Parent $projectDirectory) '.uv-cache'
. (Join-Path $projectDirectory '.venv/Scripts/Activate.ps1')
