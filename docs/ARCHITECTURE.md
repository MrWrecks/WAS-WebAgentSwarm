# Architecture

## Filesystem/transport separation

WAS treats the **workspace filesystem** and the **browser transport** as separate concerns.
The workspace is resolved natively by the Python WAS runtime or the vendored upstream CodexPro process. Browser control uses the loopback bridge (`WEB_SWARM_BRIDGE_URL`).
Neither layer derives a workspace path from the other.

Consequently, WSL is only one optional deployment environment:

```text
workspace filesystem:  $HOME/<workspace>         (native runtime path)
browser transport:     http://127.0.0.1:8788     (HTTP/WebSocket)
```

There is no required UNC hop. Windows, Linux, and macOS hosts follow
the same rule: local workspace path + loopback browser transport.

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

WAS also protects Agent-tab lifecycle at this boundary. Observed/created ChatGPT tabs are marked
`autoDiscardable=false`; discarded tabs are restored before inspect/send; and tab metadata exposes
`discarded`, `frozen`, and `autoDiscardable`. A frozen or unresponsive minimized/background
page is classified as `browser_suspended` transport liveness. It is not Task completion and is not
safe evidence for automatic scale-in.

### Local bridge

A small Node.js HTTP/WebSocket server bound to loopback. It translates structured local
requests into extension messages and waits for explicit acknowledgements.

### Python swarm runtime

Owns stable conversation bindings, worker-role planning, exact named-agent messaging,
shared task/status state, and combined snapshots.

### Watcher and browser watchdogs

The watcher is deterministic. It does not decide what task should be done. Its semantic digest
contains durable task/status/task-step/superstep/interrupt state plus actionable transport faults;
ordinary UI idle/generating/message churn is excluded. The bridge protocol v4 also keeps a bounded,
sequence-numbered browser lifecycle event ring, so the watcher can long-poll for tab/extension
changes and wake before the fixed polling interval. Fixed polling remains the fallback for durable
state and bridge compatibility.

Transport detection is split into observation-only watchdogs:
MainConversationLimitWatchdog, BrowserSuspensionWatchdog, InspectErrorWatchdog,
GeneratingStallWatchdog, and BrowserLifecycleWatchdog. They never mutate task ownership
or publish canonical state.

A main-conversation length limit is a transport exhaustion condition, not durable completion.
The watcher does not send another ordinary tick when the main send path is disabled. It opens
a fresh ChatGPT conversation in the same connected browser context, bootstraps the main orchestrator
to re-read durable WAS state and graph, waits for a stable conversation binding, rebinds main,
records rollover lineage, and only then closes the exhausted tab. Task leases, checkpoints, graph
state, interrupts, MVCC lanes, and other durable coordination state are not rewritten by rollover.

### MCP servers

WAS exposes its browser/swarm control MCP from the Python package:

- `web_agent_swarm.mcp_server` for browser-agent planning, startup, inspection, messaging, and shared state.

For workspace development tools, WAS vendors the **complete pinned upstream CodexPro source tree** rather than reimplementing it. The Git-tracked CodexPro tree is stored byte-for-byte in `integrations/codexpro/codexpro-0.30.0-src.tar.gz`, verified against `UPSTREAM_TREE.sha256`, expanded to `.runtime/codexpro/`, built with upstream `package.json`, and launched through the upstream `scripts/codexpro.mjs` entrypoint. WAS adds only thin setup/start wrappers around that upstream interface.

## Confirmed startup with main-only elastic bootstrap

```text
user asks for a swarm
       |
       v
web_agent_swarm_plan      read-only proposal
       |
       v
user confirms
       |
       v
web_agent_swarm_start     browser mutation
       |
       +-- main agent only by default
       +-- stable binding
       |
       v
main reads durable graph
       |
       v
elastic_plan -> scale
       |
       +-- create/reuse only justified workers
```

This keeps the proposal/confirmation boundary while separating **approval to start the swarm**
from a fixed initial worker topology. With no explicit roles, only the main conversation is opened;
the main agent then derives the initial worker count/roles from runnable parallel pressure.
Explicit roles/instance counts remain available when an operator wants to pin bootstrap capacity.

## Explicit durable SwarmGraph, task DAG, and elastic execution

WAS now separates the logical execution topology from browser execution capacity. An optional
activated `SwarmGraph` is persisted beside the legacy task DAG and is compiled into dispatch gates
without adding LangGraph as a runtime dependency:

```text
durable SwarmGraph (optional, explicit, revisioned)
    |
    +--> task/control/router/join/agent/tool nodes
    +--> dependency edges
    +--> conditional edges (structured predicates, no code execution)
    |
    v
compiled task gates
    |
    +--> dispatch / wait_graph / graph_unselected
    |
    +--> durable task DAG
    |      wait_dependencies / wait_retry / leased / interrupt / terminal
    |
    +--> swarm_task_fanout(items, capabilities/resources)  # Send-style map
    |      +--> shard 0 payload
    |      +--> shard 1 payload
    |      +--> shard N payload
    |               |
    |               +--> capability/resource match
    |               +--> exact worker instance task claim + lease
    |                       tester / researcher / implementer / ...
    |
    +--> swarm_task_reduce(group_id)           # stable reducer
    |
    v
web_agent_swarm_coordination_plan
    |
    +--> required_capabilities + resource-intent conflict filtering
    +--> reuse free compatible online slots before scale-out
    +--> runnable + leased + interrupt work units per assigned role profile
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

The state schema stores graphs in `graphs` plus one `active_graph_id`. Each graph owns
`nodes`, `edges`, metadata, timestamps, and a monotonic `revision`. Graph mutation methods
accept `expected_revision`; mismatches fail closed, providing optimistic concurrency control for
multiple browser agents editing the same topology.

Task graph nodes bind a durable `task_id` and may override its worker role. Router/control/join
nodes may instead carry explicit durable `status` and JSON `state`. For task-bound nodes, live
task status/result is authoritative at compile time. Dependency edges require the source to be
`done` or `skipped`. Conditional edges wait for a terminal source, then evaluate a bounded
predicate over source status/state, task result, or shared status. No edge condition contains
executable Python/JavaScript.

The state schema also stores per-task payload/group metadata, exact `claimed_by` worker identity,
lease expiry, attempt count, retry deadline, bounded `max_attempts`, and result. A valid lease
prevents another worker from claiming the same shard. Retryable failures can return to
`ready`; an expired lease becomes recoverable dispatch work. Fan-out groups retain a
`group_index`, so reducer output is stable in original shard order.

Worker identity is separate from role identity. A role describes capability; worker names are
stable execution instances such as `implementer`, `implementer-2`, and
`implementer-3`. Runtime scale-out may add instances, while scale-in marks safe workers
`suspended`, closes their browser tabs, preserves conversation bindings, and preferentially
resumes those conversations when capacity is needed again.

The allocator has two anti-thrashing constraints. First, a global worker budget (default six)
limits desired runnable capacity across roles; live leased work forms a protected minimum and is
never force-removed just to satisfy the configured budget. Second, automatic scale-in records an
`excess_since` timestamp and waits through a default 300-second grace window before closing a
safe surplus worker. Explicit operator target counts bypass that grace. A worker with a live task
lease, open/ready superstep participation, or a generating browser tab still cannot be suspended.

Before an MVCC lane exists, task-level  /  are reserved
atomically with the task lease. Conflicting ready work routes to ; the same check is
repeated on . Task heartbeat renews these reservations and every normal release,
superstep commit, or interrupt transition releases them. These reservations are an early scheduling
guard only: filesystem MVCC claims/base hashes remain the final publication authority.

Mutating durable state uses a cross-process lock plus atomic file replacement so concurrent
browser agents cannot safely-by-accident overwrite each other's claims. Graph edits add a second
guard: revision checks prevent a stale topology writer from overwriting a newer graph revision.

An active SwarmGraph gates both `coordination_plan` and `swarm_task_claim`. Therefore graph
routing is not advisory: a worker cannot bypass the main orchestrator and claim a task whose graph
node is still `wait_graph` or whose conditional branch is `graph_unselected`. A task with an
already-valid lease is not preempted by a later graph edit; topology changes affect new claims and
future scheduling.

Tasks may also declare dependency task IDs. A dependent task cannot enter the executable
frontier until every dependency is `done`. Review tasks route to the reviewer role. Blocked
tasks are typed: dependency/external waits stay parked, human-input waits interrupt the main
agent, recoverable/unknown failures may route to the debugger, and `verification_failure`
is a zero-pressure wait until concrete runnable repair/verification work exists. Browser idleness
is an availability signal only and never creates execution pressure by itself.

The watcher polls durable state every 300 seconds by default, but browser lifecycle changes can
wake it earlier through the bridge event ring. Its digest contains durable task/task-step/status/
superstep/interrupt state, the active SwarmGraph topology/revision, and actionable transport
faults; ordinary UI idle/generating/message changes are excluded. A rejected conditional branch
(`graph_unselected`) is not treated as unresolved work, so it does not create useless safety ticks.
The unresolved-work max-silence wake is also 300 seconds by default.

This is intentionally a **state graph**, not a hidden autonomous agent manager. The main Web
Agent still decides dispatch; the runtime may apply bounded elastic add/remove recommendations
while refusing scale-in for a worker with a live task lease, open/ready superstep, or generating
browser tab.

## Filesystem MVCC lanes and hard resource isolation

The task/graph registry coordinates *what* should run; MVCC lanes enforce *where filesystem side
effects may occur*. Repository-changing work must first own the task lease, then create a lane with
hierarchical resource claims. The lane is a private source copy with an immutable base manifest.

```text
canonical workspace (canonical_id, epoch)
        |
        +--> Run A / Lane A ---- pending files, reads, scratch, experiments
        |
        +--> Run B / Lane B ---- independent private copy
                              |
                              v
                     commit validation
                     base + read-set + write-set
                              |
                    +---------+---------+
                    |                   |
                 commit              conflict
                    |                   |
             publish claimed       preserve lane;
             write paths only      fork/rebase
```

Resource claims are canonical-workspace-scoped and hierarchical. `src/autol` overlaps every child
path. Modes are `read`, `write`, and `scratch`: scratch permits private experiment/build output
but is excluded from publication. Namespaced non-path resources (for example
`experiment:drc/17net`) provide the same lease/conflict mechanism for expensive logical work.

CodexPro's mutation/command entry points classify each workspace as canonical, lane, or unmanaged.
A protected canonical workspace rejects managed mutation/commands; a lane accepts mutations only
while active, and final commit rejects any delta outside its write/scratch claims. File reads form a
precise read set; broad search/tree/inspect/commands conservatively mark an opaque read and therefore
validate the full base manifest. Each successful write publication advances a per-canonical epoch and
per-resource version; a global epoch remains only as an event sequence.

Filesystem publication is journaled. Pre-image backups and a journal are durable before the registry
enters `committing`. A process restart treats a still-`committing` transaction as aborted and rolls
canonical back. If recovery material is unavailable, the lane/commit becomes `recovery_required`
and managed canonical reads/writes remain blocked. Coordination checkpoints deliberately do not
rewind lanes or published filesystem state; checkpoint restore is refused while a live/recovery lane
exists.

## Durable interrupt/resume and checkpoint recovery

WAS adopts LangGraph-style persistence semantics at the coordination layer without embedding a
LangGraph runtime. Human input is represented as explicit durable state rather than an implicit
conversation pause:

```text
task / main
    |
    v
swarm_interrupt_create
    |
    +-- durable interrupt = open
    +-- linked task = blocked(human_input)
    +-- worker lease released
    |
    v
external/user input arrives
    |
    v
swarm_interrupt_resume(value)
    |
    +-- resume value persisted
    +-- interrupt = resumed
    +-- linked task = ready
```

For risky graph rewrites or recovery points, `swarm_checkpoint_create` stores a bounded snapshot
of tasks, shared status, supersteps, interrupts, every SwarmGraph, and `active_graph_id`. Browser
bindings are deliberately excluded:
a browser conversation is an execution resource/identity binding, not graph state. Restore keeps
those bindings and checkpoint history intact and fails closed while any current live task lease or
open/ready superstep exists. Any task restored in an `active` state has its lease expiry cleared,
so recovery routes it through expired-lease dispatch rather than pretending an old worker still
owns it. If the checkpoint itself captured an open/ready superstep, restore marks that historical
barrier `aborted` and clears affected task-to-superstep bindings so an old barrier cannot be
resurrected into a recovery deadlock.

Task `group_id`, `parent_task_id`, fan-out shards, and reducer boundaries provide lightweight
subgraph/workflow scoping. This is intentionally smaller than a general nested graph engine:
independent work scopes share the durable store but keep explicit task/group identities and do not
gain hidden memory merely because they share a browser session.

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

## Conversation rollover lifecycle

Conversation-length exhaustion is transport lifecycle debt, not durable task completion. Main and worker instances are replaced in place: a fresh conversation keeps the same stable worker name and role; durable tasks, leases, supersteps, lanes, and checkpoints are not released or duplicated solely because chat history ended; rebinding happens before the exhausted tab is closed; and a failed old-tab close is persisted as `rollover_close_pending_tab_id` so the watcher can retry exact-tab cleanup on later cycles.
