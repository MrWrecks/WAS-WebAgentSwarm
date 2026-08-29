# End-to-end installation

## Requirements

- Edge or Chrome
- Node.js 20+
- Python 3.9+
- A logged-in ChatGPT Web session
- Optional: an MCP-capable host and a separate CodexPro-style local development MCP

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

## 3. Install the Python runtime

```bash
python -m venv .venv
python -m pip install -e ".[mcp]"
```

Activate the environment using the normal command for your operating system.

## 4. Smoke test

```bash
web-agent-swarm health
web-agent-swarm tabs
```

## 5. Connect the MCP server

Use `examples/mcp-config.example.json`. The exact configuration UI or file varies by host.

The command is:

```text
python -m web_agent_swarm.mcp_server
```

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

## 10. Optional CodexPro-style connector

Connect your local development MCP separately. See `integrations/codexpro/README.md`.
The Web Agent Swarm server coordinates browser agents; the development MCP handles local
workspace operations.
