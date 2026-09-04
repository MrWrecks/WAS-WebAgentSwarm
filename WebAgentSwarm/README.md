# Web Agent Swarm

A local-first, browser-hosted multi-agent orchestration toolkit for ChatGPT Web.

It provides a small browser extension, a localhost bridge, a durable shared task/status
store, an optional MCP server, and an event-aware watcher that can wake a main Web Agent
when worker state changes.

## What this project does

- Open or reuse multiple ChatGPT Web conversations.
- Start a main orchestrator alone by default, then elastically create/reuse capability-compatible worker slots from durable parallel pressure; explicit worker sets are still supported.
- Treat task `owner` / `preferred_role` as a scheduling preference, not exclusive ownership; `required_capabilities` plus dependency/resource constraints determine the runnable worker set.
- Reserve declared task  /  before dispatch so obvious write collisions park as ; MVCC remains the final publication boundary.
- Inspect bounded visible UI state: idle/generating, recent visible messages, and browser lifecycle state.
- Protect managed ChatGPT Agent tabs with `autoDiscardable=false`; detect `discarded/frozen/unresponsive` tabs and treat them as transport faults rather than completion.
- Detect main-conversation length exhaustion, stop ordinary ticks to that tab, open a fresh main conversation in the same connected browser context, rebind main, and resume from durable WAS state before closing the exhausted tab.
- Send an exact message to an exact ChatGPT tab or stable conversation binding.
- Keep a small durable task board and shared status store outside chat history.
- Recommend a worker count/role subset before opening browser tabs.
- Persist an explicit mutable `SwarmGraph` with task/control/router/join nodes, dependency/conditional edges, revision-safe edits, and activation/deactivation.
- Derive dependency-aware conditional routes, fan-out frontiers, fan-in barriers, and interrupts from durable tasks.
- Elastically add or safely suspend worker instances as durable task pressure changes, under a global worker budget and scale-in hysteresis.
- Keep waiting-only verification blockers at zero worker pressure; a blocked task does not keep a browser worker alive unless concrete runnable follow-up exists.
- Automatically materialize up to two safe JIT sidecars for a single runnable primary when natural parallel frontier work is absent; automatic sidecars are readonly, proposal-only, or experiment-only and never create a competing canonical writer.
- Persist human-input interrupts/resume values and bounded coordination checkpoints for safe recovery/time-travel.
- Preserve checkpoint ancestry/head across restore-and-resume branches so replay history remains auditable.
- Cache expensive deterministic task sub-steps with leases, automatic MVCC-aware fingerprints, and reusable durable results.
- Receive browser lifecycle events through a bounded bridge event ring and split transport health into independent watchdogs; fixed-interval polling remains a fallback rather than the only wake mechanism.
- Run deterministic scheduler benchmark episodes with explicit invariants/reward for fan-out, sidecars, waits, fan-in, and worker budgets.
- Poll durable swarm state every 5 minutes by default; UI-only idle/generating/message changes do not create semantic ticks.
- Wake the main agent on durable task/superstep/step-cache changes, actionable browser lifecycle faults, or a 5-minute unresolved-work safety wake.
- Start that watcher automatically when a confirmed swarm is launched, and restart it when watcher parameters change.
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
   |        |          +-- event-aware watcher + watchdogs
   |        +------------- durable task/status/step/checkpoint store
   +---------------------- optional MCP server
                                ^
                                |
                        main / worker agents

Bundled second MCP:
CodexPro Lite local development tools
(workspace/search/read/write/edit/background/handoff/context)
```

## Portable runtime model — native filesystem first

WAS is intentionally **not** tied to WSL, UNC paths, a specific Windows account, or a
particular Linux distribution. Run each process in the filesystem namespace where its
workspace actually lives:

```text
Windows repository   -> run Python/Node on Windows -> <native-windows-workspace>
Linux repository     -> run Python/Node on Linux   -> $HOME/<workspace>
macOS repository     -> run Python/Node on macOS   -> $HOME/<workspace>
WSL repository       -> run Python/Node in WSL     -> $HOME/<workspace>
```

For WSL, prefer a native Linux workspace under `$HOME`. Do **not** make a Windows UNC alias
for that Linux directory the default MCP/workspace root. If the browser is on Windows and the
WAS runtime is in WSL, communicate with the browser bridge over localhost HTTP/WebSocket;
file access remains native inside WSL. UNC/network workspaces are an explicit operator choice,
not a WAS requirement or default.

All machine-specific paths belong in local environment/configuration, never in the repository.
The release check rejects common user-home, WSL UNC, distro-name, and private-hostname leaks.

## Quick start

1. Install Node.js 20+ and Python 3.10+.
2. In `bridge/`, run `npm install`, then `npm start`.
3. Load `browser-extension/` as an unpacked extension in Edge or Chrome.
4. Open the extension Options page and confirm the WebSocket URL points to the bridge.
5. Create a Python environment and install:
   `pip install -e ".[mcp]"`
6. Run:
   `web-agent-swarm health`
7. Build the vendored CodexPro with `scripts/setup-codexpro.sh` (or the PowerShell equivalent), then use `examples/mcp-config.example.json` for local STDIO hosts or the ChatGPT Web guide below for Funnel.
8. Tell the controller something natural like “open a swarm”. It should call the read-only
   plan tool first and start only after confirmation. With no explicit roles, startup opens only
   the main Web Agent; that main agent reads the durable graph and creates the initial worker
   topology itself. Pass explicit roles/instances only when you want to pin the initial topology.

See `docs/INSTALL.md` and `docs/ARCHITECTURE.md`.

## Install and connect from ChatGPT Web

WAS vendors the **complete CodexPro 0.30.0 Git-tracked source tree** as the single archive
`integrations/codexpro/codexpro-0.30.0-src.tar.gz`. The Python `CodexPro Lite` compatibility layer has been
removed; the Node/TypeScript CodexPro implementation is authoritative.

Keep all examples machine-neutral. Replace placeholders locally and never commit account
names, private hostnames, tokens, user-home paths, or device identifiers.

### 1. Install WAS

Requirements:

- Python 3.10+
- Node.js 20+
- Edge or Chrome for Web Agent workers
- Tailscale when using the recommended Funnel path to ChatGPT Web

Install WAS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[mcp]"
```

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[mcp]"
```

Install the local browser bridge:

```bash
cd bridge
npm install
npm start
```

Load `browser-extension/` as an unpacked extension in Edge/Chrome and leave its bridge
WebSocket on localhost. **Do not expose the browser bridge through Tailscale Funnel.**
Only the CodexPro MCP endpoint should be published.

### 2. Build the complete vendored CodexPro

Linux/macOS/WSL:

```bash
./scripts/setup-codexpro.sh
```

PowerShell:

```powershell
.\scripts\setup-codexpro.ps1
```

Equivalent manual commands:

```bash
cd integrations/codexpro
npm ci
npm run build
```

The setup script verifies SHA-256, extracts the complete source into local
`.runtime/codexpro/`, runs `npm ci`, and builds the real `dist/stdio.js` and `dist/http.js`.
`integrations/codexpro/WAS_VENDOR.md` records the upstream version and commit.

### 3. Choose the workspace CodexPro may access

Pass a narrow workspace root at launch rather than exposing a whole home directory:

```text
<workspace-root>
```

For example, the launcher accepts:

```bash
./scripts/start-codexpro.sh start --root <workspace-root>
```

PowerShell:

```powershell
.\scripts\start-codexpro.ps1 start --root <workspace-root>
```

CodexPro constrains file operations to its configured roots and blocks common credential,
dependency, and build paths. The workspace root is still the primary access boundary.

### 4. Recommended ChatGPT Web transport: Tailscale Funnel

Install Tailscale from the official package for the operating system, sign in, and verify
that the node is connected:

```bash
tailscale up
tailscale status
```

Tailscale Funnel requires the tailnet's Funnel capability plus HTTPS/MagicDNS support.
CodexPro has a built-in Tailscale mode; use it instead of manually exposing the bridge.
After identifying the device's Funnel hostname, start:

```bash
./scripts/start-codexpro.sh tailscale \
  --root <workspace-root> \
  --hostname <device-funnel-hostname>
```

PowerShell:

```powershell
.\scripts\start-codexpro.ps1 tailscale `
  --root <workspace-root> `
  --hostname <device-funnel-hostname>
```

CodexPro runs its MCP HTTP server on loopback and asks Tailscale Funnel to publish it as
HTTPS. It prints/copies the final **ChatGPT Server URL**, ending in `/mcp`.

Important distinction:

- in ChatGPT, choose **Authentication: No Authentication / None**;
- CodexPro can still protect the public endpoint with its own private
  `codexpro_token` embedded in the copied Server URL;
- therefore do **not** remove the token from the URL and do **not** select OAuth merely
  because the URL is public;
- do not commit or share the generated Server URL because the query token is a secret.

This is the intended developer-mode setup. OAuth is not part of the normal WAS + CodexPro
installation flow documented here.

Useful Tailscale checks:

```bash
tailscale funnel status
```

Stop/reset the Funnel when it is no longer needed according to the installed Tailscale
CLI version.

### 5. Enable Developer mode in ChatGPT Web

The exact menu label can vary by ChatGPT rollout. Use the Developer mode toggle available
in your account/workspace:

- if your UI exposes **Settings -> Security and login -> Developer mode**, enable it there;
- current OpenAI documentation for Business/Enterprise/Edu also documents
  **Settings -> Apps -> Advanced Settings -> Developer mode** and
  **Workspace settings -> Apps -> Create** as supported entry points.

Full MCP write/modify support is plan/workspace dependent. Use ChatGPT Web; custom MCP app
support is not a local-browser-extension feature.

### 6. Add CodexPro in the app/plugin page

With Developer mode enabled:

1. Open the ChatGPT **Apps/Plugins** configuration page.
2. Click **Create** or the round **+** button next to the custom app/plugin control,
   depending on the UI version.
3. Give the app a name such as `CodexPro`.
4. Choose the connection type **Server URL** when that option is shown.
5. Paste the **complete Server URL printed/copied by CodexPro**. It should contain the
   HTTPS Funnel hostname and `/mcp`; if CodexPro appended `codexpro_token`, keep it intact.
6. For authentication, explicitly choose
   **No Authentication / None**. Some forms default to OAuth; change it to No
   Authentication before creating the app.
7. Click **Scan Tools** and wait for tool discovery to finish.
8. Click **Create/Save**.
9. In a new ChatGPT Web conversation, enable/select the CodexPro app and start with
   read-only checks such as `server_config`, `open_current_workspace`, and
   `codexpro_self_test` before allowing edits or bash commands.

The key configuration is therefore:

```text
Connection:      Server URL
Server URL:      https://<funnel-host>/mcp?...codexpro_token=...
Authentication:  No Authentication / None
Developer mode:  On
```

Do **not** choose OAuth for this documented development configuration.

### 7. Troubleshooting

If tool scanning or connection fails, check in this order:

```text
vendored CodexPro built
  -> CodexPro process is still running
  -> workspace root exists and is allowed
  -> tailscale status is healthy
  -> tailscale funnel status shows the expected mapping
  -> copied Server URL is complete and still contains its token
  -> ChatGPT Developer mode is enabled
  -> ChatGPT connection type is Server URL
  -> ChatGPT Authentication is No Authentication / None
  -> Scan Tools succeeds
```

If the ChatGPT page itself becomes white/unresponsive, WAS does not immediately create a
new conversation. It confirms the transport fault, refreshes/recycles the **same bound
conversation** into a fresh renderer, verifies it, and only escalates to rollover if that
bounded recovery fails. Durable Task/Run/checkpoint ownership is unchanged by this repair.

### 8. Local STDIO use

For a local MCP host rather than ChatGPT Web, use the built vendor output directly after
building:

```text
node <path-to-web-agent-swarm>/integrations/codexpro/dist/stdio.js
```

`examples/mcp-config.example.json` contains a generic STDIO example for both WAS and the
vendored CodexPro server.

### 9. What is vendored

`integrations/codexpro/codexpro-0.30.0-src.tar.gz` contains the complete Git-tracked
CodexPro source tree: TypeScript MCP server, HTTP/STDIO transports, repository analysis,
search, image/attachment support, handoff tooling, launcher, tests/smokes, package metadata,
docs, security documentation, and license. It is kept as one archive only to reduce Git/Web
upload file count. `setup-codexpro` expands it locally into `.runtime/codexpro/`.

The archive excludes only non-source/runtime artifacts: nested `.git`, `node_modules`,
generated build output, local profiles, credentials/tokens, and machine-specific state.

For CodexPro-specific details, see:

- after running setup: `.runtime/codexpro/README.md`
- after running setup: `.runtime/codexpro/README_ZH.md`
- after running setup: `.runtime/codexpro/SECURITY.md`
- `integrations/codexpro/WAS_VENDOR.md`

OpenAI's current developer-mode guidance is linked from:
<https://help.openai.com/en/articles/12584461>.

## Dynamic swarm startup

The startup flow keeps a read-only proposal/confirmation boundary, but worker creation is now
elastic by default:

```text
natural-language intent
        |
        v
web_agent_swarm_plan        # read-only proposal
        |
        v
human confirms
        |
        v
web_agent_swarm_start       # no roles => main only
        |
        v
main reads durable graph
        |
        v
elastic_plan -> scale       # initial workers chosen from real parallel pressure
```

A user can still pin the initial topology with explicit roles/instance counts.

After startup, the main orchestrator uses `web_agent_swarm_coordination_plan`
to read durable task state plus the currently active explicit `SwarmGraph`, if one exists.

### Explicit durable SwarmGraph

`SwarmGraph` is a persisted logical execution topology, separate from the physical browser
worker pool. A graph contains mutable nodes/edges and a monotonic `revision`:

```text
Durable SwarmGraph
    |
    +-- task node --------> binds durable task_id
    +-- router/control ---> durable node status/state
    +-- join/agent/tool --> logical orchestration node
    |
    +-- dependency edge --> target waits for source done/skipped
    +-- conditional edge -> target selected/rejected from structured condition
    |
    v
coordination_plan
    |
    +-- dispatch
    +-- wait_graph
    +-- graph_unselected
    |
    v
atomic swarm_task_claim gate
```

Graph mutation is explicit through `swarm_graph_create/get/list/activate/deactivate/update/delete`,
`swarm_graph_replace`, `swarm_graph_node_add/update/remove`, `swarm_graph_edge_add/update/remove`,
and `swarm_graph_validate`. `swarm_graph_preview` compiles a draft without activating it. Mutations
accept `expected_revision`; stale concurrent edits fail with
a revision conflict instead of silently overwriting another agent's topology.

Task-bound graph nodes may override the worker `role`. An activated graph gates both planning and
the atomic task lease path, so a worker cannot manually claim a `wait_graph` or
`graph_unselected` task. Existing valid leases are not preempted by a later graph edit.

Conditional edges never execute arbitrary code. Conditions use a small data DSL with sources
`source_status`, `source_state`, `task_result`, or `shared_status`, and operators
`eq/ne/in/not_in/exists/truthy/falsy/gt/gte/lt/lte`. For example:

```json
{
  "source": "task_result",
  "path": "route",
  "op": "eq",
  "value": "fast"
}
```

If no graph is active, WAS falls back to the existing durable task DAG, so older workflows remain
compatible.

WAS also implements a lightweight equivalent of LangGraph `Send` + reducer semantics:
`swarm_task_fanout` creates independent map shards with different payload state,
multiple instances of the **same role** (for example `implementer`,
`implementer-2`, `implementer-3`) can atomically claim different shards, and
`swarm_task_reduce` aggregates results in stable shard order. Claim/heartbeat/release
leases prevent duplicate execution; retryable failures return to `ready` until
`max_attempts` is exhausted.

`web_agent_swarm_elastic_plan` compares runnable shard pressure with active instance
counts and applies a global worker budget (default 6, while never forcing out capacity that
still owns a live lease). `web_agent_swarm_scale` may add missing roles **or additional
instances of an existing role**. Automatic scale-in uses a 300-second grace window before a
safe excess worker is suspended/closed, preventing 3→4→3→4 tab thrashing. Explicit target
counts bypass that grace because they are direct operator intent.

Tasks can declare dependencies, so downstream work stays behind a durable fan-in
barrier until prerequisites are `done`. Review work routes to `reviewer`. Actionable
recoverable/unknown blockers may route to `debugger`, but `verification_failure`
is a waiting route with zero worker pressure until a concrete ready/active repair or
verification sidecar exists. Browser idle alone never triggers scale-out.

When exactly one runnable/leased primary would otherwise serialize progress, the
watcher materializes at most two bounded safe JIT sidecars and immediately applies
**scale-out only** from the resulting durable pressure, so those tasks become real
browser workers without waiting for a second main-agent scheduling turn. The watcher
never performs scale-in; guarded removal remains on the orchestrator path so a frozen
worker UI cannot stall heartbeat delivery. If multiple primary/map shards are already
runnable, WAS prefers that natural parallel frontier and does not add synthetic
sidecars that would steal the worker budget. The design borrows LangGraph-style graph
semantics without requiring LangGraph at runtime.

Example explicit instance control:

```bash
web-agent-swarm start --roles implementer tester --instances implementer=4 tester=1
web-agent-swarm scale --instances implementer=6
```

The second command treats the count as a target total, so it adds only the missing
`implementer-5` / `implementer-6` instances and preserves the existing workers.

### Durable task steps and checkpoint lineage

Task-level durability is not enough for expensive multi-phase work. WAS therefore exposes
a per-task step cache:

```text
swarm_task_step_fingerprint
        |
        v
swarm_task_step_acquire
   | execute/resume -> run expensive phase -> heartbeat -> complete
   | wait           -> another worker owns identical work
   + reuse          -> consume saved result immediately
```

The automatic fingerprint includes structured inputs, task payload, canonical workspace
identity, canonical epoch, and MVCC resource version/SHA records. A committed source change
therefore invalidates the cached step without relying on an agent to remember to change a
manual cache key.

Coordination checkpoints also form an explicit ancestry chain. Restoring checkpoint `N`
moves the checkpoint head to `N`; the first checkpoint created after resume records `N`
as its parent. `swarm_checkpoint_lineage` exposes that branch for replay/audit tooling.

### Browser lifecycle event ring and watchdogs

The extension emits browser lifecycle events to bridge protocol v4. The bridge keeps a
bounded 512-event sequence ring and exposes cursor-based long polling through `/events`.
The watcher can therefore wake early on extension connect/disconnect, tab create/remove,
navigation/status changes, discard, or freeze while retaining fixed-interval durable polling
as a fallback.

Transport detection is split into small watchdogs rather than one monolithic snapshot rule:

- `BrowserSuspensionWatchdog`
- `InspectErrorWatchdog`
- `GeneratingStallWatchdog`
- `BrowserLifecycleWatchdog`

Watchdogs are observation-only: they never mutate task ownership or publish canonical state.

A bound `unresponsive`/white-screen page now has a deterministic first-line recovery path.
WAS performs one confirmation inspect; if the same bound page is still unresponsive it
reopens the **same conversation URL** in a fresh renderer/tab, retires the stale page,
re-resolves the same conversation ID, and re-inspects it. Task/lease/checkpoint/superstep
ownership is not changed. Only a failed bounded refresh may escalate to conversation
rollover or manual transport recovery. This is intentionally different from a
conversation-length rollover, which creates a new conversation while preserving durable
ownership.

### Deterministic scheduler benchmark

Run:

```bash
web-agent-swarm benchmark
```

or call `web_agent_swarm_benchmark` over MCP. The initial benchmark revision
`was-scheduler-v1` checks seven deterministic episodes: single-primary safe sidecars,
natural four-way fan-out, verification-wait zero pressure, fan-in dependency barriers,
and the global worker budget. Results include per-episode metrics, invariant evidence,
latency, and a simple reward so scheduler changes can be compared instead of judged only
from browser-window appearance.

### MVCC development lanes

Repository-changing work uses an isolated filesystem transaction instead of shared-workspace convention:

```text
task claim
    -> swarm_lane_start(canonical_root, hierarchical resources)
    -> private lane workspace
    -> CodexPro read/write/test only in that lane
    -> swarm_lane_commit
       -> validate base/read/write hashes
       -> publish all claimed write paths or return conflict
    -> task release
```

Claims are scoped by canonical workspace and are hierarchical, so a write claim on `src/autol`
conflicts with another write/read claim below that subtree. Each lane stores a complete base manifest,
precise reads when available, conservative opaque-read state for broad search/commands, and a derived
write set. `scratch` path claims permit experiment/build outputs inside the private lane but never
publish those paths to canonical. Logical resources such as `experiment:drc/17net` can be claimed
to prevent duplicate expensive work even when no source path is involved.

The bundled CodexPro runtime hard-blocks canonical `write/edit/apply_patch/bash/background` while
a live lane protects that workspace; lane mutations outside hierarchical write/scratch claims fail
closed at commit. Successful publication advances per-canonical resource versions/epochs. If a base
or observed read changed, commit records `conflict`, preserves the lane, and never overwrites
canonical; `swarm_lane_fork` creates a new lane from the latest canonical state.

Before touching canonical, commit creates pre-image backups plus a durable recovery journal. If the
process dies with the durable commit still in `committing`, the next LaneManager startup (or
`swarm_lane_recover`) rolls canonical back to the pre-commit state. Missing/invalid recovery
material becomes `recovery_required` and keeps managed canonical access fail-closed.

### Durable interrupts and checkpoints

WAS now mirrors two additional LangGraph execution ideas without taking a LangGraph runtime
dependency:

- `swarm_interrupt_create` persists a human-input pause, optionally attaches it to a task,
  releases that task's worker lease, and routes it as `human_input`.
- `swarm_interrupt_resume` stores the resume value and returns the interrupted task to
  `ready`; the value is durable and is not reconstructed from chat history.
- `swarm_checkpoint_create` records a bounded snapshot of tasks, shared status, supersteps,
  interrupts, all SwarmGraphs, and the active graph ID. Old checkpoints are pruned by a retention bound.
- `swarm_checkpoint_restore` restores that coordination snapshot while preserving browser
  conversation bindings and checkpoint history. It fails closed while any live task lease or
  open/ready superstep exists.

This is analogous to LangGraph's persistent interrupt/resume and checkpoint-based time-travel,
while WAS keeps browser identity outside the restored graph state.

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

## Bundled complete CodexPro

WAS vendors the complete CodexPro source as `integrations/codexpro/codexpro-0.30.0-src.tar.gz`;
there is no separate Python `CodexPro Lite` implementation. Build it with:

```bash
./scripts/setup-codexpro.sh
```

Then launch the same CodexPro CLI through:

```bash
./scripts/start-codexpro.sh <codexpro arguments>
```

The vendored source retains its upstream MIT license and security documentation. See
`integrations/codexpro/WAS_VENDOR.md` for the pinned version/commit and vendoring policy.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The browser bridge is intentionally small and dependency-light.

## License

MIT. See `LICENSE`.
