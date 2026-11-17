# Web Agent Swarm — implementation status

Updated: 2026-11-16. This is an implementation record, not live deployment certification.

## Verified
- Upstream: `yanchun2530/WAS-WebAgentSwarm` at `cf83412` (2026-09-17), MIT license.
- Account-owned public fork: `MrWrecks/WAS-WebAgentSwarm`; isolated development branch `fix/extension-connection-scope`, with fork `main` unchanged.
- Development machine: Git 2.55, Node 24.15, npm 11.12, Python 3.14. CodexPro not installed.
- Baseline: 95 Python tests. Current: **120 Python tests pass**, plus JavaScript syntax and Git diff checks.
- Reconnect alarm, valid extension host permissions, reduced tab scope, and non-destructive passive tab observation.
- Windows process liveness probes are non-destructive. Durable writer locks are OS-held; live writers cannot be mistaken for expired locks.
- Extension command IDs require explicit tab IDs; SHA-256 fingerprints bind commands to exact tab/message/expected-URL tuples.
- The extension writes an uncertain command reservation before browser submission. Concurrent duplicates are serialized; mismatched duplicates fail closed. Cache hydration/storage failures prevent new submissions.
- The bridge binds pending results to the exact extension connection receiving a request; a second extension cannot forge the response. Disconnects and lost responses report uncertain open/close/send outcomes.
- Bound Python sends pin the expected ChatGPT conversation URL, and the content script checks it before editing and immediately before clicking Send.
- Close operations can pin an expected URL. Rollover debt cleanup checks original conversation identity and refuses to close reused IDs.
- Uncertain rollover open/bootstrap operations and refresh replacement outcomes create durable holds rather than deleting possible live work or making blind retries.
- Synthetic tests: `npm run test:smoke`, `npm run test:delivery`, `npm run test:recipient`; isolated headless Edge MV3 load and bridge-restart reconnect: `npm run test:extension`. These did not visit ChatGPT or access browser conversations.
- Externally triggered localhost manager/worker PoC passed using the actual bridge, `SwarmRuntime`, and `DurableStore`: worker crashes after task claim but before ACK, restart resumes the same lease, one task attempt finishes, and manager receives completion. See `docs/PROOF_OF_CONCEPT.md`; sessions were synthetic.
- Optional Responses API manager/worker PoC passes against a local mock server (three exchanges, completed durable task, transport-loss stop). The live API mode is credential-gated and billable; it has **not** been executed.
- Locked `ws@8.21.3` was installed only within the development copy, with package scripts disabled. No extension installed in a personal profile, no persistent service started, and no unrelated files changed.

## Not yet demonstrated
- A platform-authorized ChatGPT browser output integration, live manager-worker send and result collection, or a real session restart.
- Safe six-worker operation under concurrent live ChatGPT sessions.
- Full browser-bridge security, token-storage, and Windows scheduled-task installation reviews.
- Reconciliation workflow for `rollover_uncertain` / `refresh_uncertain` durable holds.
- Long-horizon idempotence: the extension result cache is bounded (256 entries). These tests prove replay safety only while a command remains in that cache, not perpetual exactly-once delivery.

## Material boundaries
- OpenAI's Terms and supported integration mechanisms must be reviewed before automating extraction of ChatGPT website output. Synthetic/local tests are not authorization for live use.
- An acknowledged browser send does not prove the worker completed its task.
- An ambiguous consequential operation remains *unknown*. Inspect its actual state before any retry, cleanup, or marker removal.
- OS-held state-lock files may remain on disk; do not run old and new writer-lock implementations simultaneously during a deployment transition.
- Existing Windows task installers can register/start recurring tasks with `-Force`; review running tasks and obtain authorization before executing them.
- CodexPro, remote command exposure, persistent startup, and live ChatGPT automation remain out of scope for this bounded development pass.

## Next acceptance gates
1. Publish/verify this tested development branch on the fork, leaving `main` unchanged.
2. Resolve platform-compliant chat automation, or choose a supported non-website integration.
3. Finish credential/permission/startup review before any persistent installation.
4. Validate one manager and worker with an authorized mechanism, including uncertain-result reconciliation.
5. Scale only after reliable completion and recovery at the smaller scope.

Rollback: local backup branches preserve prior commits; no forced history rewrite or changes to the fork's default branch.
