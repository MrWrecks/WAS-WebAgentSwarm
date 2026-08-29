# Repository agent guidance

This repository is intentionally generic and publishable.

- Keep browser automation isolated in `browser-extension/`.
- Keep transport logic isolated in `bridge/`.
- Keep orchestration/state logic in `src/web_agent_swarm/`.
- Do not add machine-specific paths, credentials, browser profiles, cookies, or private transcripts.
- Do not embed domain-specific project state into the generic swarm runtime.
- The read-only planning phase must never open tabs or send messages.
- Browser idle is not external completion truth.
- Prefer small structured state and explicit verification evidence.
- Run unit tests and syntax checks before claiming completion.
