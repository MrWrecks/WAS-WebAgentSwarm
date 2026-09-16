#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ARCHIVE="$ROOT/integrations/codexpro/codexpro-0.30.0-src.tar.gz"
SUMS="$ROOT/integrations/codexpro/SHA256SUMS"
MANIFEST="$ROOT/integrations/codexpro/UPSTREAM_TREE.sha256"
METADATA="$ROOT/integrations/codexpro/VENDOR_METADATA.json"
RUNTIME="$ROOT/.runtime"
TARGET="$RUNTIME/codexpro"

mkdir -p "$RUNTIME"
python3 - "$ARCHIVE" "$SUMS" "$MANIFEST" "$METADATA" "$RUNTIME" <<'PY'
import hashlib, json, pathlib, shutil, sys, tarfile
archive = pathlib.Path(sys.argv[1])
sums = pathlib.Path(sys.argv[2])
manifest_path = pathlib.Path(sys.argv[3])
metadata_path = pathlib.Path(sys.argv[4])
runtime = pathlib.Path(sys.argv[5])
expected = sums.read_text(encoding='utf-8').split()[0]
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f'CodexPro archive checksum mismatch: expected {expected}, got {actual}')
metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
if metadata.get('archive_sha256') != actual:
    raise SystemExit('CodexPro VENDOR_METADATA archive SHA-256 does not match archive')
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

manifest = {}
for line in manifest_path.read_text(encoding='utf-8').splitlines():
    if not line.strip():
        continue
    digest, path = line.split(None, 1)
    manifest[path.strip()] = digest.strip()
actual_files = {
    str(path.relative_to(target)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in target.rglob('*')
    if path.is_file()
}
missing = sorted(set(manifest) - set(actual_files))
extra = sorted(set(actual_files) - set(manifest))
changed = sorted(path for path in set(manifest) & set(actual_files) if manifest[path] != actual_files[path])
if missing or extra or changed:
    raise SystemExit(
        'CodexPro upstream tree mismatch after extraction: '
        f'missing={missing[:8]} extra={extra[:8]} changed={changed[:8]}'
    )
if int(metadata.get('tracked_file_count') or 0) != len(manifest):
    raise SystemExit('CodexPro tracked_file_count metadata mismatch')
manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
if metadata.get('upstream_tree_manifest_sha256') != manifest_digest:
    raise SystemExit('CodexPro upstream manifest SHA-256 metadata mismatch')
print(f'CodexPro byte-exact upstream tree verified: {len(manifest)} tracked files')
print(f'CodexPro source extracted to {target}')
PY
cd "$TARGET"
npm ci
npm run build
