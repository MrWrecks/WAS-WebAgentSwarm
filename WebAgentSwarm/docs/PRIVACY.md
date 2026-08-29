# Privacy and security

## Repository contents

This repository is designed to be publishable without machine-specific configuration.
Do not commit browser profiles, cookies, access tokens, API keys, account identifiers,
local absolute paths, full private conversation exports, or generated runtime state.

## Runtime data

The default durable store contains stable conversation identifiers and bind-time URL/title,
small task records, and user-defined shared status values. It does not persist full Web
conversation transcripts by default.

## Network boundary

The bridge binds to `127.0.0.1` by default. The extension options page accepts loopback
WebSocket URLs. If you intentionally expose the bridge beyond loopback, add authentication
and a threat model first.

## Browser automation boundary

The extension sends ordinary messages through the existing logged-in Web UI. It does not
attempt to bypass verification challenges, access controls, or platform limits.

## Publishing checklist

- review all changes;
- verify there are no secrets or machine-specific paths;
- remove runtime state and dependency directories;
- confirm examples use placeholders;
- review browser permissions in `manifest.json`.
