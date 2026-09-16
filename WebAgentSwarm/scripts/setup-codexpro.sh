#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ARCHIVE="$ROOT/integrations/codexpro/codexpro-0.30.0-src.tar.gz"
SUMS="$ROOT/integrations/codexpro/SHA256SUMS"
RUNTIME="$ROOT/.runtime"
TARGET="$RUNTIME/codexpro"

mkdir -p "$RUNTIME"
python3 - "$ARCHIVE" "$SUMS" "$RUNTIME" <<'PY'
import hashlib, pathlib, shutil, sys, tarfile
archive = pathlib.Path(sys.argv[1])
sums = pathlib.Path(sys.argv[2])
runtime = pathlib.Path(sys.argv[3])
expected = sums.read_text(encoding='utf-8').split()[0]
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f'CodexPro archive checksum mismatch: expected {expected}, got {actual}')
target = runtime / 'codexpro'
if target.exists():
    shutil.rmtree(target)
with tarfile.open(archive, 'r:gz') as tf:
    members = tf.getmembers()
    for member in members:
        name = pathlib.PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'codexpro':
            raise SystemExit(f'unsafe archive member: {member.name}')
    tf.extractall(runtime)
print(f'CodexPro source extracted to {target}')
PY
cd "$TARGET"
npm ci
npm run build
