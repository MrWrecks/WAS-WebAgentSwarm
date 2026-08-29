"""Event-aware watcher that wakes the main Web Agent on state changes."""

import argparse
import hashlib
import json
import time
from typing import Any

from .swarm import SwarmRuntime


TICK = (
    "[WEB_SWARM_TICK] Shared worker/task state changed. "
    "Call web_agent_swarm_snapshot and web_agent_swarm_coordination_plan. Treat task "
    "state as graph routing: respect dependencies, retry waits, active leases and blocked "
    "interrupts. For divisible work, use Send-style swarm_task_fanout shards and let exact "
    "worker instances claim different shards; use swarm_task_reduce after the group completes. "
    "If an open/ready superstep exists, inspect its barrier: never publish staged writes early, "
    "commit only when ready_to_commit, and leave aborted/conflict rounds uncommitted. "
    "Call web_agent_swarm_elastic_plan when durable pressure changes. Apply "
    "web_agent_swarm_scale for recommended add_instances or remove_instances; scale-in must "
    "respect task leases, supersteps and generating tabs. Never scale merely because a browser "
    "tab is idle, never duplicate a valid lease, and send only a real durable next action."
)


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in sorted(value.items())
            if key not in {"updated_at"}
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _transport_alerts(snapshot: dict):
    alerts = []
    for item in snapshot.get("agents", []):
        if item.get("name") == "main":
            continue
        binding = item.get("binding") or {}
        if isinstance(binding, dict) and binding.get("suspended"):
            continue
        ui = item.get("ui") or {}
        error = str(item.get("error") or "").strip()
        if error:
            alerts.append({
                "name": item.get("name"),
                "kind": "inspect_error",
                "error": error,
            })
        if ui.get("stuckGenerating"):
            alerts.append({
                "name": item.get("name"),
                "kind": "stuck_generating",
            })
    return alerts


def _digest(snapshot: dict) -> str:
    payload = {
        "tasks": snapshot.get("tasks", []),
        "shared_status": snapshot.get("shared_status", {}),
        "supersteps": snapshot.get("supersteps", []),
        "transport_alerts": _transport_alerts(snapshot),
    }
    raw = json.dumps(
        _stable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _has_unresolved_work(snapshot: dict) -> bool:
    now = time.time()
    for task in snapshot.get("tasks", []):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").strip().lower()
        if status in {"ready", "review"}:
            retry_after = float(task.get("retry_after") or 0.0)
            if retry_after <= now:
                return True
        if status == "active":
            return True
        if status == "blocked":
            kind = str(task.get("blocker_kind") or "unknown_blocker")
            if kind not in {"dependency_wait", "external_wait"}:
                return True

    for step in snapshot.get("supersteps", []):
        if isinstance(step, dict) and step.get("status") in {"open", "ready"}:
            return True
    return False


def _main_idle(snapshot: dict) -> bool:
    for item in snapshot.get("agents", []):
        if item.get("name") != "main":
            continue
        ui = item.get("ui") or {}
        return bool(item.get("open")) and not bool(ui.get("generating"))
    return False


def run(interval: float = 300.0, max_silence: float = 300.0, once: bool = False) -> int:
    runtime = SwarmRuntime()
    last_digest = None
    last_sent = 0.0
    pending = True

    while True:
        snapshot = runtime.snapshot(tail=2)
        digest = _digest(snapshot)
        if last_digest is None or digest != last_digest:
            pending = True

        silence_due = (
            max_silence > 0
            and _has_unresolved_work(snapshot)
            and time.time() - last_sent >= max_silence
        )
        if (pending or silence_due) and _main_idle(snapshot):
            result = runtime.send("main", TICK)
            if result.get("ok"):
                last_sent = time.time()
                last_digest = digest
                pending = False
            elif once:
                return 2

        if once:
            return 0

        if last_digest is None:
            last_digest = digest
        time.sleep(max(2.0, float(interval)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--max-silence", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    return run(args.interval, args.max_silence, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
