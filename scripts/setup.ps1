$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$VersionOk = python -c "import sys; print(int(sys.version_info >= (3, 10)))"
if ($VersionOk.Trim() -ne "1") {
    throw "Python 3.10+ is required."
}

Write-Host "Installing bridge dependencies..."
Push-Location (Join-Path $Root "bridge")
npm install
Pop-Location

Write-Host "Creating Python virtual environment..."
python -m venv (Join-Path $Root ".venv")

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -e "$Root[mcp]"

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next: load browser-extension as an unpacked extension, then run scripts\start-bridge.ps1."
