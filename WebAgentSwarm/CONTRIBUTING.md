# Contributing

1. Keep changes generic and domain-independent.
2. Add or update tests for orchestration/state logic.
3. Run:
   - Python unit tests;
   - Python compile checks;
   - Node syntax checks for bridge and extension scripts.
4. Review browser permissions when extension behavior changes.
5. Do not commit runtime state, dependency directories, credentials, or machine-specific paths.
6. Keep the proposal/confirmation boundary intact unless a direct-start request is explicit.
