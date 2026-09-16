# Vendored CodexPro

The complete Git-tracked CodexPro 0.30.0 source tree is stored as codexpro-0.30.0-src.tar.gz so WebAgentSwarm stays below common browser upload file-count limits.

- Upstream: https://github.com/rebel0789/codexpro
- Version: 0.30.0
- Commit: 587f7fd3a4644a847bba13aeb49336056052e1f6
- Archive SHA-256: ea32c7fe935b80a2fa408b23cf457c831ad94a4c2022d7aa4733362e49a2f264
- Contents: every Git-tracked upstream source, documentation, test, and package file.
- Excluded runtime artifacts: nested .git metadata, node_modules, generated dist output, local profiles, tokens, and machine-specific runtime state.

Run the WAS setup-codexpro script. It verifies the archive checksum, then verifies every extracted Git-tracked file against `UPSTREAM_TREE.sha256` before installing dependencies and building CodexPro locally. `VENDOR_METADATA.json` pins the upstream commit, version, archive digest, manifest digest, and tracked-file count. WAS does not reimplement this MCP: `start-codexpro` executes the extracted upstream `scripts/codexpro.mjs` directly.
