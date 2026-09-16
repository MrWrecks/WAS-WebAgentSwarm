$ErrorActionPreference = "Stop"
$TaskName = "WebAgentSwarmWatcher"
$Script = Join-Path $PSScriptRoot "run-autol-tick-loop.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$t = Get-ScheduledTask -TaskName $TaskName
$i = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
  State = $t.State.ToString()
  LastRunTime = $i.LastRunTime
  LastTaskResult = $i.LastTaskResult
  ExecutionTimeLimit = $t.Settings.ExecutionTimeLimit
  RestartCount = $t.Settings.RestartCount
  RestartInterval = $t.Settings.RestartInterval
  Action = $t.Actions.Execute + " " + $t.Actions.Arguments
} | ConvertTo-Json -Compress
