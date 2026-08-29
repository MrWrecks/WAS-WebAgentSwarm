# CodexPro-style integration

Web Agent Swarm and a local development MCP solve different problems.

- **Web Agent Swarm**: browser-agent lifecycle, inspection, messaging, shared task/status
  state, and main-agent wakeups.
- **CodexPro-style MCP**: local workspace inspection, source edits, verification commands,
  background work, handoff files, and bounded context management.

Run them as separate MCP servers connected to the same agent host.

## Capability profile

`capabilities.json` records the generic actions this project expects from a
CodexPro-compatible connector: workspace open/inspection; tree/search/read;
write/edit/patch; allowlisted verification commands; background run/status/cancel;
change review; handoff; and context compact/resume/export.

No connector binary, token, endpoint, machine path, or private configuration is bundled here.

## Recommended main-agent policy

1. Use Web Agent Swarm tools to plan/start/observe the browser swarm.
2. Use the local development MCP for concrete repository work.
3. Keep durable shared task/status facts short and factual.
4. Do not place secrets or full private transcripts into shared swarm state.
5. A worker saying “done” is not enough; require the relevant concrete verification result.
