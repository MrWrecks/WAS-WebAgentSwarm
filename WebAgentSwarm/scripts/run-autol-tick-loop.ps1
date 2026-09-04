$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $RepoRoot ".runtime"
$Log = Join-Path $RuntimeDir "autol-tick-loop.log"
$AutoLRepo = $env:AUTOL_REPO_WSL
$Distro = $env:WAS_DISTRO_NAME

if ([string]::IsNullOrWhiteSpace($AutoLRepo)) {
    throw "Set AUTOL_REPO_WSL to the AutoL repository path as seen inside WSL."
}
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$QuotedRepo = $AutoLRepo.Replace("'", "'\"'\"'")
$Command = "cd '$QuotedRepo' && export PYTHONPATH=src && python3 -m autol.web_agent_watch --main main --interval 300 --max-silence 300 --once"

while ($true) {
    $started = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $Log -Value "[$started] tick-attempt"
    if ([string]::IsNullOrWhiteSpace($Distro)) {
        & wsl.exe -- bash -lc $Command *>> $Log
    } else {
        & wsl.exe -d $Distro -- bash -lc $Command *>> $Log
    }
    $code = $LASTEXITCODE
    $finished = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $Log -Value "[$finished] tick-exit=$code"
    Start-Sleep -Seconds 300
}
