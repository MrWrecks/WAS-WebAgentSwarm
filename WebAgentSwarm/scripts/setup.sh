#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

cd "$ROOT/bridge"
npm install

cd "$ROOT"
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[mcp]"

printf '%s\n' "Setup complete. Load browser-extension as an unpacked extension."
