[README.md](https://github.com/user-attachments/files/31596135/README.md)
# Web Agent Swarm

A local-first, browser-hosted multi-agent orchestration toolkit for ChatGPT Web.

It provides a small browser extension, a localhost bridge, a durable shared task/status
store, an optional MCP server, and an event-aware watcher that can wake a main Web Agent
when worker state changes.

## What this project does

- Open or reuse multiple ChatGPT Web conversations.
- Assign a main orchestrator and a configurable set of worker roles.
- Inspect bounded visible UI state: idle/generating and recent visible messages.
- Send an exact message to an exact ChatGPT tab or stable conversation binding.
- Keep a small durable task board and shared status store outside chat history.
- Recommend a worker count/role subset before opening browser tabs.
- Wake the main agent when worker/task state changes instead of waiting for a coarse timer.
- Start that watcher automatically when a confirmed swarm is launched.
- Optionally expose the same controls as MCP tools.
- Integrate cleanly with a separate CodexPro-style local development MCP.

## Safety and privacy model

- The bridge listens on loopback by default.
- The extension is limited to `https://chatgpt.com/*`.
- No login cookies, passwords, browser profiles, full transcripts, or machine-specific paths
  are stored in this repository.
- The runtime stores only small coordination metadata unless you explicitly put more data
  into the shared status store.
- The extension does not bypass verification, anti-bot checks, rate limits, or access controls.
- Web UI state is an availability signal, not proof that an external task is actually complete.

## Architecture

```text
ChatGPT Web conversations
        ^
        | browser extension
        v
localhost bridge (Node.js)
        ^
        | structured HTTP/WebSocket
        v
Python swarm runtime
   |        |          |
   |        |          +-- event-aware watcher
   |        +------------- durable task/status store
   +---------------------- optional MCP server
                                ^
                                |
                        main / worker agents

Optional second MCP:
CodexPro-style local development tools
(workspace/search/read/write/edit/background/handoff/context)
```

## Quick start

1. Install Node.js 20+ and Python 3.9+.
2. In `bridge/`, run `npm install`, then `npm start`.
3. Load `browser-extension/` as an unpacked extension in Edge or Chrome.
4. Open the extension Options page and confirm the WebSocket URL points to the bridge.
5. Create a Python environment and install:
   `pip install -e ".[mcp]"`
6. Run:
   `web-agent-swarm health`
7. Add the MCP server to your MCP-capable host using `examples/mcp-config.example.json`.
8. Tell the main agent something natural like “open a swarm” or “start a few web agents”.
   It should call the read-only plan tool first, show a proposed worker count/role split,
   then start only after confirmation.

See `docs/INSTALL.md` and `docs/ARCHITECTURE.md`.

## Dynamic swarm startup

The startup flow is intentionally two-stage:

```text
natural-language intent
        |
        v
web_agent_swarm_plan
        |
        v
human confirms or revises roles/count
        |
        v
web_agent_swarm_start
```

A user can also explicitly request a direct start with a specific role subset.

## CodexPro integration

This repository does not copy or redistribute a CodexPro server implementation or any
private connector configuration. Instead, `integrations/codexpro/` contains a clean
capability profile and agent-facing integration guidance. If a compatible CodexPro MCP
is already connected to the same agent host, workers can use it for local development
while this project handles browser swarm coordination.

## Development

```bash
python -m unittest discover -s tests -v
```

The browser bridge is intentionally small and dependency-light.

## License

MIT. See `LICENSE`.
