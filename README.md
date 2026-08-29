# WAS — Web Agent Swarm

**Web Agent Swarm (WAS)** is a local-first multi-agent orchestration system for ChatGPT Web.

It turns multiple ordinary ChatGPT browser tabs into a coordinated agent swarm with:

- one Main Orchestrator;
- multiple specialized Worker agents;
- durable task state outside chat history;
- dependency-aware scheduling;
- dynamic worker scale-out;
- exact browser-tab targeting;
- event-aware wakeups;
- a bundled CodexPro-compatible local development MCP.

WAS does not require a custom ChatGPT frontend.  
Agents run directly inside normal ChatGPT Web tabs in your existing browser session.

---

## Key idea

```text
                 ┌─────────────────────┐
                 │   Main Web Agent    │
                 │    Orchestrator     │
                 └──────────┬──────────┘
                            │
                durable coordination state
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌────────────┐      ┌────────────┐      ┌────────────┐
  │ Worker A   │      │ Worker B   │      │ Worker C   │
  │ researcher │      │ implementer│      │ tester     │
  └────────────┘      └────────────┘      └────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                  Web Agent Swarm MCP
                            │
                  localhost browser bridge
                            │
                    browser extension
                            │
             currently connected browser profile
                            │
                     ChatGPT Web tabs
```

Each worker is an ordinary ChatGPT conversation.

The durable task graph, rather than chat memory, is the coordination truth.

---

# Features

## Browser-hosted multi-agent swarm

WAS can create and coordinate multiple ChatGPT Web conversations as independent agents.

A swarm can contain:

```text
Main Orchestrator
├── researcher
├── implementer
├── tester
├── reviewer
├── debugger
└── documenter
```

Multiple instances of the same role are also supported:

```text
implementer
implementer-2
implementer-3
tester
tester-2
```

The number of workers does not have to remain fixed after startup.

---

## Dynamic scale-out

WAS can begin with a small swarm and add workers later when the durable task graph indicates more parallel capacity is useful.

For example:

```text
Initial swarm:

Main
├── implementer
└── tester
```

Later:

```text
Main
├── implementer
├── implementer-2
├── implementer-3
├── tester
└── reviewer
```

Scale-out is based on runnable task pressure, leases, blocked work, and role demand.

A browser tab being idle is **not** by itself a reason to create another worker.

---

## Durable task graph

WAS keeps coordination state outside individual ChatGPT conversations.

Tasks can contain:

```text
task_id
status
owner / role
dependencies
payload
group_id
claimed_by
lease_expires_at
attempts
max_attempts
retry_after
result
```

Typical task states include:

```text
ready
active
blocked
review
done
failed
```

This lets the Main Agent reason about the actual task graph rather than relying on conversational memory.

---

## Task leases

Before performing concrete work, a worker claims a task.

```text
ready
  │
  ▼
claimed by implementer-2
  │
  ▼
active lease
  │
  ├── heartbeat
  │
  ▼
done / retry / failed
```

Leases reduce accidental duplicate work when multiple workers share the same role.

Expired leases can return work to the runnable frontier.

---

## Dependency-aware scheduling

Tasks may depend on other tasks.

```text
Task A ──────┐
             ├──► Task C
Task B ──────┘
```

`Task C` remains behind a fan-in barrier until both prerequisites are complete.

WAS derives routes such as:

```text
dispatch
wait_dependencies
wait_retry
leased
interrupt
terminal
```

---

## Send-style fan-out and reducer

Large divisible tasks can be split into independent shards.

```text
                 parent task
                     │
                     ▼
             swarm_task_fanout
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       shard 0    shard 1    shard 2
          │          │          │
          ▼          ▼          ▼
 implementer implementer-2 implementer-3
          │          │          │
          └──────────┼──────────┘
                     ▼
             swarm_task_reduce
                     │
                     ▼
               stable result
```

Each shard may carry a different payload.

Reducer output preserves shard ordering.

---

## Atomic supersteps

For coordinated rounds where partial publication would be unsafe, WAS supports a superstep barrier.

```text
Main
 │
 ├── swarm_superstep_begin
 │
 ├──── Worker A executes
 │
 ├──── Worker B executes
 │
 └──── Worker C executes
          │
          ▼
     staged writes
          │
          ▼
   ready_to_commit
          │
          ▼
 swarm_superstep_commit
```

If one participant fails, stale leases are detected, or conflicting writes appear, the round fails closed.

The staged task/status outputs are not partially published.

---

# Current-browser-profile policy

WAS deliberately operates only inside the browser profile that is **currently connected to the local bridge through the WAS extension**.

This is an important design rule.

New Agent tabs are created by the browser extension using:

```javascript
chrome.tabs.create(...)
```

Therefore they remain inside the **same Edge/Chrome profile** where the extension is running and reuse that profile's existing ChatGPT login session.

WAS does **not**:

```text
launch another Edge profile
switch browser profiles
use --profile-directory
copy browser cookies
copy login credentials
create a separate browser session
```

If exactly one browser-profile extension is connected:

```text
extensionClients = 1
→ browser operations allowed
```

If no browser profile is connected:

```text
extensionClients = 0
→ request rejected
```

If multiple profiles connect to the same bridge:

```text
extensionClients > 1
→ HTTP 409
→ WAS refuses to guess which profile is current
```

Use a separate bridge port for each browser profile if multiple independent profiles are required.

---

# Browser capabilities

The bundled browser extension can:

```text
list ChatGPT tabs
open a new ChatGPT tab
inspect a specific tab
detect idle / generating state
read bounded recent visible messages
send a message to an exact tab
track stable ChatGPT conversation URLs
```

WAS binds agents using ChatGPT conversation IDs instead of ephemeral browser tab IDs.

For example:

```text
https://chatgpt.com/c/<conversation-id>
```

Temporary ChatGPT URLs such as:

```text
/c/WEB:...
```

are not treated as stable conversation identities.

---

# Event-aware orchestration

WAS includes a deterministic watcher.

The watcher observes changes in:

```text
worker UI state
durable task state
shared status
```

When meaningful state changes, it wakes the Main Agent.

The watcher itself does not decide which task should run.

```text
state changed
     │
     ▼
   watcher
     │
     ▼
wake Main
     │
     ▼
Main re-reads durable coordination state
     │
     ▼
dispatch / wait / scale / review
```

---

# Bundled CodexPro Lite

WAS includes a clean-room CodexPro-compatible local development MCP server.

It is designed for agents that need to perform concrete work on a local repository.

Start it with:

```bash
python -m web_agent_swarm.codexpro_server
```

or:

```bash
web-agent-swarm-codexpro
```

CodexPro Lite includes core actions such as:

```text
server_config
codexpro_self_test
load_skill

open_current_workspace
open_workspace
inspect_workspace

tree
search
read

write
edit
apply_patch

bash

run_background
background_status
background_cancel

show_changes

read_handoff
wait_for_handoff
handoff_to_agent

compact_context
resume_context
export_pro_context
```

It also exposes the supertool-style interface:

```text
codexpro(action, args)
```

---

## CodexPro Lite security model

The bundled implementation is intentionally constrained.

It:

```text
keeps file operations inside the workspace
blocks common secret/dependency/build directories
uses an allowlist for verification commands
rejects shell chaining and redirection
does not bundle credentials
does not bundle private endpoints
does not bundle machine-specific allowed roots
```

It is a clean-room compatible implementation, not a byte-for-byte copy of any private CodexPro deployment.

---

# Repository structure

```text
WebAgentSwarm/
├── bridge/
│   ├── server.js
│   ├── package.json
│   └── README.md
│
├── browser-extension/
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── options.html
│   ├── options.js
│   └── README.md
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INSTALL.md
│   └── PRIVACY.md
│
├── examples/
│   ├── agent-prompts.md
│   └── mcp-config.example.json
│
├── integrations/
│   └── codexpro/
│
├── scripts/
│   ├── setup.ps1
│   ├── setup.sh
│   ├── start-bridge.ps1
│   ├── start-bridge.sh
│   ├── start-watcher.ps1
│   ├── start-watcher.sh
│   ├── start-codexpro.ps1
│   ├── start-codexpro.sh
│   └── release_check.py
│
├── src/
│   └── web_agent_swarm/
│       ├── bridge.py
│       ├── cli.py
│       ├── state.py
│       ├── roles.py
│       ├── swarm.py
│       ├── watcher.py
│       ├── mcp_server.py
│       ├── codexpro_core.py
│       └── codexpro_server.py
│
├── tests/
│   ├── test_bridge.py
│   ├── test_roles.py
│   ├── test_state.py
│   ├── test_swarm.py
│   └── test_codexpro_lite.py
│
├── AGENTS.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── pyproject.toml
└── README.md
```

---

# Requirements

```text
Edge or Chrome
Node.js 20+
Python 3.10+
A logged-in ChatGPT Web session
```

An MCP-capable host is optional unless you want to expose WAS or CodexPro Lite through MCP.

---

# Installation

## 1. Clone the repository

```bash
git clone https://github.com/yanchun2530/WAS-WebAgentSwarm.git
cd WAS-WebAgentSwarm
```

---

## 2. Install the bridge

```bash
cd bridge
npm install
npm start
```

Default HTTP endpoint:

```text
http://127.0.0.1:8788
```

Default WebSocket endpoint:

```text
ws://127.0.0.1:8788/ws
```

Check:

```text
http://127.0.0.1:8788/health
```

---

## 3. Install the browser extension

### Microsoft Edge

Open:

```text
edge://extensions/
```

Then:

```text
Enable Developer mode
→ Load unpacked
→ select browser-extension/
```

### Google Chrome

Open:

```text
chrome://extensions/
```

Then use the same **Load unpacked** flow.

The extension should connect to:

```text
ws://127.0.0.1:8788/ws
```

The browser profile where you load the extension becomes the profile WAS controls.

---

## 4. Install the Python runtime

From the repository root:

```bash
python -m venv .venv
```

Activate the environment, then:

```bash
python -m pip install -e ".[mcp]"
```

---

# Smoke test

Run:

```bash
web-agent-swarm health
```

Then:

```bash
web-agent-swarm tabs
```

A healthy bridge should report something similar to:

```json
{
  "ok": true,
  "extensionClients": 1
}
```

`tabs` should show the ChatGPT tabs visible to the currently connected browser profile.

---

# Plan a swarm

WAS separates planning from browser mutation.

```bash
web-agent-swarm plan
```

or:

```bash
web-agent-swarm plan --workers 4
```

Example proposal:

```text
Main Orchestrator
+
implementer
tester
reviewer
researcher
```

The swarm should normally be started only after the user accepts the proposed role/count split.

---

# Start a swarm

Example:

```bash
web-agent-swarm start \
  --roles implementer tester reviewer researcher
```

Example with multiple instances of one role:

```bash
web-agent-swarm start \
  --roles implementer tester \
  --instances implementer=4 tester=1
```

This produces:

```text
Main
├── implementer
├── implementer-2
├── implementer-3
├── implementer-4
└── tester
```

---

# Scale an existing swarm

Increase a role to a target total:

```bash
web-agent-swarm scale \
  --instances implementer=6
```

If four implementers already exist, WAS adds only:

```text
implementer-5
implementer-6
```

Existing workers are preserved.

---

# Create durable tasks

Example:

```bash
web-agent-swarm task-create \
  "Implement feature X" \
  --owner implementer
```

Then:

```bash
web-agent-swarm task-create \
  "Verify feature X" \
  --owner tester
```

Workers should claim eligible tasks before concrete execution.

---

# Watcher

Swarm startup normally launches the watcher automatically.

Manual mode:

```bash
web-agent-swarm watch \
  --interval 15 \
  --max-silence 300
```

The watcher wakes the Main Agent when coordination state changes.

It does not independently make task-selection decisions.

---

# MCP

WAS exposes two MCP servers.

## Web Agent Swarm MCP

```bash
python -m web_agent_swarm.mcp_server
```

This exposes browser-agent and swarm coordination tools.

---

## CodexPro Lite MCP

```bash
python -m web_agent_swarm.codexpro_server
```

This exposes local repository development tools.

Example MCP configuration is available at:

```text
examples/mcp-config.example.json
```

---

# Privacy

WAS is designed to remain local-first.

The project does not require storing:

```text
ChatGPT passwords
browser cookies
Microsoft/Google credentials
full browser profiles
private connector tokens
full conversation transcripts
```

The bridge binds to loopback by default:

```text
127.0.0.1
```

Only bounded visible ChatGPT UI state is inspected by the browser extension.

---

# Safety boundaries

WAS does not attempt to bypass:

```text
CAPTCHAs
human verification
anti-bot systems
rate limits
account permissions
platform access controls
```

If ChatGPT requires user interaction, that remains a user-controlled browser action.

---

# Completion semantics

A browser page being idle means only:

```text
ChatGPT is currently not generating text
```

It does **not** prove that an external task succeeded.

For example:

```text
build completed
tests passed
deployment succeeded
experiment finished
```

must be verified using the real external source of truth.

Durable task state and concrete verification evidence determine completion.

---

# Development

Run the Python regression suite:

```bash
python -m unittest discover -s tests -v
```

Check Python syntax:

```bash
python -m compileall -q src tests
```

Check bridge JavaScript:

```bash
node --check bridge/server.js
```

Check extension JavaScript:

```bash
node --check browser-extension/background.js
node --check browser-extension/content.js
```

Before publishing:

```bash
python scripts/release_check.py
```

---

# Design principles

```text
Browser profile is explicit.
Durable state beats chat memory.
Task leases prevent duplicate work.
Browser idle is not completion.
Scale from runnable work pressure.
Fail closed on ambiguous browser profiles.
Keep orchestration local and inspectable.
Do not hide autonomous scheduling behind the browser UI.
```

---

# License

MIT License.

See:

```text
LICENSE
```

---

## Status

Web Agent Swarm currently includes:

```text
Browser extension               ✓
Local HTTP/WebSocket bridge     ✓
Exact ChatGPT tab targeting     ✓
Current-profile-only control    ✓
Stable conversation binding     ✓
Main + Worker orchestration     ✓
Multiple workers per role       ✓
Dynamic scale-out               ✓
Durable task graph              ✓
Task leases / retries           ✓
Fan-out / reducer               ✓
Dependency barriers             ✓
Atomic supersteps               ✓
Event-aware watcher             ✓
Web Agent Swarm MCP             ✓
Bundled CodexPro Lite MCP       ✓
Privacy/release checks          ✓
```

WAS is intended as a practical foundation for coordinating multiple browser-hosted AI agents while keeping browser identity, task state, and local development capabilities explicit and inspectable.
