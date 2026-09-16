$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$BridgeScript = Join-Path $PSScriptRoot "start-bridge.ps1"
$WatcherScript = Join-Path $PSScriptRoot "start-watcher.ps1"
if (-not (Test-Path $Python)) { throw "Run scripts\setup.ps1 first." }
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "WebAgentSwarm-Bridge" -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`"" -f $BridgeScript)) -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Register-ScheduledTask -TaskName "WebAgentSwarm-Watcher" -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`"" -f $WatcherScript)) -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "WebAgentSwarm-Bridge"
Start-ScheduledTask -TaskName "WebAgentSwarm-Watcher"
Write-Host "Installed current-user Scheduled Tasks: WebAgentSwarm-Bridge, WebAgentSwarm-Watcher"
