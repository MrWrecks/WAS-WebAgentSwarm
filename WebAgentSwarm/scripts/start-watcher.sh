#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" -m web_agent_swarm.watcher --interval 15 --max-silence 300
