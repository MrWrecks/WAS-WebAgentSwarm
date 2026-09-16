# Security policy

## Supported scope

The default deployment is local-only:

- bridge HTTP/WebSocket listener on loopback;
- browser extension restricted to ChatGPT Web;
- no remote control endpoint or authentication layer.

## Reporting

For a public release, configure the repository's normal private security-reporting channel.

## Hard rules

- Do not bind the bridge to a public interface without authentication and a threat model.
- Do not commit credentials, cookies, browser profiles, or private conversation dumps.
- Do not add code that bypasses verification challenges, platform access controls, or rate limits.
- Treat browser DOM selectors as untrusted compatibility points that may change.
