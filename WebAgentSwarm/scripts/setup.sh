#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required.")'

cd "$ROOT/bridge"
npm install

cd "$ROOT"
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[mcp]"

printf '%s\n' "Setup complete. Load browser-extension as an unpacked extension."
