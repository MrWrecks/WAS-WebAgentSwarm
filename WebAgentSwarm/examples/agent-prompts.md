# Example operator prompts

These are examples, not exact trigger phrases.

## Proposal first

> Open a small web-agent swarm for this task.

Expected behavior: read durable tasks/status, propose a worker count and role split, and
ask for confirmation before opening new tabs.

## Count preference

> Start a swarm. I probably want four workers; propose the best roles first.

## Direct start

> Start the swarm now with implementer, tester, and reviewer. No second confirmation.

## Main-agent wake loop

When the watcher injects `[WEB_SWARM_TICK]`, the main agent should re-read the swarm
snapshot and only send a next instruction when the durable state justifies it.
