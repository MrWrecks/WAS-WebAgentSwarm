# End-to-end installation

## Runtime placement and workspace paths

Use the native filesystem of the runtime that owns the workspace. WAS does not require or
recommend a UNC workspace layer.

| Workspace lives in | Run WAS/CodexPro in | Example workspace root |
| --- | --- | --- |
| Windows | Windows | `<native-windows-workspace>` |
| Linux | Linux | `$HOME/<workspace>` |
| macOS | macOS | `$HOME/<workspace>` |
| WSL2 | that WSL distribution | `$HOME/<workspace>` |

If ChatGPT is open in a Windows browser while the runtime is in WSL2, keep source operations
in WSL and use localhost HTTP/WebSocket for the bridge. `WEB_SWARM_BRIDGE_URL` configures that
transport; it does not require filesystem sharing. Avoid setting `CODEXPRO_ROOT` to a Windows UNC alias of a WSL directory unless you
explicitly want Windows-hosted file semantics.

The repository contains no user-specific home directory, WSL distribution name, UNC root,
Tailscale hostname, browser profile path, or authentication material.

## Requirements

- Edge or Chrome
- Node.js 20+
- Python 3.10+
- A logged-in ChatGPT Web session
- Optional: an MCP-capable host. The complete pinned upstream CodexPro MCP source is vendored with WAS.

## 1. Start the bridge

```bash
cd bridge
npm install
npm start
```

Expected endpoint: `http://127.0.0.1:8788/health`.

## 2. Install the extension

Follow `browser-extension/README.md`. Once loaded, bridge health should report at least
one extension client.

WAS always reuses the browser profile in which this extension is currently connected.
New Agent tabs are created by the extension with `chrome.tabs.create()`, so they stay inside
that same Edge/Chrome profile and inherit its existing ChatGPT login state.

WAS does **not** launch another browser profile and does not use `--profile-directory`.
If more than one browser profile connects to the same bridge, the bridge fails closed rather
than guessing which profile is current. Keep only the intended profile connected, or use a
separate bridge port per profile.

## 3. Install the Python runtime

```bash
python -m venv .venv
python -m pip install -e ".[mcp]"
```

Activate the environment using the normal command for your operating system.

## 4. Smoke test

```bash
web-agent-swarm health
web-agent-swarm doctor
web-agent-swarm tabs
```

## 5. Connect the two MCP interfaces

Use `examples/mcp-config.example.json` for local MCP hosts, or the README ChatGPT Web + Tailscale Funnel guide for remote HTTPS.

Web swarm control:

```text
python -m web_agent_swarm.mcp_server
```

Complete upstream CodexPro development tools:

```bash
./scripts/setup-codexpro.sh
./scripts/start-codexpro.sh start --root <workspace-root>
```

`setup-codexpro` verifies the archive and every one of the pinned Git-tracked upstream files before building. `start-codexpro` then executes the extracted upstream `scripts/codexpro.mjs`; there is no WAS-local CodexPro reimplementation in the call path.

The WAS start wrappers default to `--bash-transcript full` so CodexPro execution shows
the command, working directory, exit status, duration, and stdout/stderr. This is a
visible execution transcript, not a dump of private model reasoning. Set
`WAS_CODEXPRO_BASH_TRANSCRIPT=compact` to restore compact tool cards, or pass an
explicit upstream transcript flag.

## 6. Create shared tasks

```bash
web-agent-swarm task-create "Implement feature X" --owner implementer
web-agent-swarm task-create "Verify feature X" --owner tester
```

## 7. Plan a swarm

```bash
web-agent-swarm plan
web-agent-swarm plan --workers 4
```

Do not start until the proposed worker count and role responsibilities are accepted.

## 8. Start the confirmed workers

```bash
web-agent-swarm start --roles implementer tester reviewer researcher
```

By default this also starts the event-aware watcher. Use `--no-watch` only when another
process already owns wakeup scheduling.

For manual watcher operation:

```bash
web-agent-swarm watch --interval 15 --max-silence 300
```

The watcher only wakes the main agent; it does not perform autonomous task selection.

## 10. Complete vendored CodexPro

WAS uses the pinned upstream CodexPro implementation itself for workspace inspection, search/read, write/edit/patch, verification commands, background jobs, change review, handoff, context management, HTTP/STDIO transport, and tunnel support. The source is compressed only to stay below common browser Git-upload file-count limits. At runtime it is expanded intact to `.runtime/codexpro/`.

See `integrations/codexpro/WAS_VENDOR.md`, `integrations/codexpro/VENDOR_METADATA.json`, and `integrations/codexpro/UPSTREAM_TREE.sha256`. After setup, the original upstream README/README_ZH/SECURITY files are available under `.runtime/codexpro/`.

## Optional service installation

Linux/systemd user: `scripts/install-service.sh`. Windows Scheduled Tasks: `scripts\\install-service.ps1`. Both use current-user paths and do not require UNC or administrator-specific roots.

## ChatGPT Web / remote MCP

WAS stores the complete vendored CodexPro source in the single checked archive `integrations/codexpro/codexpro-0.30.0-src.tar.gz`; setup extracts it locally into `.runtime/codexpro/`.

1. Build it with `scripts/setup-codexpro.sh` or `scripts/setup-codexpro.ps1`.
2. Start its built-in Tailscale mode with a narrow `--root` and the device Funnel hostname.
3. In ChatGPT Web enable Developer mode.
4. Create/add a custom app/plugin using the **Server URL** printed by CodexPro.
5. Set **Authentication: No Authentication / None**. Do not choose OAuth for this
   documented developer configuration.
6. Run **Scan Tools**, then create/save the app.

CodexPro may include its own private `codexpro_token` in the Server URL. Keep the full URL
intact; ChatGPT still uses **No Authentication / None** because CodexPro's URL token is
handled by the server URL rather than ChatGPT's OAuth flow.

The full, maintained walkthrough and troubleshooting sequence is in the root `README.md`
under **Install and connect from ChatGPT Web**.
