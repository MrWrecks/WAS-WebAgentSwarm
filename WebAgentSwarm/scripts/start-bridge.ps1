$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root "bridge")
try {
    npm start
} finally {
    Pop-Location
}
