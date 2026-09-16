# Repository agent guidance

This repository is intentionally generic and publishable.

- Keep browser automation isolated in `browser-extension/`.
- Keep transport logic isolated in `bridge/`.
- Keep orchestration/state logic in `src/web_agent_swarm/`.
- Keep the bundled clean-room CodexPro-compatible runtime generic: no private connector code, machine-specific roots, credentials, or domain-specific project state.
- Do not add machine-specific paths, credentials, browser profiles, cookies, or private transcripts.
- Do not embed domain-specific project state into the generic swarm runtime.
- The read-only planning phase must never open tabs or send messages.
- Browser idle is not external completion truth.
- Conversation-length exhaustion is transport lifecycle debt, never durable task completion: replace the exact main/worker conversation, preserve worker identity and durable leases, bind the fresh conversation before closing the exhausted old tab, and retry any failed old-tab close as explicit transport cleanup debt.
- Prefer small structured state and explicit verification evidence.
- Run unit tests and syntax checks before claiming completion.
