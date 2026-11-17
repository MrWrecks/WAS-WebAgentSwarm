# Web Agent Swarm — implementation status

Updated: 2026-11-16. Distinguish local verification from live-browser acceptance.

## Verified
- Upstream: `yanchun2530/WAS-WebAgentSwarm` at `cf83412` (2026-09-17); MIT license.
- Fork: `MrWrecks/WAS-WebAgentSwarm`, account-owned, public, with admin and push permission.
- Windows development clone is isolated. Local `origin` is the fork; `upstream` is the original.
- Git 2.55, Node 24.15, npm 11.12, Python 3.14 available; no CodexPro installed.
- Baseline: 95 unit tests passed. Current: 107 Python unit tests pass; JS syntax and Git diff checks pass.
- Fixed reconnect alarm constant and unsupported WebSocket host-permission patterns.
- Scoped extension tab listings and automatic tab protection to reduce interference with unrelated browsing.
- Added OS-held cross-process state locks; a live old lock cannot be stolen and a crashed writer frees its lock.
- Replaced dangerous Windows `os.kill(pid, 0)` liveness check with non-destructive OS process-handle query; verified live child remains alive and exited child is detected.
- Installed only locked `ws@8.21.3` to the isolated bridge directory with npm scripts disabled; a synthetic loopback smoke test passed authentication, WebSocket hello, tab forwarding, send acknowledgement and command-ID enforcement.
- Pre-persist browser send attempts with a tracked command ID; serialize storage writes; report unknown outcomes and stop blind coordinator retries.
- All source modifications are confined to the separate development clone. No browser automation or persistent startup was enabled.

## Not yet demonstrated
- Browser extension installation and one manager/worker live round-trip.
- Full message acknowledgment, worker result collection and safe replay after real browser restarts.
- Six-worker concurrency, recovery, recipient isolation, and end-to-end reliability.
- Full security review of browser bridge, token storage, and Windows installation scripts.
- Operational installation or persistent Scheduled Task startup.
- A supported/compliant method for automating ordinary ChatGPT web session output extraction.

## Risks and approval gates
- Platform terms may restrict automated collection of ChatGPT output. Verify a supported integration before live deployment.
- Browser send acknowledgments are not proof of worker task completion.
- A crash during browser submission now retains an uncertain send record. This prevents blind resends, but requires reconciliation with the intended conversation; delivery cannot be established from a timeout alone.
- OS-held lock files intentionally remain on disk. Do not run old and new state-lock implementations concurrently during upgrade.
- Windows installers can register and start persistent tasks with `-Force`; review existing tasks and obtain appropriate approval first.
- CodexPro and any public local-command endpoint remain uninstalled and unexposed.

## Next steps
1. Maintain the separate development branch on the fork; do not modify its default branch.
2. Validate actual extension manifest loading and service-worker behavior in an isolated test browser, without automating ChatGPT output.
3. Complete Windows startup, browser permission, and bridge security reviews before any live installation.
4. Test one manager and one worker only after a supported, authorized browser communication method is established.
5. Scale to six workers only after reliability gates pass.

Rollback: a local Git branch records each change; the fork's default branch remains unchanged. Do not rewrite unrelated work.
