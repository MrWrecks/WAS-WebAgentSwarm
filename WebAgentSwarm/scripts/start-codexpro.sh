#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CODEXPRO="$ROOT/.runtime/codexpro"
if [ ! -f "$CODEXPRO/dist/http.js" ] || [ ! -f "$CODEXPRO/dist/stdio.js" ]; then
  echo "CodexPro is not prepared. Run: $ROOT/scripts/setup-codexpro.sh" >&2
  exit 2
fi
exec node "$CODEXPRO/scripts/codexpro.mjs" "$@"
