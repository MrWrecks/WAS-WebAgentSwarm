#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
STATE="${WEB_SWARM_STATE_DIR:-$HOME/.web-agent-swarm}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR" "$STATE"
cat > "$UNIT_DIR/web-agent-swarm-bridge.service" <<EOF
[Unit]
Description=Web Agent Swarm browser bridge
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT/bridge
ExecStart=$(command -v npm) start
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
cat > "$UNIT_DIR/web-agent-swarm-watcher.service" <<EOF
[Unit]
Description=Web Agent Swarm watcher
After=web-agent-swarm-bridge.service

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m web_agent_swarm.watcher --interval 300 --max-silence 300
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now web-agent-swarm-bridge.service web-agent-swarm-watcher.service
printf '%s\n' 'Installed user services. Run: systemctl --user status web-agent-swarm-bridge web-agent-swarm-watcher'
