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
- Derive dependency-aware conditional routes, fan-out frontiers, fan-in barriers, and interrupts from durable tasks.
- Elastically add missing worker roles after startup as task mix or parallelism needs change.
- Wake the main agent when worker/task state changes instead of waiting for a coarse timer.
- Start that watcher automatically when a confirmed swarm is launched.
- Optionally expose the same controls as MCP tools.
- Bundle a clean-room CodexPro-compatible local development MCP for workspace inspection, edits, verification commands, background tasks, handoff, and context management.

## Safety and privacy model

- The bridge listens on loopback by default.
- The extension is limited to `https://chatgpt.com/*`.
- WAS controls only the currently connected browser profile; it does not launch or switch to another Edge/Chrome profile.
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

Bundled second MCP:
CodexPro Lite local development tools
(workspace/search/read/write/edit/background/handoff/context)
```

## Quick start

1. Install Node.js 20+ and Python 3.10+.
2. In `bridge/`, run `npm install`, then `npm start`.
3. Load `browser-extension/` as an unpacked extension in Edge or Chrome.
4. Open the extension Options page and confirm the WebSocket URL points to the bridge.
5. Create a Python environment and install:
   `pip install -e ".[mcp]"`
6. Run:
   `web-agent-swarm health`
7. Add the two bundled MCP servers to your MCP-capable host using `examples/mcp-config.example.json`.
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

After startup, the main orchestrator uses `web_agent_swarm_coordination_plan`
to read a durable task graph: dependency-aware conditional routes, fan-in barriers,
retry waits, active leases, blocked-task interrupts, and per-role instance pressure.

WAS also implements a lightweight equivalent of LangGraph `Send` + reducer semantics:
`swarm_task_fanout` creates independent map shards with different payload state,
multiple instances of the **same role** (for example `implementer`,
`implementer-2`, `implementer-3`) can atomically claim different shards, and
`swarm_task_reduce` aggregates results in stable shard order. Claim/heartbeat/release
leases prevent duplicate execution; retryable failures return to `ready` until
`max_attempts` is exhausted.

`web_agent_swarm_elastic_plan` compares runnable shard pressure with active instance
counts, and `web_agent_swarm_scale` may add missing roles **or additional instances of
an existing role** without tearing down the team. Initial planning/startup is also
instance-aware via `instance_plan` and `instances={"implementer": 4}`.

Tasks can declare dependencies, so downstream work stays behind a durable fan-in
barrier until prerequisites are `done`. Review work routes to `reviewer`, blocked
work routes to `debugger`, and browser idle alone never triggers scale-out. The
design borrows LangGraph-style graph semantics without requiring LangGraph at runtime.

Example explicit instance control:

```bash
web-agent-swarm start --roles implementer tester --instances implementer=4 tester=1
web-agent-swarm scale --instances implementer=6
```

The second command treats the count as a target total, so it adds only the missing
`implementer-5` / `implementer-6` instances and preserves the existing workers.

### Atomic supersteps

For a coordinated round where no worker result should become visible until every worker
succeeds, WAS provides durable pending writes and an explicit barrier:

```text
swarm_superstep_begin
    -> swarm_task_claim(..., superstep_id=...)
    -> swarm_superstep_stage   # once per participant
    -> ready_to_commit
    -> swarm_superstep_commit
```

If any participant calls `swarm_superstep_fail`, or commit detects a stale lease / conflicting
write, the round becomes `aborted` or `conflict` and **none of its staged task/status outputs
are published**. Pending writes remain available through `swarm_superstep_get` for diagnosis.
A superstep-bound task cannot bypass the barrier with ordinary `swarm_task_release`.

Available MCP tools:

- `swarm_superstep_begin`
- `swarm_superstep_stage`
- `swarm_superstep_fail`
- `swarm_superstep_abort`
- `swarm_superstep_commit`
- `swarm_superstep_get`
- `swarm_superstep_list`

## Bundled CodexPro Lite

This repository now includes a clean-room CodexPro-compatible MCP implementation:

`python -m web_agent_swarm.codexpro_server`

It exposes the same core action surface used by the swarm workflow: workspace open/inspect,
tree/search/read, write/edit/apply_patch, allowlisted verification commands, background
run/status/cancel, change review, handoff, compact/resume/export context, skill loading,
self-test, and the `codexpro(action, args)` supertool wrapper.

It does **not** copy private connector code, authentication, machine-specific roots, or
private configuration. Deep repository symbol/relationship indexing and other proprietary
server internals are intentionally not claimed as byte-for-byte parity.

## Development

```bash
python -m unittest discover -s tests -v
```

The browser bridge is intentionally small and dependency-light.

## License

MIT. See `LICENSE`.
