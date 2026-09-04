@echo off
wsl.exe -d WSL_Rocky9.7_backend -- bash -lc "cd /mnt/c/Users/Admin/Desktop/WebAgentSwarm && export PYTHONPATH=src && exec python3 -m web_agent_swarm.watcher --interval 300 --max-silence 300 >> /home/maxwell/.web-agent-swarm/watcher.log 2>&1"
