@echo off
setlocal
pushd "%~dp0.."
if defined WAS_DISTRO_NAME (
  wsl.exe -d "%WAS_DISTRO_NAME%" -- bash -lc "export PYTHONPATH=src && exec python3 -m web_agent_swarm.watcher --interval 300 --max-silence 300"
) else (
  wsl.exe -- bash -lc "export PYTHONPATH=src && exec python3 -m web_agent_swarm.watcher --interval 300 --max-silence 300"
)
popd
endlocal
