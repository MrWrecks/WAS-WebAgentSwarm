# WebAgentSwarm

**WebAgentSwarm (WAS)** is a local-first orchestration runtime for coordinating multiple **ChatGPT Web agents** as a durable, elastic software-development swarm.

Unlike conventional multi-agent frameworks where multiple agents run inside one Python process, WAS coordinates **multiple independent browser conversations**. Each browser tab is a real ChatGPT Web agent with its own context, while coordination truth lives in a shared durable runtime.

The core design goal is simple:

> Multiple Web agents should be able to work in parallel without relying on chat memory, manual coordination, Git branches, or “agents behaving nicely.”

---

## Why WebAgentSwarm?

Most agent frameworks assume something like:

```text
Python Runtime
├── Agent A
├── Agent B
├── Agent C
└── Shared State
```

WAS instead targets:

```text
Browser
├── ChatGPT Web Agent — Main
├── ChatGPT Web Agent — Implementer
├── ChatGPT Web Agent — Tester
├── ChatGPT Web Agent — Reviewer
└── ...
          │
          ▼
   Durable Swarm Runtime
```

Every Web agent is an independent conversation.

They coordinate through durable tasks, leases, graphs, checkpoints, resource claims, and isolated development lanes instead of relying on one conversation forwarding natural-language messages between workers.

---

# Architecture

```text
                    User
                     │
                     ▼
             Main Web Agent
                     │
                     ▼
              Durable SwarmGraph
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Task A      Task B      Task C
          │          │          │
          ▼          ▼          ▼
      Worker A    Worker B    Worker C
      Web Tab     Web Tab     Web Tab
          │          │          │
          └──────────┼──────────┘
                     ▼
              Durable Registry
                     │
                     ▼
        Resource Claims + MVCC Lanes
                     │
                     ▼
             Canonical Workspace
```

WAS separates two layers:

### Logical execution layer

```text
SwarmGraph
Tasks
Dependencies
Conditional edges
Interrupts
Checkpoints
Supersteps
```

### Physical execution layer

```text
ChatGPT Web tabs
Worker roles
Worker instances
Browser bindings
Elastic scale-out / scale-in
```

This means logical workflow topology does not depend on how many browser agents happen to be active.

---

# Durable Coordination

WAS does **not** use browser conversation history as cluster truth.

The runtime maintains durable structured state including:

```text
tasks
bindings
shared_status
supersteps
interrupts
checkpoints
graphs
runs
lanes
resource_claims
resource_versions
commits
```

A worker must atomically claim a task before executing it.

```text
ready
  │
  ▼
task_claim
  │
  ├── claimed_by
  ├── lease_expires_at
  └── attempts
```

Only one worker can hold a valid task lease at a time.

If a worker disappears, the lease can expire and the work becomes recoverable.

---

# Explicit Durable SwarmGraph

WAS includes an explicit mutable workflow graph inspired by LangGraph-style execution semantics.

```text
SwarmGraph
├── task nodes
├── router nodes
├── control nodes
├── join nodes
├── agent nodes
├── tool nodes
│
├── dependency edges
└── conditional edges
```

Example:

```text
                    Research
                       │
              ┌────────┴────────┐
              ▼                 ▼
        Implement A        Implement B
              │                 │
              └────────┬────────┘
                       ▼
                     Test
                       │
                       ▼
                    Review
```

Conditional routing is durable and declarative.

Example condition:

```json
{
  "source": "task_result",
  "path": "route",
  "op": "eq",
  "value": "fast"
}
```

A conditional branch can become:

```text
dispatch
wait_graph
graph_unselected
```

Graph routing is not merely advisory.

The atomic task-claim layer checks the active SwarmGraph, so a worker cannot manually claim a task that is blocked by graph state.

Graph mutations also support optimistic revision control:

```text
Graph revision = 12

Agent A updates revision 12
→ revision 13

Agent B still writes expected_revision=12
→ REVISION CONFLICT
```

No silent topology overwrite.

---

# Elastic Web-Agent Pool

WAS can start with only the Main agent.

```text
Main
```

The Main agent reads durable runnable pressure and decides how many workers are actually needed.

Example:

```text
Runnable work:
implementer = 3
tester      = 1
reviewer    = 0
```

Possible runtime topology:

```text
Main
├── implementer
├── implementer-2
├── implementer-3
└── tester
```

When work finishes, excess workers can be suspended and browser tabs closed.

Later demand can resume the preserved conversation before opening a new worker.

WAS includes:

* global worker budget
* role pressure
* multiple instances of the same role
* lease-aware scale-in
* superstep-aware scale-in
* generating-tab protection
* scale-in hysteresis
* preserved browser bindings

Browser idle is treated only as an availability signal.

> Browser idle is never considered task completion truth.

---

# Send-Style Fan-Out and Reduce

Parallel work can be dynamically sharded.

```text
Task
 │
 ▼
fanout
 ├── shard 0
 ├── shard 1
 ├── shard 2
 └── shard 3
        │
        ▼
 multiple worker instances
        │
        ▼
      reduce
```

Workers of the same role can claim separate shards concurrently:

```text
implementer
implementer-2
implementer-3
```

Reducer output remains stable in shard order.

---

# Durable Interrupt / Resume

Human input is represented as durable execution state rather than inferred from chat.

```text
task
 │
 ▼
interrupt_create
 │
 ├── task = blocked
 ├── interrupt = open
 └── worker lease released
 │
 ▼
human input
 │
 ▼
interrupt_resume
 │
 ├── resume value persisted
 └── task = ready
```

A restarted browser does not need to reconstruct the interrupt from chat history.

---

# Checkpoints and Time Travel

WAS can checkpoint coordination state before risky workflow changes.

A checkpoint stores:

```text
tasks
shared_status
supersteps
interrupts
SwarmGraphs
active_graph_id
```

Browser conversation bindings are intentionally not rewound.

```text
Logical coordination state
        ≠
Browser execution identity
```

Checkpoint restore fails closed while live task leases, active barriers, or live MVCC lanes exist.

---

# Atomic Supersteps

For coordinated rounds where partial publication is unsafe, WAS supports Pregel-style pending writes.

```text
superstep_begin
      │
      ├── Worker A stages result
      ├── Worker B stages result
      └── Worker C stages result
                │
                ▼
            Barrier ready
                │
                ▼
         atomic superstep_commit
```

If one participant fails:

```text
superstep_fail
      │
      ▼
entire round aborted
```

No partial durable publication.

---

# MVCC Development Lanes

One of the biggest problems in multi-agent coding systems is that coordination state can say:

```text
Agent A owns module X
```

while another agent can still physically modify the same file.

WAS addresses this with filesystem-level **MVCC development lanes**.

```text
Task Claim
    │
    ▼
Hierarchical Resource Claim
    │
    ▼
Private MVCC Lane
    │
    ▼
Agent modifies private workspace
    │
    ▼
Read / Write Validation
    │
    ├── PASS
    │      ▼
    │   Commit
    │      ▼
    │  Canonical Workspace
    │
    └── CONFLICT
           ▼
       Never overwrite
```

Each Run receives a private source copy.

The agent works only inside that lane.

---

## Hierarchical Resource Claims

Resources are hierarchical.

```text
claim: src/autol
```

conflicts with:

```text
src/autol/router.py
src/autol/web_agent_swarm.py
src/autol/submodule/foo.py
```

Resource claims are scoped to a canonical workspace, so identical paths in unrelated projects do not conflict.

Logical resources are also supported.

Example:

```text
experiment:calibre/17net
benchmark:router/v3
artifact:oa/view/foo
```

This can prevent two agents from independently launching the same expensive experiment.

---

# Write / Read / Scratch Claims

Path resources support three modes.

### `write`

The lane may modify the resource and publish it to canonical during commit.

### `read`

The resource represents a dependency observed by the Run.

If canonical changes before commit, the Run can fail validation.

### `scratch`

The lane may modify the path, but the result is never published.

Useful for:

```text
build/
tmp/
scratch/
experiment output
generated logs
temporary verifier artifacts
```

Example:

```text
src/router.py      → write
config/router.yaml → read
scratch/calibre/   → scratch
```

---

# Serializable-Style Read Validation

Checking only modified files is not sufficient.

Example:

```text
Agent A reads config.py
Agent A modifies router.py

Agent B modifies config.py
```

Even though both agents modified different files, Agent A's result may now be stale.

WAS therefore tracks:

```text
read_set
write_set
```

Broad operations such as repository-wide search or arbitrary shell commands can be marked as:

```text
opaque_read = true
```

which conservatively validates the full base manifest before commit.

This is similar in spirit to optimistic serializable validation.

---

# Canonical Versions and Epochs

Each canonical workspace has its own identity and version sequence.

```text
canonical_id
canonical_epoch
resource_version
```

Example:

```text
src/router.py
version = 18

Agent lane saw:
version = 17

commit
→ conflict
```

The old lane is preserved for provenance.

It can then fork from the new canonical state:

```text
old lane
   │
   ▼
conflict
   │
   ▼
swarm_lane_fork
   │
   ▼
new lane from latest canonical
```

---

# Hard Canonical Guard

The bundled CodexPro runtime recognizes:

```text
canonical workspace
lane workspace
unmanaged workspace
```

When a live MVCC lane protects a canonical workspace, managed mutation entry points are blocked.

Examples:

```text
write
edit
apply_patch
bash
background execution
```

must run inside the lane instead.

Therefore the system does not rely only on:

> "Agent, please remember not to edit the shared workspace."

The restriction exists at the tool boundary.

Final commit also re-derives the filesystem delta, so edits outside claimed paths fail closed.

---

# Crash-Safe Commit Journal

Multi-file publication can fail in the middle of a process crash.

WAS therefore creates:

```text
pre-image backups
+
durable commit journal
```

before publishing canonical files.

```text
prepare backups
      │
      ▼
write journal
      │
      ▼
status = committing
      │
      ▼
publish files
      │
      ▼
durable commit
```

If the process dies while the durable state still says:

```text
committing
```

the next runtime startup automatically restores the canonical workspace to the pre-commit state.

Manual recovery is also available through:

```text
swarm_lane_recover
```

If required recovery material is missing or invalid:

```text
recovery_required
```

is persisted and managed canonical access remains fail-closed.

---

# Browser Bridge

WAS communicates with currently connected ChatGPT Web tabs through a browser bridge.

The bridge supports:

* current browser profile reuse
* stable browser bindings
* targeted tab messaging
* idempotent command IDs
* close-tab
* stuck-generation detection
* retry-safe message delivery

WAS does not intentionally launch arbitrary alternate browser profiles when an existing connected profile is available.

---

# Semantic Watcher

The watcher does not blindly send periodic messages.

It tracks meaningful durable changes such as:

```text
task state
SwarmGraph revision
interrupt state
superstep state
MVCC lane state
resource claims
commit conflicts
commit recovery
transport faults
```

UI-only changes such as:

```text
idle
generating
message count
```

do not by themselves become coordination truth.

The default semantic watcher interval is 300 seconds.

---

# Relationship to LangGraph

WAS borrows several execution ideas associated with graph runtimes such as LangGraph:

| Concept            | WAS                                 |
| ------------------ | ----------------------------------- |
| State graph        | `SwarmGraph`                        |
| Conditional edge   | Structured graph conditions         |
| Send               | `swarm_task_fanout`                 |
| Reducer            | `swarm_task_reduce`                 |
| Pregel superstep   | `swarm_superstep_*`                 |
| Checkpoint         | `swarm_checkpoint_*`                |
| Interrupt / resume | `swarm_interrupt_*`                 |
| Fork / branch      | Graph and MVCC lane fork            |
| Pending writes     | Superstep + lane pending changes    |
| Version tracking   | Graph revisions + resource versions |

However, WAS targets a different execution environment:

> LangGraph usually controls nodes inside one runtime.
> WAS coordinates multiple independent **ChatGPT Web conversations** that may execute filesystem operations, shell commands, browser actions, and external tools.

For that reason WAS adds an additional filesystem transaction layer through MVCC lanes.

---

# Typical Workflow

```text
User starts WAS
      │
      ▼
Main Web Agent
      │
      ▼
Build / inspect durable task graph
      │
      ▼
Elastic worker allocation
      │
      ▼
Worker task claim
      │
      ▼
MVCC lane + resource claims
      │
      ▼
Worker modifies/tests private workspace
      │
      ▼
MVCC validation
      │
      ├── conflict → preserve / fork
      │
      └── commit
             │
             ▼
      canonical update
             │
             ▼
        task release
             │
             ▼
      downstream graph routing
```

---

# Installation

Clone the repository and install locally:

```bash
pip install -e .
```

Optional MCP support:

```bash
pip install -e ".[mcp]"
```

---

# Run

Start WAS with Main only:

```bash
web-agent-swarm start
```

WAS can then derive the worker topology from durable runnable pressure.

Explicit roles are also supported:

```bash
web-agent-swarm start --roles implementer tester reviewer
```

Multiple instances of one role:

```bash
web-agent-swarm scale --instances implementer=4 tester=2
```

Start the watcher:

```bash
web-agent-swarm-watch
```

Start the bundled CodexPro-compatible local development server:

```bash
web-agent-swarm-codexpro
```

---

# Core MCP Operations

## Tasks

```text
swarm_task_create
swarm_task_claim
swarm_task_heartbeat
swarm_task_release
swarm_task_fanout
swarm_task_reduce
```

## SwarmGraph

```text
swarm_graph_create
swarm_graph_get
swarm_graph_list
swarm_graph_replace
swarm_graph_preview
swarm_graph_validate
swarm_graph_activate
swarm_graph_deactivate
swarm_graph_node_add
swarm_graph_node_update
swarm_graph_node_remove
swarm_graph_edge_add
swarm_graph_edge_update
swarm_graph_edge_remove
```

## MVCC lanes

```text
swarm_lane_start
swarm_lane_get
swarm_lane_list
swarm_lane_heartbeat
swarm_lane_commit
swarm_lane_abort
swarm_lane_fork
swarm_lane_recover
swarm_resource_claim
swarm_resource_claim_list
swarm_run_list
swarm_commit_list
```

## Interrupts

```text
swarm_interrupt_create
swarm_interrupt_resume
swarm_interrupt_list
```

## Checkpoints

```text
swarm_checkpoint_create
swarm_checkpoint_list
swarm_checkpoint_restore
```

## Supersteps

```text
swarm_superstep_begin
swarm_superstep_stage
swarm_superstep_fail
swarm_superstep_commit
```

---

# Safety Model

WAS uses multiple layers rather than trusting one mechanism.

```text
Task Lease
    ↓
prevents duplicate task ownership

Resource Claim
    ↓
avoids obvious parallel conflicts

Private MVCC Lane
    ↓
isolates filesystem side effects

Read/Write Validation
    ↓
detects stale execution

Canonical Tool Guard
    ↓
blocks managed direct mutation

Commit Journal
    ↓
protects against partial publication

Conflict/Fork
    ↓
never silently overwrites newer work
```

Resource claims are primarily a coordination and efficiency mechanism.

MVCC validation is the correctness boundary.

---

# Verification

Current release:

```text
WebAgentSwarm v0.4.0
```

Regression suite:

```text
63 / 63 tests PASS
```

Covered scenarios include:

* task lease exclusivity
* graph routing
* graph revision conflicts
* conditional branch selection
* elastic scaling
* scale-in safety
* durable interrupts
* checkpoint restore
* superstep atomicity
* hierarchical resource conflicts
* canonical direct-write blocking
* lane out-of-scope mutation rejection
* stale write conflict
* stale read conflict
* opaque-read validation
* scratch non-publication
* multi-canonical isolation
* resource version tracking
* conflict → fork
* crash-interrupted commit rollback

Release check:

```text
PASS
```

---

# Design Principle

WAS treats browser agents as unreliable distributed workers rather than trusted cooperating scripts.

The runtime therefore follows one core rule:

> Coordination intent should be durable, and correctness should be enforced below the prompt layer.

That means an agent may misunderstand an instruction, retry incorrectly, or attempt to touch the wrong file — but the runtime should still prevent silent corruption of shared state.

---

# License

MIT
