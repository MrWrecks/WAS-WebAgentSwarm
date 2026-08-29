# Bundled CodexPro Lite

Web Agent Swarm ships with a clean-room CodexPro-compatible MCP server for local
development work:

```text
python -m web_agent_swarm.codexpro_server
```

Set `CODEXPRO_ROOT` to the workspace that the agent is allowed to operate on.

## Included action surface

The bundled server exposes the same core names used by the swarm workflow:

- `codexpro(action, args)` supertool and `server_config`;
- `codexpro_self_test` and workspace skill loading;
- `open_current_workspace`, `open_workspace`, `inspect_workspace`;
- `tree`, `search`, `read`;
- `write`, `edit`, `apply_patch`;
- allowlisted `bash`;
- `run_background`, `background_status`, `background_cancel`;
- `show_changes`;
- `read_handoff`, `wait_for_handoff`, `handoff_to_agent`;
- `compact_context`, `resume_context`, `export_pro_context`.

The canonical list is also recorded in `capabilities.json`.

## Security model

This open-source implementation is deliberately narrower than a private workstation
connector:

- all file mutation is constrained to the opened workspace;
- common secret/dependency/build directories are blocked;
- shell chaining and redirection are rejected;
- only a small verification-oriented command allowlist is accepted;
- no machine-specific allowed roots, authentication material, private endpoints, or user
  configuration are bundled.

## Compatibility note

This is a clean-room compatible implementation, not a byte-for-byte copy of any private
CodexPro deployment. The core workflow and action names are present, but advanced
proprietary internals such as deep symbol/relationship analysis may be simplified.

## Recommended main-agent policy

1. Use Web Agent Swarm tools for browser-agent planning, startup, inspection, and messaging.
2. Use CodexPro Lite for concrete repository inspection, edits, tests, and background work.
3. Review changes and concrete verification before declaring a task complete.
4. Keep secrets and full private transcripts out of shared swarm state.
