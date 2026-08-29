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

The browser extension is also the browser-profile boundary. All tab creation happens through
`chrome.tabs.create()` in the profile where the extension is connected. The Python runtime
never launches Edge/Chrome with a profile selector. The bridge requires exactly one connected
profile and fails closed if multiple profile-scoped extension clients are connected.

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

### Bundled MCP servers

The Python package exposes two MCP servers with FastMCP:

- `web_agent_swarm.mcp_server` for browser-agent planning, startup, inspection, messaging, and shared state;
- `web_agent_swarm.codexpro_server` for clean-room local development tools such as workspace inspection, edits, verification commands, background tasks, handoff, and context management.

The CodexPro Lite server is workspace-bounded and deliberately excludes private connector configuration and machine-specific roots.

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

## Durable task graph, Send-style map/reduce, and elastic scale-out/scale-in

The runtime derives a LangGraph-inspired coordination view from the durable task board without
adding LangGraph as a runtime dependency:

```text
durable tasks
    |
    +--> conditional route
    |      dispatch / wait_dependencies / wait_retry / leased / interrupt / terminal
    |
    +--> swarm_task_fanout(items, owner)       # Send-style map
    |      +--> shard 0 payload
    |      +--> shard 1 payload
    |      +--> shard N payload
    |               |
    |               +--> exact worker instance task claim + lease
    |                       implementer
    |                       implementer-2
    |                       implementer-3
    |
    +--> swarm_task_reduce(group_id)           # stable reducer
    |
    v
web_agent_swarm_coordination_plan
    |
    +--> runnable + leased + interrupt work units per role
    +--> desired_instances vs active_instances
    |
    v
web_agent_swarm_elastic_plan
    |
    +--> no-op when instance capacity is sufficient
    +--> add_instances when durable pressure exceeds active capacity
    +--> remove_instances when durable pressure falls below active capacity
    +--> web_agent_swarm_scale applies both directions with lease/superstep/UI guards
```

The state schema stores per-task payload/group metadata, exact `claimed_by` worker identity,
lease expiry, attempt count, retry deadline, bounded `max_attempts`, and result. A valid lease
prevents another worker from claiming the same shard. Retryable failures can return to
`ready`; an expired lease becomes recoverable dispatch work. Fan-out groups retain a
`group_index`, so reducer output is stable in original shard order.

Worker identity is separate from role identity. A role describes capability; worker names are
stable execution instances such as `implementer`, `implementer-2`, and
`implementer-3`. Initial planning may propose multiple instances of one role. Runtime
scale-out may add instances, while scale-in marks safe workers `suspended`, closes their
browser tabs, preserves conversation bindings, and preferentially resumes those conversations
when capacity is needed again.

Mutating durable state uses a cross-process lock plus atomic file replacement so concurrent
browser agents cannot safely-by-accident overwrite each other's claims.

Tasks may also declare dependency task IDs. A dependent task cannot enter the executable
frontier until every dependency is `done`. Review tasks route to the reviewer role. Blocked
tasks are typed: dependency/external waits stay parked, human-input waits interrupt the main
agent, and recoverable/verification/unknown failures may route to the debugger. Browser
idleness is an availability signal only and never creates execution pressure by itself.

The watcher polls every 300 seconds by default. Its digest contains durable task/status/
superstep state plus actionable transport faults; ordinary UI idle/generating/message changes
are excluded. The unresolved-work max-silence wake is also 300 seconds by default.

This is intentionally a **state graph**, not a hidden autonomous agent manager. The main Web
Agent still decides dispatch; the runtime may apply bounded elastic add/remove recommendations
while refusing scale-in for a worker with a live task lease, open/ready superstep, or generating
browser tab.

## Superstep barrier and pending writes

Coordinated rounds can use a Pregel/LangGraph-style superstep barrier when partial publication
would be unsafe:

```text
main: swarm_superstep_begin([worker-a, worker-b])
                    |
          workers claim tasks with superstep_id
                    |
             parallel execution
              /             \
             v               v
      worker-a stage     worker-b stage
       pending writes     pending writes
              \             /
               +--- barrier -+
                       |
          ready_to_commit == true
                       |
             swarm_superstep_commit
                       |
          one locked validation pass
                       |
             one atomic os.replace
                       |
          all task/status writes visible
```

Pending writes are durable but live under the superstep record, not in published task/status
state. Supported pending operations are currently `task_release` and `status_set`.
A task claimed with `superstep_id` cannot be directly released while that superstep is
`open` or `ready`; the runtime forces the worker through `swarm_superstep_stage`.

The commit barrier fails closed. Before publishing anything it verifies that every participant
has staged, no participant failed, each task lease is still active and owned by the staging
worker, each task is still bound to the same superstep, and two workers are not writing the
same task/status target. A participant failure produces `aborted`; stale leases or write
collisions produce `conflict`. In either case the pending writes remain inspectable for
diagnosis, while the published task/status state receives none of the round's outputs.

This provides round-level all-or-nothing visibility. Task claims and lease heartbeats are
coordination metadata established before commit; the workers' result/status outputs are the
transactional portion held behind the barrier.

## Stable identity

Browser tab IDs are ephemeral. The runtime binds agents to the conversation ID extracted
from the ChatGPT conversation URL. If a browser tab is recreated, reopening the same
conversation is enough to recover the binding.

## Completion boundary

`uiState=idle` means only that the Web page is currently not generating. It does not
prove that a software build, experiment, deployment, or other external task completed.
Use durable task state and the real external verification source.
