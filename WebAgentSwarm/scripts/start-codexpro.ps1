$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CodexPro = Join-Path $Root ".runtime\codexpro"
$Launcher = Join-Path $CodexPro "scripts\codexpro.mjs"
$HttpBuild = Join-Path $CodexPro "dist\http.js"
$StdioBuild = Join-Path $CodexPro "dist\stdio.js"
if (-not (Test-Path $HttpBuild) -or -not (Test-Path $StdioBuild)) {
    throw "CodexPro is not prepared. Run scripts\setup-codexpro.ps1 first."
}
& node $Launcher @args
exit $LASTEXITCODE
