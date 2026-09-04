$ErrorActionPreference = "Continue"
$Distro = "WSL_Rocky9.7_backend"
$RuntimeDir = Join-Path $env:USERPROFILE "Desktop\WebAgentSwarm\.runtime"
$Log = Join-Path $RuntimeDir "autol-tick-loop.log"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

while ($true) {
    $started = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $Log -Value "[$started] tick-attempt"
    & wsl.exe -d $Distro -- bash -lc 'cd /home/maxwell/AutoL/autol-mcp && export PYTHONPATH=src && python3 -m autol.web_agent_watch --main main --interval 300 --max-silence 300 --once' *>> $Log
    $code = $LASTEXITCODE
    $finished = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $Log -Value "[$finished] tick-exit=$code"
    Start-Sleep -Seconds 300
}
