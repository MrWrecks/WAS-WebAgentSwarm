# Architecture

## Components

### Browser extension

Runs inside the browser and owns all DOM interaction. It can:

- enumerate tabs;
- open a new ChatGPT tab;
- inspect bounded visible user/assistant messages;
- detect whether the page is generating;
- insert and send a message.

It never receives arbitrary JavaScript from the bridge.

### Local bridge

A small Node.js HTTP/WebSocket server bound to loopback. It translates structured local
requests into extension messages and waits for explicit acknowledgements.

### Python swarm runtime

Owns stable conversation bindings, worker-role planning, exact named-agent messaging,
shared task/status state, and combined snapshots.

### Watcher

The watcher is deterministic. It does not decide what task should be done. It hashes
worker UI state plus durable shared state and wakes the main agent when something changes
or a fallback silence interval expires.

### Optional MCP server

The same Python runtime can be exposed as MCP tools with FastMCP.

## Two-stage startup

```text
user asks for a swarm
       |
       v
web_agent_swarm_plan      read-only
       |
       v
proposal: count + roles
       |
       v
user confirms/revises
       |
       v
web_agent_swarm_start     browser mutation
       |
       +-- main agent
       +-- selected workers only
       +-- stable bindings
```

This keeps user agency while allowing natural-language trigger phrases rather than one
hard-coded sentence.

## Stable identity

Browser tab IDs are ephemeral. The runtime binds agents to the conversation ID extracted
from the ChatGPT conversation URL. If a browser tab is recreated, reopening the same
conversation is enough to recover the binding.

## Completion boundary

`uiState=idle` means only that the Web page is currently not generating. It does not
prove that a software build, experiment, deployment, or other external task completed.
Use durable task state and the real external verification source.
