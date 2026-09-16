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
snapshot plus `web_agent_swarm_coordination_plan`. It should fan out independent
frontier work, respect dependency barriers, treat blocked work as an interrupt, and only
send a next instruction when durable state justifies it.

If the graph exposes executable work beyond current instance capacity, the main agent
should call `web_agent_swarm_elastic_plan` and may use
`web_agent_swarm_scale` to add a missing role or another instance of the same role.
For divisible work it may call `swarm_task_fanout`, instruct each exact worker instance
to claim a different shard, then call `swarm_task_reduce` after the group completes.

When a parallel round must publish all worker outputs atomically, the main agent should
create `swarm_superstep_begin(participants=[...])` first and include that `superstep_id`
in each worker instruction. Workers claim their tasks with the same `superstep_id`, keep
leases alive, and call `swarm_superstep_stage` instead of ordinary task release. The main
agent commits only after `ready_to_commit=true`; any participant failure must call
`swarm_superstep_fail`, leaving every staged output unpublished.

Browser idle alone is not a scale signal.
