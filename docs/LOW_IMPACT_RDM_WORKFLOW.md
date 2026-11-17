# Low-impact Web Agent Swarm + Remote Desktop Commander workflow

## Accepted path

**Use the existing conversation as the decision-making manager, Remote Desktop
Commander as the computer-operation executor, and Web Agent Swarm's durable task
store to track work.** This requires no additional AI model, paid API, Codex
invocation, browser automation, background daemon, or startup service.

The manager uses the connected Remote Desktop Commander tool to run short-lived
Python commands. These share the existing Web Agent Swarm state; durable task
claims and completion protect against accidental cross-worker completion.
This is **operator-directed task execution**, not a fully autonomous six-agent
ChatGPT website swarm. Reasoning in this chat still consumes applicable ChatGPT
usage; nothing here creates free or unlimited model inference.

## Use on Galactus

Run from the root of the local development checkout. These examples are
for a trusted local operator and do not depend on a particular user directory.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
$env:WEB_SWARM_STATE_DIR = (Join-Path $env:TEMP "was-rdm-local-queue")
python -m web_agent_swarm.cli task-create "Check repository health" --owner tester
python -m web_agent_swarm.cli task-list --status ready
python -m web_agent_swarm.cli task-claim TASK_ID rdc-operator --lease-seconds 900
# Run the authorized computer task through Remote Desktop Commander.
python -m web_agent_swarm.cli task-release TASK_ID rdc-operator --status done --summary "Verified"
python -m web_agent_swarm.cli task-list --status done
```

Replace `TASK_ID` with the ID returned by task-create. The task claim must
precede execution. Release a task as `done` only after its result is known.
Use `task-heartbeat TASK_ID rdc-operator` for a long-running task, and
`task-release ... --status failed --no-retry --error "reason"` when a task
cannot safely continue. Never release another worker's task, and never retry
an uncertain external action without inspecting whether it happened.

`WEB_SWARM_STATE_DIR` selects the durable task queue. The example uses a
separate directory so it cannot modify the normal swarm state. Leave this
variable unchanged between commands for one job; choosing a different
directory creates a separate queue. The directory persists until the operator
explicitly removes it.

## Verification and boundaries

On Galactus, an isolated queue received a real task; Remote Desktop Commander
claimed it, ran Python syntax and Git diff checks in the development repository,
marked it done, and read it back in a separate CLI process. Task attempts: **1**.
The test's temporary queue was removed after completion.

The CLI also has local tests for claimed ownership, heartbeat, completion,
dependency blocking, invalid JSON, and terminal failure. No persistent service
was started and no existing browser profile was touched.

**Not yet verified:** multi-ChatGPT-session communication, browser response
collection, autonomous work, or six simultaneous workers. OpenAI's
consumer Terms prohibit automatically/programmatically extracting output from
ChatGPT. An external trigger alone does not exempt extraction. Do not use the
browser extension for live output collection unless an authorized mechanism
is established. The optional paid API PoC remains archived development work;
it is **not** part of this accepted zero-additional-cost path.

## Low-overhead operating decision

Start with **one manager (this ChatGPT conversation) + one RDM computer executor**,
then expand on evidence of real need. Use direct deterministic tool calls rather
than adding a classifier model, scheduler service, browser polling, or six idle
ChatGPT sessions. This removes additional inference charges and minimizes idle
local resource use; it does not make ChatGPT reasoning free of plan limits.
