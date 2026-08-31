# End-to-end installation

## Runtime placement and workspace paths

Use the native filesystem of the runtime that owns the workspace. WAS does not require or
recommend a UNC workspace layer.

| Workspace lives in | Run WAS/CodexPro Lite in | Example workspace root |
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
- Optional: an MCP-capable host. A clean-room CodexPro-compatible local development MCP is bundled.

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

## 5. Connect the two bundled MCP servers

Use `examples/mcp-config.example.json`. The exact configuration UI or file varies by host.

Web swarm control:

```text
python -m web_agent_swarm.mcp_server
```

Bundled CodexPro Lite local-development tools:

```text
python -m web_agent_swarm.codexpro_server
```

Set `CODEXPRO_ROOT` in the CodexPro Lite MCP environment to the repository/workspace that
the agents should be allowed to edit.

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

## 10. CodexPro Lite local development

The bundled server covers the core development workflow used by the swarm: workspace
inspection, search/read, write/edit/patch, allowlisted verification commands, background
jobs, change review, handoff, and context management.

See `integrations/codexpro/README.md` for the supported action surface and the differences
from any private/full CodexPro deployment.

## Optional service installation

Linux/systemd user: `scripts/install-service.sh`. Windows Scheduled Tasks: `scripts\\install-service.ps1`. Both use current-user paths and do not require UNC or administrator-specific roots.
