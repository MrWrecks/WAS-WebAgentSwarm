#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CODEXPRO="$ROOT/.runtime/codexpro"
if [ ! -f "$CODEXPRO/dist/http.js" ] || [ ! -f "$CODEXPRO/dist/stdio.js" ]; then
  echo "CodexPro is not prepared. Run: $ROOT/scripts/setup-codexpro.sh" >&2
  exit 2
fi

has_transcript_option=0
for arg in "$@"; do
  case "$arg" in
    --bash-transcript|--full-bash-transcript) has_transcript_option=1 ;;
  esac
done

if [ "$has_transcript_option" -eq 0 ]; then
  transcript="${WAS_CODEXPRO_BASH_TRANSCRIPT:-full}"
  case "$transcript" in
    compact|full) ;;
    *)
      echo "WAS_CODEXPRO_BASH_TRANSCRIPT must be compact or full." >&2
      exit 2
      ;;
  esac
  set -- "$@" --bash-transcript "$transcript"
fi

exec node "$CODEXPRO/scripts/codexpro.mjs" "$@"
