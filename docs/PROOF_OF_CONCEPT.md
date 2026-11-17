# Triggered manager–worker proof of concept

## Result

**PASS on Galactus in an isolated test.** This exercises the existing
Web Agent Swarm coordinator and durable state with two **synthetic** sessions.
It is a viability test, not live ChatGPT acceptance.

Run from the repository root, after `npm ci --ignore-scripts` in `bridge`:

```powershell
python scripts/poc_manager_worker.py
```

## What actually happens

1. A separate local process POSTs a JSON event to a temporary loopback HTTP webhook.
2. The real `BridgeClient`, Node WebSocket bridge, `SwarmRuntime`, and `DurableStore`
   route that event to a simulated manager session.
3. A simulated worker receives a task instruction over the real bridge.
4. The worker **claims a durable lease** in a separate Python process, then
   crashes before acknowledging the instruction.
5. The coordinator records the delivery as **uncertain**, without asserting failure
   or blindly resending it.
6. The worker transport restarts; a new `SwarmRuntime` reads the same durable state.
7. The worker resumes its original lease, completes the deterministic task,
   and sends a completion report to the simulated manager.
8. The run asserts `attempts == 1`, `status == done`, and the expected output.

The script uses a random localhost port, a temporary token, and a temporary
state directory. It terminates the test bridge and workers and deletes its
temporary files. No persistent services or real browsing profiles are used.

## Scope of the evidence

**Demonstrated:** externally triggered handoff into a real local bridge,
manager-to-worker routing, OS-process interruption, unknown acknowledgment
handling, persisted task ownership, restart recovery, and a single completed task.

**Not demonstrated:** model autonomy, ChatGPT website content access or extraction,
live browser-session orchestration, actual ChatGPT message/response behavior,
long-running worker recovery, or six-worker concurrency.

The source currently reads ChatGPT page output programmatically; an outside
trigger does not itself remove the platform-terms constraint. Resolve a
supported/authorized integration before live deployment.

## Next decision

The local coordination architecture is viable for a synthetic triggered task.
Do not spend effort scaling to six live ChatGPT workers until the intended
triggered input/output path has an authorized mechanism and one real
manager–worker cycle passes.

## Optional supported OpenAI API route

A separate, opt-in proof of concept tests **actual model-generated** manager
routing, worker processing, and manager acceptance through the supported
Responses API. This does not convert ordinary ChatGPT web tabs into API sessions.

Without a key (default), run a local mock with **zero external calls or cost**:

```powershell
python scripts/poc_responses_api.py
```

For a real-model round-trip, first configure an OpenAI Platform project and
provide `OPENAI_API_KEY` to the process without committing or printing it.
**Only after approving API billing**, run:

```powershell
python scripts/poc_responses_api.py --live
```

`--live` makes **three billable Responses API calls**, using `gpt-6-luna`
by default; the API client sets `store: false`. The script creates an isolated
temporary durable store, records command transactions, and never retries an
unknown outcome. OpenAI API billing is separate from a ChatGPT subscription.

The local mock and corresponding unit tests pass, but **no paid/live API call
has been made** and no API key has been created or stored by this script.
