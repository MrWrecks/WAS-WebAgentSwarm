$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Archive = Join-Path $Root "integrations\codexpro\codexpro-0.30.0-src.tar.gz"
$Sums = Join-Path $Root "integrations\codexpro\SHA256SUMS"
$Runtime = Join-Path $Root ".runtime"
$Target = Join-Path $Runtime "codexpro"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$Expected = ((Get-Content $Sums -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw "CodexPro archive checksum mismatch: expected $Expected, got $Actual" }
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
tar -xzf $Archive -C $Runtime
Push-Location $Target
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
