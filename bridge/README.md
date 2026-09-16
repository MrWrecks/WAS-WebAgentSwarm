# Local bridge

The bridge is a small loopback-only Node.js service.

## Start

```bash
npm install
npm start
```

Default HTTP endpoint: `http://127.0.0.1:8788`

Default WebSocket endpoint: `ws://127.0.0.1:8788/ws`

Environment variables:

- `WEB_SWARM_BIND` — defaults to `127.0.0.1`
- `WEB_SWARM_PORT` — defaults to `8788`

The bridge accepts structured commands only; it never forwards arbitrary JavaScript to
the browser extension.

The bridge is intentionally profile-strict: exactly one browser-profile extension client may
be connected for browser operations. WAS sends `tabs/open/inspect/send` only to that connected
profile. If multiple profiles are connected, the bridge returns a conflict instead of guessing.
WAS never starts Edge/Chrome with `--profile-directory`.
